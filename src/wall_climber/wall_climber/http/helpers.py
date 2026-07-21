from __future__ import annotations

import asyncio
import base64
import contextlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import functools
import hashlib
import json
import os

os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Optional

import numpy
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response

try:
    import websockets
except ImportError:
    websockets = None

try:
    import rclpy
    from ament_index_python.packages import get_package_share_directory
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String
    from wall_climber_interfaces.msg import BoardPoint, PathPrimitive, PrimitivePathPlan
except ImportError as exc:
    rclpy = None
    _ROS_IMPORT_ERROR = exc

    def get_package_share_directory(_package_name: str) -> str:
        raise RuntimeError('ROS 2 Python dependencies are required for package share lookup.') from _ROS_IMPORT_ERROR

    class SingleThreadedExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError('ROS 2 Python dependencies are required for WebBackendNode.') from _ROS_IMPORT_ERROR

    class Node:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError('ROS 2 Python dependencies are required for WebBackendNode.') from _ROS_IMPORT_ERROR

    class ReliabilityPolicy:
        RELIABLE = 'reliable'

    class DurabilityPolicy:
        TRANSIENT_LOCAL = 'transient_local'

    class QoSProfile:
        def __init__(self, *, depth: int, reliability: Any, durability: Any) -> None:
            self.depth = depth
            self.reliability = reliability
            self.durability = durability

    class String:
        data: str

    class BoardPoint:
        x: float
        y: float

    class PathPrimitive:
        pass

    class PrimitivePathPlan:
        pass
else:
    _ROS_IMPORT_ERROR = None

