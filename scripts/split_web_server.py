#!/usr/bin/env python3
"""One-shot script to split web_server.py into http package modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'src' / 'wall_climber' / 'wall_climber'
HTTP = PKG / 'http'
ROUTES = HTTP / 'routes'


def main() -> None:
    src = PKG / 'web_server.py'
    lines = src.read_text().splitlines(keepends=True)

    def slice_lines(start: int, end: int) -> str:
        return ''.join(lines[start - 1 : end])

    helpers_body = slice_lines(778, 2235)
    resolve_web = slice_lines(2237, 2297)
    create_app_fn = slice_lines(2300, 4705)
    main_body = slice_lines(4708, 4756)

    ROUTES.mkdir(parents=True, exist_ok=True)

    runtime_header = (PKG / 'web_server.py').read_text().split('class WebBackendNode')[0]
    # Trim create_app and below from header - take only through BackendRuntime end
    runtime_mid = slice_lines(192, 776)
    (HTTP / 'runtime.py').write_text(runtime_header.split('_MAX_TEXT_CHARS')[0] + slice_lines(203, 776) + resolve_web)

    # Write helpers - use full import block from original through line 190
    import_block = slice_lines(1, 190)
    helpers_header = import_block.replace(
        'from wall_climber.port_utils import bind_listening_socket\n',
        '',
    ) + '''
from wall_climber.http.runtime import (
    BackendRuntime,
    LineartCacheEntry,
    PreviewCacheEntry,
    _run_cpu_bound,
)
'''
    (HTTP / 'helpers.py').write_text(helpers_header + helpers_body)

    # app_factory gets create_app with imports adjusted
    app_factory = '''from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any, Optional

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
from fastapi.responses import FileResponse, JSONResponse, Response

try:
    import websockets
except ImportError:
    websockets = None

from wall_climber import canonical_adapters as _canonical_adapters
from wall_climber import voice_stream_whisper_vad as _voice_stream
from wall_climber.canonical_adapters import (
    canonical_plan_diagnostics,
)
from wall_climber.canonical_builders import draw_strokes_to_canonical_plan
from wall_climber.canonical_optimizer import CanonicalOptimizationPolicy
from wall_climber.canonical_path import CanonicalPathPlan
from wall_climber.image_pipeline.autotrace_vector import is_autotrace_available
from wall_climber.image_pipeline.potrace_vector import is_potrace_available
from wall_climber.image_pipeline.ai_preprocess import (
    anilines_weights_cached,
    informative_weights_cached,
    swinir_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.vram_manager import cuda_available
from wall_climber.ingestion.text import TextGlyphOutline
from wall_climber._ttl_cache import TTLCache
from wall_climber.runtime_topics import (
    MODE_DRAW,
    MODE_OFF,
    MODE_TEXT,
    VALID_MANUAL_PEN_MODES,
    VALID_MODES,
)
from wall_climber.shared_config import load_shared_config

from wall_climber.http.runtime import (
    BackendRuntime,
    LineartCacheEntry,
    PreviewCacheEntry,
    _LINEART_CACHE_MAX_ENTRIES,
    _LINEART_CACHE_TTL_SECONDS,
    _MAX_DRAW_PLAN_BYTES,
    _MAX_UPLOAD_BYTES,
    _MAX_VECTOR_REQUEST_BYTES,
    _PREVIEW_CACHE_MAX_ENTRIES,
    _PREVIEW_CACHE_TTL_SECONDS,
    _resolve_web_asset_path,
    _web_ui_diagnostics,
)
from wall_climber.http import helpers as h
from wall_climber.http.routes import draw, health, preview, static, voice

'''
    # Replace helper references in create_app body
    body = create_app_fn
    for name in [
        '_require_json_object', '_load_json_request', '_reject_extra_fields',
        '_validate_text_value', '_validate_text_request', '_coerce_float', '_coerce_int',
        '_coerce_bool', '_validate_upload_id', '_validate_preview_id', '_stable_float',
        '_stable_point_payload', '_stable_payload', '_stable_hash', 'settings_hash',
        '_canonical_command_payload', 'canonical_plan_stable_payload', 'canonical_plan_hash',
        '_content_hash', '_normalize_text_column', '_text_column_x_bounds',
        '_resolve_text_start_placement', '_grouped_text_bounds', '_first_line_grouped_bounds',
        '_continuation_placement_center_y', '_max_partial_column_bottom_y',
        '_full_wrap_overlap_floor_y', '_is_cross_mode_text_seed', '_cross_mode_seed_anchor_y',
        '_text_ink_floor_y', '_bump_row_top_below_ink_floor', '_bump_row_top_below_global_ink',
        '_wrapped_lines_global_y_shift_amount', '_shift_placed_glyph_lines_y',
        '_text_bottom_y_from_groups', '_note_text_column_bottom_from_groups',
        '_normalize_text_font_source', '_expand_preview_bounds', '_preview_sampling_policy',
        '_runtime_sampling_policy', '_draw_optimization_policy', '_sketch_draw_optimization_policy',
        '_sampling_validation_step_m', '_validated_runtime_sampled_paths',
        '_preview_payload_from_strokes', '_interpolated_outside_safe_workspace_count',
        '_build_primitive_path_plan_message', '_primitive_path_plan_message_from_descriptor',
        '_build_execution_transport_message', '_sanitize_points', '_normalize_stroke_payload',
        '_validate_upload', '_validate_sketch_upload', '_image_value_error_to_http',
        '_drawing_plan_bounds', '_downsample_points_for_preview', '_sketch_preview_strokes',
        '_svg_number', '_sketch_preview_svg', '_command_start', '_smooth_sketch_preview_svg',
        '_sampled_paths_bounds', '_sampled_paths_length', '_sampled_paths_stable_payload',
        '_execution_preview_svg_from_sampled_paths', '_canonical_primitive_counts',
        '_canonical_geometry_metrics', '_executable_geometry_metrics',
        '_canonical_transport_size_summary', '_enforce_sketch_draw_size_limits',
        '_bounds_payload', '_slowest_timing_stage', '_image_preprocess_settings_from_parameters',
        '_sketch_autotrace_direct_upload', '_append_vectorization_pipeline_stage',
        '_ai_sketch_pipeline_mode', '_sketch_preview_response', '_run_cpu_bound',
    ]:
        body = body.replace(name, f'h.{name}')

    # create_app function - we'll keep monolithic for now and split routes via script phase 2
    (HTTP / 'app_factory.py').write_text(app_factory + body)

    (HTTP / '__init__.py').write_text(
        'from wall_climber.http.app_factory import create_app\n'
        'from wall_climber.http.runtime import BackendRuntime, WebBackendNode\n'
    )

    (ROUTES / '__init__.py').write_text('')

    # Thin web_server.py
    thin = '''from __future__ import annotations

from wall_climber.http import create_app, BackendRuntime, WebBackendNode
from wall_climber.http.runtime import (
    _ROS_IMPORT_ERROR,
    _web_ui_diagnostics,
    rclpy,
)
from wall_climber.http import helpers as _helpers
from wall_climber.port_utils import bind_listening_socket

# Backward-compatible re-exports for tests and monkeypatches.
from wall_climber.http.helpers import *  # noqa: F403
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)

''' + main_body

    (PKG / 'web_server.py').write_text(thin)
    print('Split complete')


if __name__ == '__main__':
    main()