from wall_climber import _http_helpers as _http
from wall_climber._debug_snapshots import DebugSnapshotStore
from wall_climber._ttl_cache import TTLCache
from wall_climber import canonical_adapters as _canonical_adapters
from wall_climber.canonical_adapters import (
    SamplingPolicy,
    canonical_plan_debug_payload,
    canonical_plan_diagnostics,
    canonical_plan_to_draw_strokes,
    canonical_plan_to_legacy_strokes,
    canonical_plan_to_primitive_path_plan,
    canonical_plan_to_sampled_paths,
)
from wall_climber.canonical_builders import (
    draw_strokes_to_canonical_plan,
    text_glyph_outlines_to_canonical_plan,
)
from wall_climber.canonical_optimizer import (
    CanonicalOptimizationPolicy,
    optimize_canonical_plan,
)
from wall_climber.canonical_path import (
    ArcSegment,
    CanonicalCommand,
    CanonicalPathPlan,
    CubicBezier,
    LineSegment,
    PenDown,
    PenUp,
    QuadraticBezier,
    TravelMove,
)
from wall_climber.canonical_tiny_details import expand_tiny_details_in_canonical_plan
from wall_climber.canonical_ops import (
    cleanup_canonical_plan,
    default_image_placement,
    normalize_placement,
    place_canonical_plan_on_board,
    place_grouped_text_on_board,
    stroke_stats,
)
from wall_climber.ingestion.svg import vectorize_svg
from wall_climber.ingestion.text import (
    TextGlyphOutline,
    normalize_text_plan_input,
    vectorize_text_grouped,
)
from wall_climber.ingestion.upload_routing import (
    UploadedVectorFile,
    classify_uploaded_vector_file,
)
from wall_climber.image_pipeline.adapters import drawing_path_plan_to_canonical
from wall_climber.image_pipeline.curve_fit import drawing_path_plan_to_smooth_canonical
from wall_climber.image_pipeline.potrace_vector import (
    is_potrace_available,
    vectorize_potrace_image_to_plan,
)
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.image_pipeline.ai_preprocess import (
    AnilinesModelError,
    InformativeModelError,
    SwinirModelError,
    anilines_weights_cached,
    informative_weights_cached,
    preprocess_image_to_lineart,
    swinir_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.preview_encode import (
    decode_lineart_png,
    rasterize_board_strokes_board_frame,
    rasterize_board_strokes_thumbnail,
    rasterize_strokes_on_lineart_frame,
)
from wall_climber.image_pipeline.ai_preprocess.types import PreprocessSettings
from wall_climber.image_pipeline.ai_preprocess.vram_manager import cuda_available
from wall_climber.image_pipeline.types import DrawingPathPlan
from wall_climber.optimizers import vpype_optimizer
from wall_climber.runtime_topics import (
    ACTIVE_MODE_TOPIC,
    CABLE_EXECUTOR_STATUS_TOPIC,
    CABLE_SUPERVISOR_STATUS_TOPIC,
    EXECUTION_DIAGNOSTICS_TOPIC,
    EXECUTION_CANCEL_TOPIC,
    MANUAL_PEN_MODE_TOPIC,
    MODE_DRAW,
    MODE_OFF,
    MODE_TEXT,
    PEN_MODE_AUTO,
    PEN_MODE_DOWN,
    PEN_MODE_UP,
    PRIMITIVE_PATH_PLAN_TOPIC,
    VALID_MODES,
    VALID_MANUAL_PEN_MODES,
)
from wall_climber.shared_config import load_shared_config
from wall_climber.vector_pipeline import VectorPlacement
from wall_climber import voice_stream_whisper_vad as _voice_stream

from wall_climber.http.runtime import (
    BackendRuntime,
    LineartCacheEntry,
    PreviewCacheEntry,
    _MAX_DRAW_PLAN_BYTES,
    _MAX_DRAW_STROKES,
    _MAX_POINTS_PER_STROKE,
    _MAX_SVG_BYTES,
    _MAX_TEXT_BYTES,
    _MAX_TEXT_CHARS,
    _MAX_TOTAL_POINTS,
    _MAX_UPLOAD_BYTES,
    _MAX_VECTOR_REQUEST_BYTES,
    _SEGMENT_EPS_M,
    _SKETCH_DRAW_MAX_CANONICAL_COMMANDS,
    _SKETCH_DRAW_MAX_PRIMITIVES,
    _SKETCH_DRAW_MAX_PRIMITIVE_DESCRIPTOR_BYTES,
    _SKETCH_PREVIEW_MAX_POINTS,
    _VALID_TEXT_COLUMNS,
    _run_cpu_bound,
)
def _require_json_object(raw: Any, name: str) -> dict[str, Any]:
    return _http.require_json_object(raw, name)


async def _load_json_request(
    request: Request,
    *,
    name: str,
    max_bytes: int,
) -> dict[str, Any]:
    return await _http.load_json_request(request, name=name, max_bytes=max_bytes)


def _reject_extra_fields(payload: dict[str, Any], allowed: set[str], name: str) -> None:
    _http.reject_extra_fields(payload, allowed, name)


def _validate_text_value(value: Any, field_name: str) -> str:
    return _http.validate_text_value(
        value,
        field_name,
        max_chars=_MAX_TEXT_CHARS,
        max_bytes=_MAX_TEXT_BYTES,
    )


def _validate_text_request(raw: Any) -> str:
    payload = _require_json_object(raw, 'text request')
    _reject_extra_fields(payload, {'text'}, 'text request')
    return _validate_text_value(payload.get('text'), 'text request.text')


def _coerce_float(
    value: Any,
    *,
    field_name: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    return _http.coerce_float(
        value, field_name=field_name, minimum=minimum, maximum=maximum,
    )


def _coerce_int(
    value: Any,
    *,
    field_name: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    return _http.coerce_int(
        value, field_name=field_name, minimum=minimum, maximum=maximum,
    )


def _coerce_bool(value: Any, *, field_name: str, default: bool | None = None) -> bool:
    return _http.coerce_bool(value, field_name=field_name, default=default)


def _validate_upload_id(raw_upload_id: Any) -> str:
    return _http.validate_upload_id(raw_upload_id)


def _validate_preview_id(raw_preview_id: Any) -> str:
    return _http.validate_preview_id(raw_preview_id)


def _stable_float(value: float, *, precision: int = 7) -> float:
    return _http.stable_float(value, precision=precision)


def _stable_point_payload(point: tuple[float, float]) -> list[float]:
    return _http.stable_point_payload(point)


def _stable_payload(value: Any) -> Any:
    return _http.stable_payload(value)


def _stable_hash(value: Any) -> str:
    return _http.stable_hash(value)


def settings_hash(settings: dict[str, Any]) -> str:
    return _http.settings_hash(settings)


def _canonical_command_payload(command: CanonicalCommand) -> dict[str, Any]:
    if isinstance(command, PenUp):
        return {'type': 'pen_up'}
    if isinstance(command, PenDown):
        return {'type': 'pen_down'}
    if isinstance(command, TravelMove):
        return {
            'type': 'travel',
            'start': _stable_point_payload(command.start),
            'end': _stable_point_payload(command.end),
        }
    if isinstance(command, LineSegment):
        return {
            'type': 'line',
            'start': _stable_point_payload(command.start),
            'end': _stable_point_payload(command.end),
        }
    if isinstance(command, ArcSegment):
        return {
            'type': 'arc',
            'center': _stable_point_payload(command.center),
            'radius': _stable_float(command.radius),
            'start_angle_rad': _stable_float(command.start_angle_rad),
            'sweep_angle_rad': _stable_float(command.sweep_angle_rad),
        }
    if isinstance(command, QuadraticBezier):
        return {
            'type': 'quadratic',
            'start': _stable_point_payload(command.start),
            'control': _stable_point_payload(command.control),
            'end': _stable_point_payload(command.end),
        }
    if isinstance(command, CubicBezier):
        return {
            'type': 'cubic',
            'start': _stable_point_payload(command.start),
            'control1': _stable_point_payload(command.control1),
            'control2': _stable_point_payload(command.control2),
            'end': _stable_point_payload(command.end),
        }
    raise ValueError(f'Unsupported canonical command {type(command)!r}.')


def canonical_plan_stable_payload(plan: CanonicalPathPlan) -> dict[str, Any]:
    return {
        'frame': str(plan.frame),
        'theta_ref': _stable_float(plan.theta_ref),
        'commands': [
            _canonical_command_payload(command)
            for command in plan.commands
        ],
    }


def canonical_plan_hash(plan: CanonicalPathPlan) -> str:
    payload = canonical_plan_stable_payload(plan)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(content: bytes | str | dict[str, Any] | None) -> str | None:
    if content is None:
        return None
    if isinstance(content, bytes):
        payload = content
    elif isinstance(content, str):
        payload = content.encode('utf-8')
    else:
        payload = json.dumps(content, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _lineart_preprocess_cache_key(content: bytes, settings: PreprocessSettings) -> str:
    return _content_hash(
        {
            'source': _content_hash(content),
            'mode': settings.mode,
            'raw_print': settings.raw_print,
            'target_resolution': settings.target_resolution,
            'force_solid_black_lines': settings.force_solid_black_lines,
            'photo_lineart_model': settings.photo_lineart_model,
            'nano_banana_prompt': settings.nano_banana_prompt,
        }
    ) or ''


def _lineart_cache_hit_preview(entry: LineartCacheEntry) -> dict[str, Any]:
    preview = dict(entry.preprocess_preview)
    preview['reused_from_cache'] = True
    timing = dict(preview.get('timing_ms') or {})
    preview['timing_ms'] = {key: 0.0 for key in timing}
    return preview


def _normalize_text_column(raw: Any) -> str:
    value = str(raw or 'full').strip().lower()
    if value not in _VALID_TEXT_COLUMNS:
        raise ValueError(f"text_column must be one of {sorted(_VALID_TEXT_COLUMNS)}")
    return value


def _text_column_x_bounds(
    safe_bounds: dict[str, float],
    column: str,
) -> tuple[float, float]:
    if column == 'full':
        return float(safe_bounds['x_min']), float(safe_bounds['x_max'])
    width = float(safe_bounds['x_max']) - float(safe_bounds['x_min'])
    third = width / 3.0
    x_min = float(safe_bounds['x_min'])
    if column == 'left':
        return x_min, x_min + third
    if column == 'center':
        return x_min + third, x_min + (2.0 * third)
    return x_min + (2.0 * third), float(safe_bounds['x_max'])


def _resolve_text_start_placement(
    raw_placement: Any,
    *,
    request_name: str,
    writable_bounds: dict[str, float],
    safe_bounds: dict[str, float],
    text_layout_defaults,
    default_x_override: float | None = None,
    default_y_override: float | None = None,
    use_continuation_cursor: bool = False,
) -> VectorPlacement:
    min_x = safe_bounds['x_min'] + float(text_layout_defaults.left_margin)

    # Keep left protection, but do not shrink the right side with a text right-margin.
    max_x = safe_bounds['x_max']

    min_y = safe_bounds['y_min'] + float(text_layout_defaults.top_margin)
    max_y = safe_bounds['y_max'] - float(text_layout_defaults.bottom_margin)

    if default_x_override is not None and min_x <= default_x_override <= max_x:
        # Continuation cursor: caller wants the next text to continue on the
        # same line at the X where the previous text ended (with a small gap
        # already baked in by the publish handler).
        default_x = float(default_x_override)
    else:
        default_x = min_x
    if default_y_override is not None and min_y <= default_y_override <= max_y:
        # Continuation cursor: caller wants the next text to continue at the
        # baseline Y of the previous text (or wrapped to the next line if it
        # would overflow the writable width).
        default_y = float(default_y_override)
    else:
        default_y = min_y
    default_scale = 1.0

    if raw_placement is None:
        return VectorPlacement(x=default_x, y=default_y, scale=default_scale)

    if not isinstance(raw_placement, dict):
        raise HTTPException(
            status_code=422,
            detail=f'{request_name}.placement must be an object with x, y, and scale',
        )

    _reject_extra_fields(raw_placement, {'x', 'y', 'scale'}, f'{request_name}.placement')

    if use_continuation_cursor:
        # The caller has opted in to cursor-driven placement. The UI still
        # sends its (X, Y) defaults from the placement panel, but those are
        # implicit defaults — not an explicit user override — so we ignore
        # them and use the runtime cursor instead. The scale is still taken
        # from the request because that is a real user setting.
        scale = _coerce_float(
            raw_placement.get('scale', default_scale),
            field_name=f'{request_name}.placement.scale',
            minimum=0.05,
            maximum=10.0,
        )
        return VectorPlacement(x=default_x, y=default_y, scale=scale)

    x = _coerce_float(
        raw_placement.get('x', default_x),
        field_name=f'{request_name}.placement.x',
    )
    y = _coerce_float(
        raw_placement.get('y', default_y),
        field_name=f'{request_name}.placement.y',
    )
    scale = _coerce_float(
        raw_placement.get('scale', default_scale),
        field_name=f'{request_name}.placement.scale',
        minimum=0.05,
        maximum=10.0,
    )

    x = min(max(x, min_x), max_x)
    y = min(max(y, min_y), max_y)
    return VectorPlacement(x=x, y=y, scale=scale)


def _grouped_text_bounds(glyphs: tuple[TextGlyphOutline, ...]) -> dict[str, float]:
    if not glyphs:
        raise ValueError('text produced no drawable glyphs')
    x_min = min(glyph.bbox.x_min for glyph in glyphs)
    x_max = max(glyph.bbox.x_max for glyph in glyphs)
    y_min = min(glyph.bbox.y_min for glyph in glyphs)
    y_max = max(glyph.bbox.y_max for glyph in glyphs)
    return {
        'x_min': x_min,
        'x_max': x_max,
        'y_min': y_min,
        'y_max': y_max,
        'width': x_max - x_min,
        'height': y_max - y_min,
    }


def _first_line_grouped_bounds(
    glyphs: tuple[TextGlyphOutline, ...],
) -> dict[str, float]:
    if not glyphs:
        raise ValueError('text produced no drawable glyphs')
    first_line_index = min(glyph.line_index for glyph in glyphs)
    return _grouped_text_bounds(
        tuple(glyph for glyph in glyphs if glyph.line_index == first_line_index)
    )


def _continuation_placement_center_y(
    grouped_source: tuple[TextGlyphOutline, ...],
    *,
    row_top_y: float,
    glyph_scale_m: float,
    grouped_fit_scale: float,
    writable_bounds: dict[str, float],
    fit_padding: float,
) -> float:
    """Row-top Y for line 0 after place_strokes_on_board full-block centering."""
    from wall_climber.vector_pipeline import _strokes_bounds

    flat_strokes = tuple(stroke for glyph in grouped_source for stroke in glyph.strokes)
    if not flat_strokes:
        raise ValueError('text produced no drawable strokes')
    stroke_bounds = _strokes_bounds(flat_strokes)
    board_width = writable_bounds['x_max'] - writable_bounds['x_min']
    board_height = writable_bounds['y_max'] - writable_bounds['y_min']
    stroke_fit_scale = min(
        (board_width * fit_padding) / max(stroke_bounds.width, 1.0e-9),
        (board_height * fit_padding) / max(stroke_bounds.height, 1.0e-9),
    )
    final_scale = stroke_fit_scale * (glyph_scale_m / grouped_fit_scale)
    stroke_center_y = 0.5 * (stroke_bounds.y_min + stroke_bounds.y_max)
    line0_y_min = _first_line_grouped_bounds(grouped_source)['y_min']
    return float(row_top_y) + (stroke_center_y - line0_y_min) * final_scale


def _max_partial_column_bottom_y(runtime: BackendRuntime) -> float | None:
    max_y: float | None = None
    for column in ('left', 'center', 'right'):
        bottom = runtime.get_text_column_bottom_y(column)
        if bottom is None:
            continue
        max_y = float(bottom) if max_y is None else max(max_y, float(bottom))
    return max_y


def _full_wrap_overlap_floor_y(
    runtime: BackendRuntime,
    line_gap_m: float,
) -> float | None:
    partial_max = _max_partial_column_bottom_y(runtime)
    if partial_max is None:
        return None
    return float(partial_max) + float(line_gap_m)


def _is_cross_mode_text_seed(
    runtime: BackendRuntime,
    text_column: str,
    *,
    has_column_bottom: bool,
) -> bool:
    last = runtime.get_last_text_draw_column()
    if has_column_bottom:
        if text_column != 'full' and last == 'full':
            return True
        return False
    if text_column == 'full':
        return last is not None and last in {'left', 'center', 'right'}
    if text_column not in {'left', 'center', 'right'}:
        return False
    if runtime.get_text_full_width_bottom_y() is None:
        return False
    if last is None:
        return True
    if last == 'full':
        return True
    return last != text_column


def _cross_mode_seed_anchor_y(
    runtime: BackendRuntime,
    text_column: str,
    *,
    column_seed_gap_m: float,
) -> float | None:
    full_bottom = runtime.get_text_full_width_bottom_y()
    if text_column == 'full':
        partial_max = _max_partial_column_bottom_y(runtime)
        anchors = [value for value in (full_bottom, partial_max) if value is not None]
        if not anchors:
            return None
        return max(anchors) + float(column_seed_gap_m)
    if full_bottom is None:
        return None
    return float(full_bottom) + float(column_seed_gap_m)


def _text_ink_floor_y(
    runtime: BackendRuntime,
    text_column: str,
    line_gap_m: float,
) -> float | None:
    if text_column == 'full':
        global_bottom = runtime.get_text_global_bottom_y()
        if global_bottom is None:
            return None
        return float(global_bottom) + float(line_gap_m)
    column_bottom = runtime.get_text_column_bottom_y(text_column)
    if column_bottom is not None:
        return float(column_bottom) + float(line_gap_m)
    full_bottom = runtime.get_text_full_width_bottom_y()
    if full_bottom is None:
        return None
    return float(full_bottom) + float(line_gap_m)


def _bump_row_top_below_ink_floor(
    proposed_y: float,
    floor_y: float | None,
) -> float:
    if floor_y is None:
        return float(proposed_y)
    return max(float(proposed_y), float(floor_y))


def _bump_row_top_below_global_ink(
    runtime: BackendRuntime,
    proposed_y: float,
    line_gap_m: float,
) -> float:
    return _bump_row_top_below_ink_floor(
        proposed_y,
        _text_ink_floor_y(runtime, 'full', line_gap_m),
    )


def _wrapped_lines_global_y_shift_amount(
    grouped_source: tuple[TextGlyphOutline, ...],
    *,
    placement_center_y: float,
    glyph_scale_m: float,
    grouped_fit_scale: float,
    writable_bounds: dict[str, float],
    fit_padding: float,
    floor_y: float | None,
) -> float:
    if floor_y is None:
        return 0.0
    line_indices = sorted({glyph.line_index for glyph in grouped_source})
    if len(line_indices) <= 1:
        return 0.0
    from wall_climber.vector_pipeline import _strokes_bounds

    flat_strokes = tuple(stroke for glyph in grouped_source for stroke in glyph.strokes)
    stroke_bounds = _strokes_bounds(flat_strokes)
    board_width = writable_bounds['x_max'] - writable_bounds['x_min']
    board_height = writable_bounds['y_max'] - writable_bounds['y_min']
    stroke_fit_scale = min(
        (board_width * fit_padding) / max(stroke_bounds.width, 1.0e-9),
        (board_height * fit_padding) / max(stroke_bounds.height, 1.0e-9),
    )
    final_scale = stroke_fit_scale * (glyph_scale_m / grouped_fit_scale)
    stroke_center_y = 0.5 * (stroke_bounds.y_min + stroke_bounds.y_max)
    required_shift = 0.0
    for line_index in line_indices[1:]:
        line_glyphs = [glyph for glyph in grouped_source if glyph.line_index == line_index]
        if not line_glyphs:
            continue
        line_y_min_em = min(glyph.bbox.y_min for glyph in line_glyphs)
        board_y_min = placement_center_y + (line_y_min_em - stroke_center_y) * final_scale
        if board_y_min < floor_y:
            required_shift = max(required_shift, float(floor_y) - board_y_min)
    return required_shift


def _shift_placed_glyph_lines_y(
    placed_groups: tuple[TextGlyphOutline, ...],
    *,
    line_index: int,
    delta_y: float,
) -> tuple[TextGlyphOutline, ...]:
    if abs(delta_y) <= 1.0e-12:
        return placed_groups
    from wall_climber.vector_pipeline import _strokes_bounds

    shifted: list[TextGlyphOutline] = []
    for glyph in placed_groups:
        if glyph.line_index != line_index:
            shifted.append(glyph)
            continue
        glyph_strokes = tuple(
            tuple((point[0], point[1] + delta_y) for point in stroke)
            for stroke in glyph.strokes
        )
        shifted.append(
            TextGlyphOutline(
                line_index=glyph.line_index,
                word_index=glyph.word_index,
                text=glyph.text,
                strokes=glyph_strokes,
                bbox=_strokes_bounds(glyph_strokes),
                advance=glyph.advance,
                source=glyph.source,
            )
        )
    return tuple(shifted)


def _text_bottom_y_from_groups(
    placed_groups: tuple[TextGlyphOutline, ...],
) -> float | None:
    if not placed_groups:
        return None
    return max(glyph.bbox.y_max for glyph in placed_groups)


def _note_text_column_bottom_from_groups(
    runtime: BackendRuntime,
    placed_groups: tuple[TextGlyphOutline, ...],
    *,
    text_column: str,
) -> None:
    bottom_y = _text_bottom_y_from_groups(placed_groups)
    if bottom_y is None:
        return
    runtime.note_text_global_bottom_y(bottom_y)
    if text_column == 'full':
        runtime.note_text_full_width_bottom_y(bottom_y)
    elif text_column in {'left', 'center', 'right'}:
        runtime.note_text_column_bottom_y(text_column, bottom_y)


def _normalize_text_font_source(font_source: Any) -> str:
    normalized = str(font_source or 'relief_singleline').strip().lower()
    if normalized not in {'relief_singleline', 'hershey_sans_1', 'dejavu_sans'}:
        raise ValueError(
            'font_source must be one of ["relief_singleline", "hershey_sans_1", "dejavu_sans"]'
        )
    return normalized


def _expand_preview_bounds(
    bounds: dict[str, float],
    *,
    pad_m: float,
) -> dict[str, float]:
    pad = max(1.0e-4, float(pad_m))
    x_min = float(bounds['x_min']) - pad
    x_max = float(bounds['x_max']) + pad
    y_min = float(bounds['y_min']) - pad
    y_max = float(bounds['y_max']) + pad
    return {
        'x_min': x_min,
        'x_max': x_max,
        'y_min': y_min,
        'y_max': y_max,
        'width': x_max - x_min,
        'height': y_max - y_min,
    }


def _preview_sampling_policy(shared_config) -> SamplingPolicy:
    draw_defaults = shared_config.draw_execution
    return SamplingPolicy(
        curve_tolerance_m=max(0.006, float(draw_defaults.draw_resample_step_m) * 1.75),
        draw_step_m=max(0.006, float(draw_defaults.draw_resample_step_m) * 1.75),
        travel_step_m=max(0.006, float(draw_defaults.travel_resample_step_m) * 1.35),
        max_heading_delta_rad=0.28,
        label='preview',
    )


def _runtime_sampling_policy(shared_config) -> SamplingPolicy:
    draw_defaults = shared_config.draw_execution
    return SamplingPolicy(
        curve_tolerance_m=max(1.0e-4, float(draw_defaults.draw_resample_step_m)),
        draw_step_m=max(1.0e-4, float(draw_defaults.draw_resample_step_m)),
        travel_step_m=max(1.0e-4, float(draw_defaults.travel_resample_step_m)),
        max_heading_delta_rad=0.16,
        label='runtime',
    )


def _draw_optimization_policy(
    shared_config,
    *,
    label: str,
    reorder_units: bool,
    fit_arcs: bool = False,
    enable_hatch_ordering: bool = False,
    cluster_units: bool = False,
) -> CanonicalOptimizationPolicy:
    draw_defaults = shared_config.draw_execution
    tiny = max(2.0e-4, float(draw_defaults.draw_path_simplify_tolerance_m) * 2.0)
    return CanonicalOptimizationPolicy(
        label=label,
        reorder_units=bool(reorder_units),
        cluster_units=bool(cluster_units),
        merge_travel_moves=True,
        fit_arcs=bool(fit_arcs),
        enable_hatch_ordering=bool(enable_hatch_ordering),
        tiny_primitive_m=tiny,
        arc_fit_tolerance_m=max(tiny, float(draw_defaults.draw_path_simplify_tolerance_m) * 2.5),
        merge_distance_tolerance_m=max(1.0e-5, tiny * 0.25),
        dedupe_precision_m=max(1.0e-5, tiny * 0.5),
        cluster_cell_size_m=0.26,
    )


def _sketch_draw_optimization_policy(shared_config) -> CanonicalOptimizationPolicy:
    draw_defaults = shared_config.draw_execution
    tiny = max(2.0e-4, float(draw_defaults.draw_path_simplify_tolerance_m) * 2.0)
    return CanonicalOptimizationPolicy(
        label='sketch_centerline_draw',
        merge_collinear_lines=False,
        reorder_units=True,
        cluster_units=True,
        merge_travel_moves=True,
        remove_duplicate_units=True,
        prune_tiny_primitives=False,
        fit_arcs=False,
        enable_hatch_ordering=False,
        tiny_primitive_m=tiny,
        merge_distance_tolerance_m=max(1.0e-5, tiny * 0.25),
        dedupe_precision_m=max(1.0e-5, tiny * 0.5),
        cluster_cell_size_m=0.26,
    )


def _sampling_validation_step_m(policy: SamplingPolicy) -> float:
    candidates = [
        float(policy.curve_tolerance_m),
        float(policy.draw_step_m) if policy.draw_step_m is not None else None,
        float(policy.travel_step_m) if policy.travel_step_m is not None else None,
    ]
    steps = [max(1.0e-4, value) for value in candidates if value is not None]
    return min(steps) if steps else 0.01


def _validated_runtime_sampled_paths(
    canonical_plan: CanonicalPathPlan,
    *,
    writable_bounds: dict[str, float],
    shared_config,
    sampling_policy: SamplingPolicy,
):
    segments = canonical_plan_to_sampled_paths(
        canonical_plan,
        sampling_policy=sampling_policy,
    )
    if not segments:
        raise HTTPException(status_code=422, detail='execution payload has no drawable segments')
    for index, segment in enumerate(segments):
        if len(segment.points) < 2:
            raise HTTPException(status_code=422, detail=f'draw segment[{index}] is degenerate')
        for point in segment.points:
            if not (
                writable_bounds['x_min'] <= point[0] <= writable_bounds['x_max']
                and writable_bounds['y_min'] <= point[1] <= writable_bounds['y_max']
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f'draw segment[{index}] extends outside carriage-safe writable bounds',
                )
        if _interpolated_outside_safe_workspace_count(
            (segment.points,),
            shared_config,
            step_m=_sampling_validation_step_m(sampling_policy),
        ) != 0:
            raise HTTPException(
                status_code=422,
                detail=f'draw segment[{index}] exits the configured safe cable workspace',
            )
    return segments


def _preview_payload_from_strokes(
    placed_strokes: tuple[tuple[tuple[float, float], ...], ...],
    placement_result,
    *,
    outside_safe_points: int,
    normalized_plan: dict[str, Any] | None = None,
    canonical_plan: CanonicalPathPlan | None = None,
    preview_sampling_policy: SamplingPolicy | None = None,
    runtime_sampling_policy: SamplingPolicy | None = None,
) -> dict[str, Any]:
    if canonical_plan is None:
        preview_strokes = [
            [[float(point[0]), float(point[1])] for point in stroke]
            for stroke in placed_strokes
        ]
        diagnostics = None
    else:
        preview_policy = preview_sampling_policy or SamplingPolicy(label='preview')
        runtime_policy = runtime_sampling_policy or preview_policy
        preview_draw_strokes = canonical_plan_to_draw_strokes(
            canonical_plan,
            sampling_policy=preview_policy,
        )
        preview_strokes = [
            [[float(point[0]), float(point[1])] for point in stroke]
            for stroke in preview_draw_strokes
        ]
        diagnostics = canonical_plan_diagnostics(
            canonical_plan,
            preview_sampling_policy=preview_policy,
            runtime_sampling_policy=runtime_policy,
        )
    stats = stroke_stats(
        tuple(
            tuple((float(point[0]), float(point[1])) for point in stroke)
            for stroke in (
                tuple(tuple(tuple(point) for point in stroke) for stroke in placed_strokes)
                if canonical_plan is None else preview_draw_strokes
            )
        )
    )

    # Add preview-only padding so letters touching the text bounds
    # do not appear visually clipped in the browser.
    preview_pad_m = max(0.003, float(placement_result.final_scale) * 0.10)
    padded_bounds = _expand_preview_bounds(stats['bounds'], pad_m=preview_pad_m)

    can_commit = placement_result.outside_points == 0 and outside_safe_points == 0
    validation_error = None
    if placement_result.outside_points != 0:
        validation_error = 'geometry exceeds carriage-safe writable bounds'
    elif outside_safe_points != 0:
        validation_error = 'geometry exits the configured safe cable workspace'

    return {
        'strokes': preview_strokes,
        'stroke_count': stats['stroke_count'],
        'point_count': stats['point_count'],
        'bounds': padded_bounds,
        'placement': {
            'x': placement_result.placement.x,
            'y': placement_result.placement.y,
            'scale': placement_result.placement.scale,
            'base_fit_scale': placement_result.base_fit_scale,
            'final_scale': placement_result.final_scale,
        },
        'outside_points': placement_result.outside_points,
        'outside_safe_points': outside_safe_points,
        'can_commit': can_commit,
        'validation_error': validation_error,
        'normalized_plan': normalized_plan,
        'diagnostics': diagnostics,
    }


def _interpolated_outside_safe_workspace_count(
    polylines: tuple[tuple[tuple[float, float], ...], ...],
    shared_config,
    *,
    step_m: float = 0.01,
) -> int:
    outside = 0
    step = max(1.0e-4, float(step_m))
    for stroke in polylines:
        if len(stroke) < 2:
            continue
        for index in range(1, len(stroke)):
            start = stroke[index - 1]
            end = stroke[index]
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = float(numpy.hypot(dx, dy))
            subdivisions = max(1, int(numpy.ceil(length / step)))
            for sample_index in range(subdivisions + 1):
                t = sample_index / subdivisions
                x = start[0] + dx * t
                y = start[1] + dy * t
                if not shared_config.point_in_safe_workspace(x, y):
                    outside += 1
    return outside


def _build_primitive_path_plan_message(
    canonical_plan: CanonicalPathPlan,
) -> PrimitivePathPlan:
    descriptor = canonical_plan_to_primitive_path_plan(canonical_plan)
    return _primitive_path_plan_message_from_descriptor(descriptor)


def _primitive_path_plan_message_from_descriptor(
    descriptor: dict[str, Any],
) -> PrimitivePathPlan:
    def board_point_from_payload(point: dict[str, Any]):
        try:
            return BoardPoint(
                x=float(point['x']),
                y=float(point['y']),
            )
        except TypeError:
            board_point = BoardPoint()
            board_point.x = float(point['x'])
            board_point.y = float(point['y'])
            return board_point

    plan = PrimitivePathPlan()
    plan.frame = str(descriptor['frame'])
    plan.theta_ref = float(descriptor['theta_ref'])
    if not hasattr(plan, 'primitives'):
        plan.primitives = []
    type_codes = {
        'PEN_UP': getattr(PathPrimitive, 'PEN_UP', 1),
        'PEN_DOWN': getattr(PathPrimitive, 'PEN_DOWN', 2),
        'TRAVEL_MOVE': getattr(PathPrimitive, 'TRAVEL_MOVE', 3),
        'LINE_SEGMENT': getattr(PathPrimitive, 'LINE_SEGMENT', 4),
        'ARC_SEGMENT': getattr(PathPrimitive, 'ARC_SEGMENT', 5),
        'QUADRATIC_BEZIER': getattr(PathPrimitive, 'QUADRATIC_BEZIER', 6),
        'CUBIC_BEZIER': getattr(PathPrimitive, 'CUBIC_BEZIER', 7),
    }
    for primitive_descriptor in descriptor['primitives']:
        primitive = PathPrimitive()
        primitive.type = int(type_codes[str(primitive_descriptor['type'])])
        for field_name in ('start', 'end', 'control1', 'control2', 'center'):
            point = primitive_descriptor[field_name]
            setattr(
                primitive,
                field_name,
                board_point_from_payload(point),
            )
        primitive.radius = float(primitive_descriptor['radius'])
        primitive.start_angle_rad = float(primitive_descriptor['start_angle_rad'])
        primitive.sweep_angle_rad = float(primitive_descriptor['sweep_angle_rad'])
        primitive.clockwise = bool(primitive_descriptor['clockwise'])
        primitive.pen_down = bool(primitive_descriptor['pen_down'])
        plan.primitives.append(primitive)
    return plan


def _build_execution_transport_message(
    canonical_plan: CanonicalPathPlan,
    *,
    writable_bounds: dict[str, float],
    shared_config,
    sampling_policy: SamplingPolicy,
) -> PrimitivePathPlan:
    _validated_runtime_sampled_paths(
        canonical_plan,
        writable_bounds=writable_bounds,
        shared_config=shared_config,
        sampling_policy=sampling_policy,
    )
    return _build_primitive_path_plan_message(canonical_plan)


def _sanitize_points(raw_points: Any, stroke_index: int) -> list[tuple[float, float]]:
    if not isinstance(raw_points, list):
        raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].points must be a list')
    if len(raw_points) > _MAX_POINTS_PER_STROKE:
        raise HTTPException(status_code=413, detail=f'stroke[{stroke_index}] exceeds the maximum points per stroke')
    points: list[tuple[float, float]] = []
    for point_index, point in enumerate(raw_points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].points[{point_index}] must be [x, y]')
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].points[{point_index}] must be numeric')
        if not (numpy.isfinite(x) and numpy.isfinite(y)):
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].points[{point_index}] must be finite')
        current = (x, y)
        if points and abs(points[-1][0] - current[0]) <= _SEGMENT_EPS_M and abs(points[-1][1] - current[1]) <= _SEGMENT_EPS_M:
            continue
        points.append(current)
    sanitized: list[tuple[float, float]] = []
    for point in points:
        if not sanitized:
            sanitized.append(point)
            continue
        previous = sanitized[-1]
        if abs(previous[0] - point[0]) <= _SEGMENT_EPS_M and abs(previous[1] - point[1]) <= _SEGMENT_EPS_M:
            continue
        sanitized.append(point)
    return sanitized


def _normalize_stroke_payload(raw: Any, writable_bounds: dict[str, float]) -> str:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail='stroke payload body must be a JSON object')
    _reject_extra_fields(raw, {'frame', 'strokes'}, 'stroke payload')
    if raw.get('frame') != 'board':
        raise HTTPException(status_code=422, detail='stroke payload.frame must be exactly "board"')
    strokes = raw.get('strokes')
    if not isinstance(strokes, list) or not strokes:
        raise HTTPException(status_code=422, detail='stroke payload.strokes must be a non-empty list')
    if len(strokes) > _MAX_DRAW_STROKES:
        raise HTTPException(status_code=413, detail='stroke payload exceeds the maximum number of strokes')

    normalized_strokes: list[dict[str, Any]] = []
    total_points = 0
    for stroke_index, stroke in enumerate(strokes):
        if not isinstance(stroke, dict):
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}] must be an object')
        _reject_extra_fields(stroke, {'type', 'draw', 'points'}, f'stroke[{stroke_index}]')
        stroke_type = stroke.get('type')
        if stroke_type not in ('line', 'polyline'):
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].type must be "line" or "polyline"')
        draw_flag = stroke.get('draw')
        if not isinstance(draw_flag, bool):
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}].draw must be boolean')
        points = _sanitize_points(stroke.get('points'), stroke_index)
        if len(points) < 2:
            raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}] is degenerate after sanitization')
        total_points += len(points)
        if total_points > _MAX_TOTAL_POINTS:
            raise HTTPException(status_code=413, detail='stroke payload exceeds the maximum total point budget')
        for point in points:
            if not (
                writable_bounds['x_min'] <= point[0] <= writable_bounds['x_max']
                and writable_bounds['y_min'] <= point[1] <= writable_bounds['y_max']
            ):
                raise HTTPException(status_code=422, detail=f'stroke[{stroke_index}] contains points outside writable board bounds')
        normalized_strokes.append(
            {
                'type': 'line' if len(points) == 2 else 'polyline',
                'draw': draw_flag,
                'points': [[point[0], point[1]] for point in points],
            }
        )
    payload = {'frame': 'board', 'strokes': normalized_strokes}
    encoded = json.dumps(payload, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > _MAX_DRAW_PLAN_BYTES:
        raise HTTPException(status_code=413, detail='stroke payload exceeds the maximum allowed payload size')
    return encoded


def _validate_upload(upload: UploadFile, content: bytes) -> UploadedVectorFile:
    if not content:
        raise HTTPException(status_code=422, detail='uploaded file is empty')
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='uploaded file exceeds the maximum allowed size')
    try:
        return classify_uploaded_vector_file(upload.filename, upload.content_type, content)
    except ValueError as exc:
        detail = str(exc)
        status_code = 415 if 'unsupported upload content type' in detail else 422
        raise HTTPException(status_code=status_code, detail=detail)


def _validate_sketch_upload(upload: UploadFile, content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=422, detail='uploaded file is empty')
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='uploaded file exceeds the maximum allowed size')

    suffix = Path(upload.filename or '').suffix.lower()
    normalized_type = str(upload.content_type or '').split(';', 1)[0].strip().lower()
    if suffix not in {'.png', '.jpg', '.jpeg', '.webp'} and normalized_type not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise HTTPException(status_code=415, detail='sketch preview accepts PNG, JPG, or WebP uploads only')


def _image_value_error_to_http(exc: ValueError) -> HTTPException:
    detail = str(exc)
    lowered = detail.lower()
    if (
        'could not be decoded' in lowered
        or 'failed to decode' in lowered
        or 'image payload is empty' in lowered
    ):
        return HTTPException(
            status_code=400,
            detail='Unable to decode uploaded image. Please upload a valid PNG, JPG, or WebP image.',
        )
    return HTTPException(status_code=422, detail=detail)


def _drawing_plan_bounds(plan) -> dict[str, float]:
    points = [point for stroke in plan.strokes for point in stroke.points]
    if not points:
        raise ValueError('DrawingPathPlan has no points.')
    x_min = min(float(point.x) for point in points)
    x_max = max(float(point.x) for point in points)
    y_min = min(float(point.y) for point in points)
    y_max = max(float(point.y) for point in points)
    return {
        'x_min': x_min,
        'x_max': x_max,
        'y_min': y_min,
        'y_max': y_max,
        'width': x_max - x_min,
        'height': y_max - y_min,
    }


def _downsample_points_for_preview(points, *, stride: int) -> list:
    selected = list(points[::stride])
    if selected and selected[-1] != points[-1]:
        selected.append(points[-1])
    if len(selected) < 2 and len(points) >= 2:
        selected = [points[0], points[-1]]
    return selected


def _sketch_preview_strokes(plan, *, max_points: int | None = None) -> dict[str, Any]:
    from wall_climber import web_server

    max_points = web_server._SKETCH_PREVIEW_MAX_POINTS if max_points is None else max_points
    max_points = max(2, int(max_points))
    original_point_count = sum(len(stroke.points) for stroke in plan.strokes)
    stride = max(1, int(numpy.ceil(original_point_count / float(max_points)))) if original_point_count else 1
    preview_strokes: list[list[list[float]]] = []
    returned_point_count = 0

    for stroke in plan.strokes:
        selected = _downsample_points_for_preview(tuple(stroke.points), stride=stride)
        remaining = max_points - returned_point_count
        if remaining <= 1:
            break
        if len(selected) > remaining:
            selected = selected[:remaining]
        if len(selected) < 2:
            continue
        preview_strokes.append([[float(point.x), float(point.y)] for point in selected])
        returned_point_count += len(selected)

    return {
        'strokes': preview_strokes,
        'max_points': max_points,
        'returned_point_count': returned_point_count,
        'original_point_count': original_point_count,
        'truncated': returned_point_count < original_point_count,
    }


def _svg_number(value: float) -> str:
    return f'{float(value):.6g}'


def _preview_stroke_width_m(
    board_width_m: float,
    board_height_m: float,
    *,
    pen_tip_radius_m: float | None = None,
) -> float:
    if pen_tip_radius_m is not None and pen_tip_radius_m > 0.0:
        return 2.0 * float(pen_tip_radius_m)
    return max(float(board_width_m), float(board_height_m)) / 360.0


def _sketch_preview_svg(
    preview_strokes: list[list[list[float]]],
    *,
    board_width_m: float,
    board_height_m: float,
    pen_tip_radius_m: float | None = None,
) -> str:
    # Match the simulated pen's physical line width so that the preview
    # the user sees is what the robot will actually draw. Pen lines are
    # drawn in board-space metres (the SVG viewBox is in metres), so the
    # stroke width below is 2 × tip_radius in metres. A 3 mm tip
    # therefore renders as a 6 mm-wide stroke in the preview, which is
    # exactly what the carriage will leave on the board.
    stroke_width = _preview_stroke_width_m(
        board_width_m,
        board_height_m,
        pen_tip_radius_m=pen_tip_radius_m,
    )
    polylines: list[str] = []
    for stroke in preview_strokes:
        if len(stroke) < 2:
            continue
        points = ' '.join(
            f'{_svg_number(point[0])},{_svg_number(point[1])}'
            for point in stroke
        )
        polylines.append(
            f'<polyline points="{points}" fill="none" stroke="#111827" '
            f'stroke-width="{_svg_number(stroke_width)}" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    body = ''.join(polylines)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_svg_number(board_width_m)} {_svg_number(board_height_m)}" '
        f'width="{_svg_number(board_width_m)}" height="{_svg_number(board_height_m)}" '
        'role="img" aria-label="Sketch centerline preview">'
        f'<rect x="0" y="0" width="{_svg_number(board_width_m)}" '
        f'height="{_svg_number(board_height_m)}" fill="white"/>'
        f'{body}</svg>'
    )


def _command_start(command: CanonicalCommand) -> tuple[float, float] | None:
    if isinstance(command, LineSegment):
        return command.start
    if isinstance(command, QuadraticBezier):
        return command.start
    if isinstance(command, CubicBezier):
        return command.start
    return None


def _smooth_sketch_preview_svg(
    canonical_plan: CanonicalPathPlan,
    *,
    board_width_m: float,
    board_height_m: float,
    pen_tip_radius_m: float | None = None,
) -> str:
    # Match the simulated pen's physical line width — see
    # _sketch_preview_svg() for the rationale.
    stroke_width = _preview_stroke_width_m(
        board_width_m,
        board_height_m,
        pen_tip_radius_m=pen_tip_radius_m,
    )
    paths: list[str] = []
    current: list[str] = []
    pen_down = False

    def flush_current() -> None:
        if current:
            paths.append(
                '<path d="'
                + ' '.join(current)
                + f'" fill="none" stroke="#111827" stroke-width="{_svg_number(stroke_width)}" '
                + 'stroke-linecap="round" stroke-linejoin="round"/>'
            )
            current.clear()

    for command in canonical_plan.commands:
        if isinstance(command, PenDown):
            flush_current()
            pen_down = True
            continue
        if isinstance(command, PenUp):
            flush_current()
            pen_down = False
            continue
        if isinstance(command, TravelMove):
            continue
        if not pen_down:
            continue
        start = _command_start(command)
        if start is None:
            continue
        if not current:
            current.append(f'M {_svg_number(start[0])} {_svg_number(start[1])}')
        if isinstance(command, LineSegment):
            current.append(f'L {_svg_number(command.end[0])} {_svg_number(command.end[1])}')
        elif isinstance(command, QuadraticBezier):
            current.append(
                f'Q {_svg_number(command.control[0])} {_svg_number(command.control[1])} '
                f'{_svg_number(command.end[0])} {_svg_number(command.end[1])}'
            )
        elif isinstance(command, CubicBezier):
            current.append(
                f'C {_svg_number(command.control1[0])} {_svg_number(command.control1[1])} '
                f'{_svg_number(command.control2[0])} {_svg_number(command.control2[1])} '
                f'{_svg_number(command.end[0])} {_svg_number(command.end[1])}'
            )
    flush_current()
    body = ''.join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_svg_number(board_width_m)} {_svg_number(board_height_m)}" '
        f'width="{_svg_number(board_width_m)}" height="{_svg_number(board_height_m)}" '
        'role="img" aria-label="Sketch centerline smooth curve preview">'
        f'<rect x="0" y="0" width="{_svg_number(board_width_m)}" '
        f'height="{_svg_number(board_height_m)}" fill="white"/>'
        f'{body}</svg>'
    )


def _sampled_paths_bounds(sampled_paths) -> dict[str, float] | None:
    points = [
        point
        for sampled in sampled_paths
        if sampled.draw
        for point in sampled.points
    ]
    if not points:
        return None
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    return {
        'x_min': min(x_values),
        'x_max': max(x_values),
        'y_min': min(y_values),
        'y_max': max(y_values),
        'width': max(x_values) - min(x_values),
        'height': max(y_values) - min(y_values),
    }


def _sampled_paths_length(sampled_paths, *, draw: bool) -> float:
    total = 0.0
    for sampled in sampled_paths:
        if bool(sampled.draw) != bool(draw):
            continue
        for index in range(1, len(sampled.points)):
            start = sampled.points[index - 1]
            end = sampled.points[index]
            total += float(numpy.hypot(end[0] - start[0], end[1] - start[1]))
    return total


def _sampled_paths_stable_payload(sampled_paths) -> list[dict[str, Any]]:
    return [
        {
            'draw': bool(sampled.draw),
            'points': [
                _stable_point_payload((float(point[0]), float(point[1])))
                for point in sampled.points
            ],
        }
        for sampled in sampled_paths
    ]


def _execution_preview_svg_from_sampled_paths(
    sampled_paths,
    *,
    board_width_m: float,
    board_height_m: float,
    pen_tip_radius_m: float | None = None,
) -> str:
    draw_strokes = [
        [
            [float(point[0]), float(point[1])]
            for point in sampled.points
        ]
        for sampled in sampled_paths
        if sampled.draw and len(sampled.points) >= 2
    ]
    return _sketch_preview_svg(
        draw_strokes,
        board_width_m=board_width_m,
        board_height_m=board_height_m,
        pen_tip_radius_m=pen_tip_radius_m,
    )


def _canonical_primitive_counts(plan: CanonicalPathPlan) -> dict[str, int]:
    line_count = sum(isinstance(command, LineSegment) for command in plan.commands)
    quadratic_count = sum(isinstance(command, QuadraticBezier) for command in plan.commands)
    cubic_count = sum(isinstance(command, CubicBezier) for command in plan.commands)
    arc_count = sum(isinstance(command, ArcSegment) for command in plan.commands)
    return {
        'line_primitive_count': int(line_count),
        'quadratic_primitive_count': int(quadratic_count),
        'cubic_primitive_count': int(cubic_count),
        'arc_primitive_count': int(arc_count),
        'curve_primitive_count': int(quadratic_count + cubic_count + arc_count),
    }


def _canonical_geometry_metrics(plan: CanonicalPathPlan) -> dict[str, int]:
    counts = _canonical_primitive_counts(plan)
    return {
        'line_count': int(counts['line_primitive_count']),
        'quadratic_count': int(counts['quadratic_primitive_count']),
        'cubic_count': int(counts['cubic_primitive_count']),
        'arc_count': int(counts['arc_primitive_count']),
        'total_curve_count': int(counts['curve_primitive_count']),
    }


def _executable_geometry_metrics(sampled_paths) -> dict[str, int]:
    draw_paths = [sampled for sampled in sampled_paths if sampled.draw]
    sampled_point_count = sum(len(sampled.points) for sampled in draw_paths)
    sampled_segment_count = sum(max(0, len(sampled.points) - 1) for sampled in draw_paths)
    return {
        'draw_path_count': int(len(draw_paths)),
        'sampled_point_count': int(sampled_point_count),
        'sampled_segment_count': int(sampled_segment_count),
    }


def _canonical_transport_size_summary(plan: CanonicalPathPlan) -> dict[str, int]:
    descriptor = canonical_plan_to_primitive_path_plan(plan)
    primitive_count = len(descriptor['primitives'])
    descriptor_bytes = len(
        json.dumps(descriptor, separators=(',', ':'), sort_keys=True).encode('utf-8')
    )
    return {
        'canonical_command_count': int(len(plan.commands)),
        'primitive_count': int(primitive_count),
        'primitive_descriptor_bytes': int(descriptor_bytes),
    }


def _enforce_sketch_draw_size_limits(summary: dict[str, int]) -> None:
    from wall_climber import web_server

    limits = {
        'max_canonical_command_count': int(web_server._SKETCH_DRAW_MAX_CANONICAL_COMMANDS),
        'max_primitive_count': int(web_server._SKETCH_DRAW_MAX_PRIMITIVES),
        'max_primitive_descriptor_bytes': int(web_server._SKETCH_DRAW_MAX_PRIMITIVE_DESCRIPTOR_BYTES),
    }
    violations: list[str] = []
    if int(summary['canonical_command_count']) > limits['max_canonical_command_count']:
        violations.append('canonical_command_count')
    if int(summary['primitive_count']) > limits['max_primitive_count']:
        violations.append('primitive_count')
    if int(summary['primitive_descriptor_bytes']) > limits['max_primitive_descriptor_bytes']:
        violations.append('primitive_descriptor_bytes')
    if violations:
        raise HTTPException(
            status_code=413,
            detail={
                'error': 'sketch preview plan is too large for the existing execution transport',
                'violations': violations,
                'counts': summary,
                'limits': limits,
            },
        )


def _bounds_payload(bounds: dict[str, float]) -> dict[str, float]:
    return {
        'x_min': float(bounds['x_min']),
        'x_max': float(bounds['x_max']),
        'y_min': float(bounds['y_min']),
        'y_max': float(bounds['y_max']),
        'width': float(bounds['x_max']) - float(bounds['x_min']),
        'height': float(bounds['y_max']) - float(bounds['y_min']),
    }


def _slowest_timing_stage(timing: dict[str, Any]) -> dict[str, Any] | None:
    candidates = {
        key: float(value)
        for key, value in timing.items()
        if key.endswith('_time_ms') and key != 'preview_total_time_ms'
    }
    if not candidates:
        return None
    key, value = max(candidates.items(), key=lambda item: item[1])
    return {'stage': key.removesuffix('_time_ms'), 'time_ms': float(value)}


def _image_preprocess_settings_from_parameters(
    parameters: dict[str, Any],
) -> PreprocessSettings | None:
    mode = parameters.get('image_preprocess_mode')
    if mode is None:
        return None
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {'photo', 'coloring_book'}:
        return None
    return PreprocessSettings(
        mode=normalized_mode,  # type: ignore[arg-type]
        raw_print=bool(parameters.get('image_raw_print', False)),
        target_resolution=int(parameters.get('image_target_resolution', 1024)),
        force_solid_black_lines=bool(parameters.get('image_force_solid_black_lines', False)),
        photo_lineart_model=str(
            parameters.get(
                'photo_lineart_model',
                parameters.get('image_photo_lineart_model', 'informative'),
            )
        ),
        nano_banana_prompt=str(
            parameters.get(
                'nano_banana_prompt',
                parameters.get('image_nano_banana_prompt', ''),
            )
        ),
        google_api_key=str(
            parameters.get(
                'google_api_key',
                parameters.get('image_google_api_key', ''),
            )
        ),
    ).normalized()


def _sketch_autotrace_direct_upload(
    preprocess_settings: PreprocessSettings | None,
    *,
    vectorization_method: str,
) -> bool:
    """Archive-style AutoTrace: feed original upload bytes (anti-aliasing intact).

    Coloring-book Raw Print skips AI upscaling but still runs a lightweight
    preprocess pass for pipeline previews. Vectorization must not go through
    ``preprocessed_bitmap`` + ``light_binarize_for_vectorizer``.
    """
    if preprocess_settings is None:
        return False
    if str(vectorization_method).strip().lower() != 'autotrace':
        return False
    return bool(
        preprocess_settings.mode == 'coloring_book'
        and preprocess_settings.raw_print
    )


def _canonical_plan_preview_strokes(canonical_plan) -> list[list[list[float]]]:
    from wall_climber.canonical_adapters import sampled_paths_from_canonical_plan

    strokes: list[list[list[float]]] = []
    for sampled in sampled_paths_from_canonical_plan(
        canonical_plan,
        curve_tolerance_m=0.01,
    ):
        if not sampled.draw or len(sampled.points) < 2:
            continue
        strokes.append([[float(point[0]), float(point[1])] for point in sampled.points])
    return strokes


def _decode_preprocess_lineart_png(preprocess_preview: dict[str, Any] | None) -> Any | None:
    if not preprocess_preview:
        return None
    data_url = str(preprocess_preview.get('lineart_data_url') or '').strip()
    if not data_url.startswith('data:image'):
        return None
    try:
        encoded = data_url.split(',', 1)[1]
        return decode_lineart_png(base64.b64decode(encoded))
    except (IndexError, ValueError, TypeError):
        return None


def _append_vectorization_pipeline_stage(
    preprocess_preview: dict[str, Any] | None,
    *,
    strokes: list[list[list[float]]],
    board_width_m: float,
    board_height_m: float,
    vectorization_method: str,
    placement_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    preview = dict(preprocess_preview or {})
    lineart_gray = _decode_preprocess_lineart_png(preview)
    if lineart_gray is not None:
        encoded, width, height = rasterize_strokes_on_lineart_frame(
            strokes,
            lineart_gray,
            board_width_m=float(board_width_m),
            board_height_m=float(board_height_m),
            placement_metadata=placement_metadata,
        )
    else:
        encoded, width, height = rasterize_board_strokes_board_frame(
            strokes,
            board_width_m=float(board_width_m),
            board_height_m=float(board_height_m),
        )
    label = 'AutoTrace' if str(vectorization_method).strip().lower() == 'autotrace' else 'Potrace'
    stages = list(preview.get('pipeline_stages') or [])
    stages.append(
        {
            'stage_id': 'vectorization',
            'label': label,
            'data_url': f'data:image/png;base64,{encoded}',
            'width_px': int(width),
            'height_px': int(height),
        }
    )
    preview['pipeline_stages'] = stages
    return preview


def _ai_sketch_pipeline_mode(
    *,
    preprocess_settings: PreprocessSettings | None,
    skipped_preprocess: bool,
    vectorization_method: str,
) -> str:
    if preprocess_settings is None:
        return f'sketch_{vectorization_method}'
    if skipped_preprocess:
        return f'sketch_raw_print_{vectorization_method}'
    if preprocess_settings.mode == 'photo':
        return f'sketch_ai_photo_{vectorization_method}'
    return f'sketch_ai_coloring_{vectorization_method}'


def _sketch_preview_response(
    plan,
    *,
    preview_id: str | None = None,
    canonical_plan: CanonicalPathPlan,
    board_width_m: float,
    board_height_m: float,
    preview_geometry_mode: str,
    use_smooth_svg: bool,
    curve_metadata: dict[str, Any] | None = None,
    pen_tip_radius_m: float | None = None,
    preprocess_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview = _sketch_preview_strokes(plan)
    metadata = dict(plan.metadata)
    base_metadata_warnings = tuple(str(item) for item in metadata.get('warnings') or ())
    curve_metadata = dict(curve_metadata or {})
    metadata.update(curve_metadata)
    metadata['preview_geometry_mode'] = preview_geometry_mode
    timing = dict(metadata.get('timing') or {})
    timing['slowest_stage'] = _slowest_timing_stage(timing)
    metadata['timing'] = timing
    if 'curve_fit_time_ms' in timing:
        metadata['curve_fit_time_ms'] = float(timing['curve_fit_time_ms'])
    primitive_counts = _canonical_primitive_counts(canonical_plan)
    metadata.update(primitive_counts)
    warnings = list(plan.metrics.warnings)
    warnings.extend(base_metadata_warnings)
    warnings.extend(str(item) for item in curve_metadata.get('warnings') or ())
    deduped_warnings = list(dict.fromkeys(warnings))
    metadata['warnings'] = tuple(deduped_warnings)
    preview_svg = (
        _smooth_sketch_preview_svg(
            canonical_plan,
            board_width_m=board_width_m,
            board_height_m=board_height_m,
            pen_tip_radius_m=pen_tip_radius_m,
        )
        if use_smooth_svg
        else _sketch_preview_svg(
            preview['strokes'],
            board_width_m=board_width_m,
            board_height_m=board_height_m,
            pen_tip_radius_m=pen_tip_radius_m,
        )
    )
    return {
        'ok': True,
        'preview_id': preview_id,
        'mode': plan.mode.value,
        'stroke_count': len(plan.strokes),
        'point_count': sum(len(stroke.points) for stroke in plan.strokes),
        'canonical_command_count': len(canonical_plan.commands),
        'metrics': asdict(plan.metrics),
        'metadata': metadata,
        'bounds': _drawing_plan_bounds(plan),
        'warnings': deduped_warnings,
        'preview_svg': preview_svg,
        'preview': preview,
        'preprocess_preview': preprocess_preview,
    }

