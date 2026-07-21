from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from pathlib import Path
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
    canonical_plan_debug_payload,
    canonical_plan_diagnostics,
    canonical_plan_to_draw_strokes,
    canonical_plan_to_legacy_strokes,
    canonical_plan_to_primitive_path_plan,
)
from wall_climber.canonical_path import LineSegment
from wall_climber.image_pipeline.ai_preprocess.preview_encode import decode_lineart_png
from wall_climber.canonical_builders import (
    draw_strokes_to_canonical_plan,
    text_glyph_outlines_to_canonical_plan,
)
from wall_climber.canonical_optimizer import CanonicalOptimizationPolicy
from wall_climber.canonical_path import CanonicalPathPlan
from wall_climber.canonical_ops import (
    cleanup_canonical_plan,
    normalize_placement,
    place_canonical_plan_on_board,
    place_grouped_text_on_board,
)
from wall_climber.canonical_optimizer import optimize_canonical_plan
from wall_climber.image_pipeline.adapters import drawing_path_plan_to_canonical
from wall_climber.image_pipeline.curve_fit import drawing_path_plan_to_smooth_canonical
from wall_climber.image_pipeline.ai_preprocess import (
    AnilinesModelError,
    InformativeModelError,
    SwinirModelError,
)
from wall_climber.image_pipeline.ai_preprocess.types import PreprocessSettings
from wall_climber.vector_pipeline import VectorPlacement
from wall_climber.ingestion.svg import vectorize_svg
from wall_climber.ingestion.text import normalize_text_plan_input, vectorize_text_grouped
from wall_climber.ingestion.upload_routing import classify_uploaded_vector_file
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.image_pipeline.potrace_vector import (
    is_potrace_available,
    vectorize_potrace_image_to_plan,
)
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
    _MAX_DRAW_PLAN_BYTES,
    BackendRuntime,
    LineartCacheEntry,
    PreviewCacheEntry,
    _LINEART_CACHE_MAX_ENTRIES,
    _LINEART_CACHE_TTL_SECONDS,
    _MAX_DRAW_PLAN_BYTES,
    _MAX_SVG_BYTES,
    _MAX_UPLOAD_BYTES,
    _MAX_VECTOR_REQUEST_BYTES,
    _PREVIEW_CACHE_MAX_ENTRIES,
    _PREVIEW_CACHE_TTL_SECONDS,
    _resolve_web_asset_path,
    _web_ui_diagnostics,
)
from wall_climber import web_server as _web_server
from wall_climber.http import helpers as h


from wall_climber.http.app_state import AppState


def _register_preview_draw_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
    shared = state.shared
    text_layout_defaults = state.text_layout_defaults
    draw_execution_defaults = state.draw_execution_defaults
    preview_sampling_policy = state.preview_sampling_policy
    runtime_sampling_policy = state.runtime_sampling_policy
    svg_optimization_policy = state.svg_optimization_policy
    sketch_draw_optimization_policy = state.sketch_draw_optimization_policy
    preview_cache = state.preview_cache
    lineart_cache = state.lineart_cache
    _store_preview = state._store_preview
    _load_preview = state._load_preview
    _attach_preview_contract = state._attach_preview_contract
    _is_sketch_source_type = state._is_sketch_source_type
    _preview_allowed_modes = state._preview_allowed_modes
    _carriage_safe_writable_bounds_for_sketch = state._carriage_safe_writable_bounds_for_sketch
    _board_bounds_for_sketch = state._board_bounds_for_sketch
    _preview_writable_bounds_for_source = state._preview_writable_bounds_for_source
    _normalize_path_optimizer = state._normalize_path_optimizer

    async def preview_sketch_centerline(
        file: UploadFile = File(...),
        margin_m: Optional[float] = Form(None),
        max_image_dim: Optional[int] = Form(None),
        min_component_area_px: Optional[int] = Form(None),
        min_stroke_length_px: Optional[float] = Form(None),
        simplify_epsilon_px: Optional[float] = Form(None),
        line_sensitivity: Optional[float] = Form(None),
        sketch_extraction_method: Optional[str] = Form(None),
        skeleton_prune_px: Optional[float] = Form(None),
        merge_gap_px: Optional[float] = Form(None),
        merge_max_angle_deg: Optional[float] = Form(None),
        optimization_preset: Optional[str] = Form(None),
        preview_geometry_mode: Optional[str] = Form(None),
        curve_tolerance_px: Optional[float] = Form(None),
        curve_tolerance_m: Optional[float] = Form(None),
        scale_percent: Optional[float] = Form(None),
        center_x_m: Optional[float] = Form(None),
        center_y_m: Optional[float] = Form(None),
        fit_to_safe_area: Optional[bool] = Form(None),
        optimize_stroke_order: Optional[bool] = Form(None),
        path_optimizer: Optional[str] = Form(None),
        preserve_tiny_details: Optional[bool] = Form(None),
        minimum_drawable_feature_m: Optional[float] = Form(None),
        tiny_detail_candidate_max_feature_m: Optional[float] = Form(None),
        tiny_detail_expand_mode: Optional[str] = Form(None),
        tiny_detail_max_expansions: Optional[int] = Form(None),
        thin_line_min_width_mm: Optional[float] = Form(None),
        enable_face_handling: Optional[bool] = Form(None),
        capture_all_details: Optional[bool] = Form(None),
        text_as_outline: Optional[bool] = Form(None),
        collect_svg: Optional[bool] = Form(None),
        vectorization_method: Optional[str] = Form(None),
        requested_input_type: Optional[str] = None,
        image_preprocess_mode: Optional[str] = None,
        image_raw_print: Optional[bool] = None,
        image_target_resolution: Optional[int] = None,
        image_force_solid_black_lines: Optional[bool] = None,
        curve_fit_time_limit_ms: Optional[float] = None,
        autotrace_speckle_strength: Optional[int] = None,
        image_photo_lineart_model: Optional[str] = None,
        image_nano_banana_prompt: Optional[str] = None,
        image_google_api_key: Optional[str] = None,
    ) -> JSONResponse:
        try:
            content = await file.read(_MAX_UPLOAD_BYTES + 1)
            h._validate_sketch_upload(file, content)
            normalized_requested_input_type = str(requested_input_type or 'auto').strip().lower()
            if normalized_requested_input_type not in {'auto', 'sketch_image', 'sketch'}:
                raise HTTPException(
                    status_code=422,
                    detail='input_type must be one of: auto, sketch_image, sketch',
                )
            sketch_fit_to_safe_area = h._coerce_bool(
                True if fit_to_safe_area is None else fit_to_safe_area,
                field_name='fit_to_safe_area',
                default=True,
            )
            sketch_safe_bounds = _carriage_safe_writable_bounds_for_sketch()
            sketch_board_bounds = _board_bounds_for_sketch()
            sketch_fit_bounds = sketch_safe_bounds if sketch_fit_to_safe_area else sketch_board_bounds
            sketch_margin_m = h._coerce_float(
                0.05 if margin_m is None else margin_m,
                field_name='margin_m',
                minimum=0.0,
                maximum=min(float(sketch_fit_bounds['width']), float(sketch_fit_bounds['height'])) * 0.45,
            )
            if max_image_dim is None:
                sketch_max_image_dim = 0
            else:
                sketch_max_image_dim = h._coerce_int(
                    max_image_dim,
                    field_name='max_image_dim',
                    minimum=0,
                    maximum=65536,
                )
            sketch_min_component_area_px = h._coerce_int(
                2 if min_component_area_px is None else min_component_area_px,
                field_name='min_component_area_px',
                minimum=1,
                maximum=100000,
            )
            sketch_min_stroke_length_px = h._coerce_float(
                1.0 if min_stroke_length_px is None else min_stroke_length_px,
                field_name='min_stroke_length_px',
                minimum=0.0,
                maximum=100000.0,
            )
            sketch_simplify_epsilon_px = h._coerce_float(
                0.25 if simplify_epsilon_px is None else simplify_epsilon_px,
                field_name='simplify_epsilon_px',
                minimum=0.0,
                maximum=10000.0,
            )
            sketch_line_sensitivity = h._coerce_float(
                0.35 if line_sensitivity is None else line_sensitivity,
                field_name='line_sensitivity',
                minimum=0.0,
                maximum=0.95,
            )
            sketch_skeleton_prune_px = h._coerce_float(
                # Match the new sketch_centerline default of 6 px (was 4).
                # Higher prune length means fewer spurious "wiggles" at
                # stroke tips on hatched / inked source images.
                6.0 if skeleton_prune_px is None else skeleton_prune_px,
                field_name='skeleton_prune_px',
                minimum=0.0,
                maximum=100.0,
            )
            sketch_extraction = str(sketch_extraction_method or 'adaptive').strip().lower()
            if sketch_extraction not in {'hysteresis_ink', 'otsu', 'adaptive'}:
                raise HTTPException(
                    status_code=422,
                    detail='sketch_extraction_method must be one of: hysteresis_ink, otsu, adaptive',
                )
            sketch_merge_gap_px = h._coerce_float(
                0.0 if merge_gap_px is None else merge_gap_px,
                field_name='merge_gap_px',
                minimum=0.0,
                maximum=1000.0,
            )
            sketch_merge_max_angle_deg = h._coerce_float(
                20.0 if merge_max_angle_deg is None else merge_max_angle_deg,
                field_name='merge_max_angle_deg',
                minimum=0.0,
                maximum=180.0,
            )
            sketch_optimization_preset = str(optimization_preset or 'auto').strip().lower()
            sketch_preview_geometry_mode = str(preview_geometry_mode or 'smooth_curves').strip().lower()
            if sketch_preview_geometry_mode not in {'smooth_curves', 'polyline'}:
                raise HTTPException(
                    status_code=422,
                    detail="preview_geometry_mode must be one of: smooth_curves, polyline",
                )
            sketch_optimize_stroke_order = h._coerce_bool(
                False if optimize_stroke_order is None else optimize_stroke_order,
                field_name='optimize_stroke_order',
                default=False,
            )
            sketch_path_optimizer = _normalize_path_optimizer(path_optimizer)
            sketch_curve_tolerance_px = h._coerce_float(
                # 0.6 px (~4mm at 1000px / 6m board) is a tight enough
                # tolerance to keep the fit visually faithful while still
                # letting Schneider collapse smooth arcs into a few
                # cubics. Note that the dominant geometric deviation on
                # rasters is skeletonisation noise (3-7mm on a 3px-thick
                # stroke); reducing the curve tolerance below that does
                # not improve fidelity, only inflates the canonical plan
                # back into hundreds of line segments.
                0.6 if curve_tolerance_px is None else curve_tolerance_px,
                field_name='curve_tolerance_px',
                minimum=0.05,
                maximum=50.0,
            )
            sketch_curve_tolerance_m = (
                None if curve_tolerance_m is None
                else h._coerce_float(
                    curve_tolerance_m,
                    field_name='curve_tolerance_m',
                    minimum=1.0e-6,
                    maximum=0.25,
                )
            )
            sketch_scale_percent = h._coerce_float(
                100.0 if scale_percent is None else scale_percent,
                field_name='scale_percent',
                minimum=1.0,
                maximum=500.0,
            )
            sketch_center_x_m = (
                None if center_x_m is None
                else h._coerce_float(
                    center_x_m,
                    field_name='center_x_m',
                    minimum=0.0,
                    maximum=float(shared.board.width),
                )
            )
            sketch_center_y_m = (
                None if center_y_m is None
                else h._coerce_float(
                    center_y_m,
                    field_name='center_y_m',
                    minimum=0.0,
                    maximum=float(shared.board.height),
                )
            )
            sketch_thin_line_min_width_mm = h._coerce_float(
                0.0 if thin_line_min_width_mm is None else thin_line_min_width_mm,
                field_name='thin_line_min_width_mm',
                minimum=0.0,
                maximum=6.0,
            )
            sketch_enable_face_handling = h._coerce_bool(
                True if enable_face_handling is None else enable_face_handling,
                field_name='enable_face_handling',
                default=True,
            )
            sketch_preserve_tiny_details = h._coerce_bool(
                True if preserve_tiny_details is None else preserve_tiny_details,
                field_name='preserve_tiny_details',
                default=True,
            )
            if not isinstance(vectorization_method, (str, type(None))):
                sketch_vectorization_method = 'autotrace'
            else:
                sketch_vectorization_method = str(vectorization_method or 'autotrace').strip().lower()
            # Legacy clients may still send removed engine names; route them to AutoTrace.
            if sketch_vectorization_method in {'centerline', 'lingdong', 'lee_sknw', 'skeleton'}:
                sketch_vectorization_method = 'autotrace'
            if sketch_vectorization_method not in {'potrace', 'autotrace'}:
                raise HTTPException(
                    status_code=422,
                    detail='vectorization_method must be one of: autotrace, potrace',
                )
            if sketch_vectorization_method == 'potrace' and not _web_server.is_potrace_available():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        'potrace is not installed or is not on PATH. '
                        'Install the potrace package (e.g. apt install potrace) or use AutoTrace.'
                    ),
                )
            if sketch_vectorization_method == 'autotrace' and not _web_server.is_autotrace_available():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        'autotrace is not installed or is not on PATH. '
                        'Run scripts/install_autotrace.sh or use Potrace vectorization.'
                    ),
                )
            sketch_parameters = {
                'margin_m': sketch_margin_m,
                'max_image_dim': sketch_max_image_dim,
                'preview_geometry_mode': sketch_preview_geometry_mode,
                'curve_tolerance_px': sketch_curve_tolerance_px,
                'curve_tolerance_m': sketch_curve_tolerance_m,
                'scale_percent': sketch_scale_percent,
                'center_x_m': sketch_center_x_m,
                'center_y_m': sketch_center_y_m,
                'fit_to_safe_area': sketch_fit_to_safe_area,
                'optimize_stroke_order': sketch_optimize_stroke_order,
                'path_optimizer': sketch_path_optimizer,
                'preserve_tiny_details': False,
                'requested_input_type': normalized_requested_input_type,
                'vectorization_method': sketch_vectorization_method,
            }
            if image_preprocess_mode is not None:
                sketch_parameters['image_preprocess_mode'] = str(image_preprocess_mode).strip().lower()
            if image_raw_print is not None:
                sketch_parameters['image_raw_print'] = bool(image_raw_print)
            if image_target_resolution is not None:
                sketch_parameters['image_target_resolution'] = int(image_target_resolution)
            if image_force_solid_black_lines is not None:
                sketch_parameters['image_force_solid_black_lines'] = bool(image_force_solid_black_lines)
            if image_photo_lineart_model is not None:
                sketch_parameters['image_photo_lineart_model'] = str(image_photo_lineart_model).strip().lower()
            if image_nano_banana_prompt is not None:
                sketch_parameters['image_nano_banana_prompt'] = str(image_nano_banana_prompt)
            if image_google_api_key is not None:
                sketch_parameters['image_google_api_key'] = str(image_google_api_key).strip()
            sketch_curve_fit_time_limit_ms = h._coerce_float(
                3000.0 if curve_fit_time_limit_ms is None else curve_fit_time_limit_ms,
                field_name='curve_fit_time_limit_ms',
                minimum=0.0,
                maximum=3_600_000.0,
            )
            sketch_autotrace_speckle_strength = h._coerce_int(
                1 if autotrace_speckle_strength is None else autotrace_speckle_strength,
                field_name='autotrace_speckle_strength',
                minimum=0,
                maximum=5,
            )
            def _vectorize_sketch_and_fit() -> tuple[Any, Any, dict[str, Any], str, str, bytes, dict[str, Any] | None]:
                effective_input_type = 'sketch_image'
                preview_started = time.perf_counter()
                preprocess_preview: dict[str, Any] | None = None
                preprocess_settings = h._image_preprocess_settings_from_parameters(sketch_parameters)
                vectorization_content = content
                preprocessed_bitmap = None
                skipped_preprocess = False
                if preprocess_settings is not None:
                    cache_key = h._lineart_preprocess_cache_key(content, preprocess_settings)
                    cached_lineart = lineart_cache.load(cache_key) if cache_key else None
                    if cached_lineart is not None:
                        preprocess_preview = h._lineart_cache_hit_preview(cached_lineart)
                        skipped_preprocess = bool(preprocess_preview.get('skipped_preprocess'))
                        vectorization_content = cached_lineart.lineart_png
                        preprocessed_bitmap = decode_lineart_png(cached_lineart.lineart_png)
                    else:
                        preprocess_result = _web_server.preprocess_image_to_lineart(content, preprocess_settings)
                        preprocess_preview = preprocess_result.to_preprocess_preview_payload()
                        skipped_preprocess = preprocess_result.skipped_preprocess
                        if preprocess_result.lineart_png != content or not skipped_preprocess:
                            vectorization_content = preprocess_result.lineart_png
                            preprocessed_bitmap = decode_lineart_png(preprocess_result.lineart_png)
                            if cache_key:
                                lineart_cache.store(
                                    cache_key,
                                    LineartCacheEntry(
                                        lineart_png=preprocess_result.lineart_png,
                                        preprocess_preview=dict(preprocess_preview),
                                        created_at_unix=time.time(),
                                    ),
                                )
                if h._sketch_autotrace_direct_upload(
                    preprocess_settings,
                    vectorization_method=sketch_vectorization_method,
                ):
                    vectorization_content = content
                    preprocessed_bitmap = None
                pipeline_mode = h._ai_sketch_pipeline_mode(
                    preprocess_settings=preprocess_settings,
                    skipped_preprocess=skipped_preprocess,
                    vectorization_method=sketch_vectorization_method,
                )
                vectorize_kwargs = {
                    'board_width_m': float(shared.board.width),
                    'board_height_m': float(shared.board.height),
                    'margin_m': sketch_margin_m,
                    'max_image_dim': sketch_max_image_dim,
                    'scale_percent': sketch_scale_percent,
                    'center_x_m': sketch_center_x_m,
                    'center_y_m': sketch_center_y_m,
                    'fit_bounds_m': sketch_fit_bounds,
                    'validation_bounds_m': sketch_safe_bounds,
                    'curve_tolerance': max(0.01, float(sketch_curve_tolerance_px) * 0.05),
                    'preprocessed_bitmap': preprocessed_bitmap,
                }
                if sketch_vectorization_method == 'potrace':
                    plan = _web_server.vectorize_potrace_image_to_plan(vectorization_content, **vectorize_kwargs)
                else:
                    plan = _web_server.vectorize_autotrace_image_to_plan(
                        vectorization_content,
                        speckle_strength=sketch_autotrace_speckle_strength,
                        **vectorize_kwargs,
                    )
                plan.metadata.update(
                    {
                        'requested_input_type': normalized_requested_input_type,
                        'detected_input_type': effective_input_type,
                        'pipeline_mode': pipeline_mode,
                        'vectorization_method': sketch_vectorization_method,
                        'autotrace_direct_upload': h._sketch_autotrace_direct_upload(
                            preprocess_settings,
                            vectorization_method=sketch_vectorization_method,
                        ),
                    }
                )
                if preprocess_preview is not None:
                    plan.metadata['preprocess_timing_ms'] = dict(
                        preprocess_preview.get('timing_ms') or {}
                    )
                curve_metadata: dict[str, Any] = {
                    'preview_geometry_mode': sketch_preview_geometry_mode,
                    'curve_tolerance_px': float(sketch_curve_tolerance_px),
                    'fit_to_safe_area': bool(sketch_fit_to_safe_area),
                    'safe_x_min': float(sketch_safe_bounds['x_min']),
                    'safe_x_max': float(sketch_safe_bounds['x_max']),
                    'safe_y_min': float(sketch_safe_bounds['y_min']),
                    'safe_y_max': float(sketch_safe_bounds['y_max']),
                    'safe_width': float(sketch_safe_bounds['width']),
                    'safe_height': float(sketch_safe_bounds['height']),
                    'safe_bounds_m': sketch_safe_bounds,
                    'fit_bounds_m': sketch_fit_bounds,
                    'validation_bounds_m': sketch_safe_bounds,
                }
                curve_fit_start = time.perf_counter()
                if sketch_preview_geometry_mode == 'smooth_curves':
                    scale_m_per_px = float(dict(plan.metadata).get('scale_m_per_px') or 0.0)
                    effective_curve_tolerance_m = (
                        float(sketch_curve_tolerance_m)
                        if sketch_curve_tolerance_m is not None
                        else max(1.0e-6, float(sketch_curve_tolerance_px) * scale_m_per_px)
                    )
                    smooth_result = drawing_path_plan_to_smooth_canonical(
                        plan,
                        curve_tolerance_m=effective_curve_tolerance_m,
                        max_curve_segment_points=96,
                        max_fit_time_ms=float(sketch_curve_fit_time_limit_ms),
                    )
                    canonical_plan = smooth_result.plan
                    curve_metadata.update(dict(smooth_result.metadata))
                    curve_metadata['curve_tolerance_m'] = float(effective_curve_tolerance_m)
                else:
                    canonical_plan = drawing_path_plan_to_canonical(plan)
                    curve_metadata.update(
                        {
                            'curve_tolerance_m': None if sketch_curve_tolerance_m is None else float(sketch_curve_tolerance_m),
                            'line_primitive_count': sum(isinstance(command, LineSegment) for command in canonical_plan.commands),
                            'quadratic_primitive_count': 0,
                            'cubic_primitive_count': 0,
                            'curve_primitive_count': 0,
                        }
                    )
                curve_fit_time_ms = (time.perf_counter() - curve_fit_start) * 1000.0
                timing = dict(plan.metadata.get('timing') or {})
                timing['curve_fit_time_ms'] = float(curve_fit_time_ms)
                timing['preview_total_time_ms'] = (time.perf_counter() - preview_started) * 1000.0
                plan.metadata['timing'] = timing
                return (
                    plan,
                    canonical_plan,
                    curve_metadata,
                    effective_input_type,
                    pipeline_mode,
                    vectorization_content,
                    preprocess_preview,
                )

            sketch_parameters['input_type'] = 'sketch_image'
            try:
                (
                    plan,
                    canonical_plan,
                    curve_metadata,
                    effective_input_type,
                    pipeline_mode,
                    vectorization_content,
                    preprocess_preview,
                ) = await h._run_cpu_bound(_vectorize_sketch_and_fit)
            except (AnilinesModelError, InformativeModelError, SwinirModelError) as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            except RuntimeError as exc:
                message = str(exc)
                if 'out of memory' in message.lower():
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f'CUDA out of memory: {message}. '
                            'Try lowering AI Target Resolution to 1536 px or below.'
                        ),
                    ) from exc
                raise HTTPException(status_code=500, detail=message) from exc
            except ValueError as exc:
                raise h._image_value_error_to_http(exc)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=503, detail=str(exc))

            sketch_parameters['pipeline_mode'] = pipeline_mode

            preview_id = uuid.uuid4().hex
            response_payload = h._sketch_preview_response(
                plan,
                preview_id=preview_id,
                canonical_plan=canonical_plan,
                board_width_m=float(shared.board.width),
                board_height_m=float(shared.board.height),
                preview_geometry_mode=sketch_preview_geometry_mode,
                use_smooth_svg=sketch_preview_geometry_mode == 'smooth_curves',
                curve_metadata=curve_metadata,
                pen_tip_radius_m=float(shared.pen.tip_radius),
                preprocess_preview=h._append_vectorization_pipeline_stage(
                    preprocess_preview,
                    strokes=(
                        h._canonical_plan_preview_strokes(canonical_plan)
                        or list(h._sketch_preview_strokes(plan).get('strokes') or [])
                    ),
                    board_width_m=float(shared.board.width),
                    board_height_m=float(shared.board.height),
                    vectorization_method=sketch_vectorization_method,
                    placement_metadata=dict(plan.metadata),
                ),
            )
            response_payload['detected_input_type'] = effective_input_type
            generic_entry = _store_preview(
                preview_id=preview_id,
                source_type='sketch_image',
                canonical_plan=canonical_plan,
                preview_payload=dict(response_payload.get('preview') or {}),
                commit_request={'preview_id': preview_id},
                input_type=effective_input_type,
                pipeline_mode=pipeline_mode,
                source_hash=h._content_hash(
                    {
                        'original': h._content_hash(content),
                        'vectorized': h._content_hash(vectorization_content),
                        'input_type': effective_input_type,
                        'preprocess': preprocess_preview,
                    }
                ),
                settings=sketch_parameters,
                metadata=dict(response_payload.get('metadata') or {}),
                warnings=tuple(str(item) for item in response_payload.get('warnings') or ()),
                source_filename=str(file.filename or ''),
                drawing_plan=plan,
                route_metadata=dict(response_payload.get('metadata') or {}),
                optimize_stroke_order=sketch_optimize_stroke_order,
                path_optimizer=sketch_path_optimizer,
            )
            return JSONResponse(_attach_preview_contract(response_payload, generic_entry))
        finally:
            await file.close()

    def _cached_preview_allowed_modes(entry: PreviewCacheEntry) -> tuple[str, ...]:
        return _preview_allowed_modes(entry.source_type)

    def _cached_preview_writable_bounds(entry: PreviewCacheEntry) -> dict[str, float]:
        if _is_sketch_source_type(entry.source_type):
            return _carriage_safe_writable_bounds_for_sketch()
        return runtime.node.carriage_safe_writable_bounds()

    def _draw_cached_preview_response(
        entry: PreviewCacheEntry,
    ) -> dict[str, Any]:
        if _is_sketch_source_type(entry.source_type):
            size_summary = h._canonical_transport_size_summary(entry.executable_canonical_plan)
            h._enforce_sketch_draw_size_limits(size_summary)
        publish_start = time.perf_counter()
        transport = runtime.node.publish_execution_plan(
            entry.primitive_plan,
            allowed_modes=_cached_preview_allowed_modes(entry),
        )
        publish_ms = _elapsed_ms(publish_start)
        timings = {
            'optimization_ms': 0.0,
            'transport_build_ms': 0.0,
            'publish_ms': publish_ms,
        }
        preview_payload = dict(entry.preview_payload)
        preview_payload['preview_id'] = entry.preview_id
        preview_payload['canonical_hash'] = entry.canonical_hash
        preview_payload['executable_canonical_hash'] = entry.executable_canonical_hash
        preview_payload['primitive_hash'] = entry.primitive_hash
        preview_payload['execution_hash'] = entry.execution_hash
        preview_payload['settings_hash'] = entry.settings_hash
        preview_payload['execution_preview_svg'] = entry.execution_preview_svg
        preview_payload['diagnostics'] = canonical_plan_diagnostics(
            entry.executable_canonical_plan,
            preview_sampling_policy=preview_sampling_policy,
            runtime_sampling_policy=runtime_sampling_policy,
        )
        _record_last_plan_debug(
            source_type=entry.source_type,
            canonical_plan=entry.executable_canonical_plan,
            preview_payload=preview_payload,
            timings=timings,
            optimizer_stats=entry.metrics.get('optimization') or entry.optimizer_stats,
            route_metadata={
                **dict(entry.route_metadata or {}),
                'preview_id': entry.preview_id,
                'input_type': entry.input_type,
                'pipeline_mode': entry.pipeline_mode,
                'source_hash': entry.source_hash,
                'settings': entry.settings,
                'settings_hash': entry.settings_hash,
                'metadata': entry.metadata,
                'used_cached_executable_payload': True,
                'optimized': bool(entry.metrics.get('optimized')),
                'cached_canonical_hash': entry.canonical_hash,
                'published_canonical_hash': entry.executable_canonical_hash,
                'primitive_hash': entry.primitive_hash,
                'execution_hash': entry.execution_hash,
            },
            transport=transport,
            committed=True,
            command_metadata=entry.command_metadata,
        )
        _record_last_execution_debug(
            source_type=entry.source_type,
            preview_payload=preview_payload,
            transport=transport,
            timings=timings,
        )
        if entry.curve_fit_payload:
            _record_last_curve_fit_debug(entry.curve_fit_payload)
        elif entry.source_type == 'svg':
            _record_curve_fit_unavailable('svg')

        primitive_count = len(entry.primitive_descriptor.get('primitives') or ())
        primitive_descriptor_bytes = len(
            json.dumps(entry.primitive_descriptor, separators=(',', ':'), sort_keys=True).encode('utf-8')
        )
        # Advance the text continuation cursor so the next text submission
        # resumes on the same line (with a word-spacing gap), or wraps onto
        # a new line if it would overflow the writable width. The preview
        # handler pre-computed the next (X, Y) and stored it in metadata.
        if entry.source_type == 'text':
            try:
                text_column = str(
                    (entry.metadata or {}).get('text_column')
                    or ((entry.settings or {}).get('text_column'))
                    or 'full'
                )
                try:
                    normalized_draw_column = h._normalize_text_column(text_column)
                except ValueError:
                    normalized_draw_column = 'full'
                runtime.push_text_ink_snapshot(normalized_draw_column)
                metadata = entry.metadata or {}
                if 'text_next_cursor' in metadata:
                    next_cursor = metadata.get('text_next_cursor')
                    if isinstance(next_cursor, dict):
                        runtime.set_text_cursor(
                            float(next_cursor.get('x')),
                            float(next_cursor.get('y')),
                            text_column,
                        )
                    elif next_cursor is None:
                        runtime.set_text_cursor(None, None, text_column)
                text_bottom_y = metadata.get('text_bottom_y')
                if text_bottom_y is not None:
                    bottom_y = float(text_bottom_y)
                    runtime.note_text_global_bottom_y(bottom_y)
                    if text_column == 'full':
                        runtime.note_text_full_width_bottom_y(bottom_y)
                    elif text_column in {'left', 'center', 'right'}:
                        runtime.note_text_column_bottom_y(text_column, bottom_y)
                if normalized_draw_column in {'left', 'center', 'right'}:
                    runtime.clear_text_cursor_position('full')
                elif normalized_draw_column == 'full':
                    for partial_column in ('left', 'center', 'right'):
                        runtime.clear_text_cursor_position(partial_column)
                runtime.set_last_text_draw_column(normalized_draw_column)
            except (TypeError, ValueError):
                pass
        return {
            'ok': True,
            'published': True,
            'active_mode': MODE_TEXT if entry.source_type == 'text' else MODE_DRAW,
            'source_type': entry.source_type,
            'input_type': entry.input_type,
            'pipeline_mode': entry.pipeline_mode,
            'preview_id': entry.preview_id,
            'canonical_hash': entry.canonical_hash,
            'cached_canonical_hash': entry.canonical_hash,
            'executable_canonical_hash': entry.executable_canonical_hash,
            'primitive_hash': entry.primitive_hash,
            'execution_hash': entry.execution_hash,
            'settings_hash': entry.settings_hash,
            'preview_draw_hash_match': entry.canonical_hash == entry.executable_canonical_hash,
            'primitive_hash_match': True,
            'execution_hash_match': True,
            'used_cached_preview_plan': True,
            'used_cached_executable_payload': True,
            'optimized': bool(entry.metrics.get('optimized')),
            'optimization': dict(entry.metrics.get('optimization') or {}),
            'metrics': dict(entry.metrics),
            'canonical_command_count': len(entry.executable_canonical_plan.commands),
            'cached_canonical_command_count': len(entry.canonical_plan.commands),
            'primitive_count': int(primitive_count),
            'primitive_descriptor_bytes': int(primitive_descriptor_bytes),
            'transport': transport,
            'warnings': list(entry.warnings),
            'timings_ms': timings,
        }



    @app.post('/api/draw/live-stroke')
    async def draw_live_stroke(request: Request) -> JSONResponse:
        raw = await h._load_json_request(
            request,
            name='live stroke request',
            max_bytes=_MAX_DRAW_PLAN_BYTES,
        )
        h._reject_extra_fields(raw, {'strokes', 'preview_id'}, 'live stroke request')
        if raw.get('preview_id'):
            raise HTTPException(
                status_code=409,
                detail='Live drawing is disabled while an uploaded preview is active.',
            )
        writable_bounds = runtime.node.carriage_safe_writable_bounds()
        encoded_payload = h._normalize_stroke_payload(
            {'frame': 'board', 'strokes': raw.get('strokes')},
            writable_bounds,
        )
        stroke_payload = json.loads(encoded_payload)
        stroke_tuples: list[tuple[tuple[float, float], ...]] = []
        for stroke in stroke_payload.get('strokes') or []:
            if not isinstance(stroke, dict) or not stroke.get('draw'):
                continue
            points = [
                (float(point[0]), float(point[1]))
                for point in stroke.get('points') or []
            ]
            if len(points) >= 2:
                stroke_tuples.append(tuple(points))
        if not stroke_tuples:
            raise HTTPException(status_code=422, detail='live stroke request has no drawable strokes')
        canonical_plan = draw_strokes_to_canonical_plan(
            tuple(stroke_tuples),
            theta_ref=float(shared.draw_execution.fixed_draw_theta_rad),
            frame='board',
        )
        primitive_plan = h._build_execution_transport_message(
            canonical_plan,
            writable_bounds=writable_bounds,
            shared_config=shared,
            sampling_policy=runtime_sampling_policy,
        )
        transport = runtime.node.publish_execution_plan(
            primitive_plan,
            allowed_modes=(MODE_DRAW,),
        )
        return JSONResponse({'ok': True, 'transport': transport})

    @app.post('/api/preprocess/lineart')
    async def preprocess_lineart_only(
        file: UploadFile = File(...),
        settings_json: Optional[str] = Form(None),
    ) -> JSONResponse:
        content = await file.read(_MAX_UPLOAD_BYTES + 1)
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail='uploaded file exceeds the maximum allowed size')
        h._validate_sketch_upload(file, content)
        settings: dict[str, Any] = {}
        if settings_json not in (None, ''):
            try:
                settings = json.loads(str(settings_json))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f'settings_json is invalid JSON: {exc}')
        settings['image_preprocess_mode'] = 'photo'
        settings['image_raw_print'] = False
        preprocess_settings = h._image_preprocess_settings_from_parameters(settings)
        if preprocess_settings is None or preprocess_settings.mode != 'photo':
            raise HTTPException(
                status_code=422,
                detail='preprocess/lineart requires photo mode AI settings',
            )
        preprocess_result = _web_server.preprocess_image_to_lineart(content, preprocess_settings)
        payload = preprocess_result.to_preprocess_preview_payload()
        payload['lineart_data_url'] = payload.get('lineart_data_url')
        return JSONResponse(payload)

    @app.post('/api/preview/edited-lineart')
    async def preview_edited_lineart(
        file: UploadFile = File(...),
        settings_json: Optional[str] = Form(None),
    ) -> JSONResponse:
        settings: dict[str, Any] = {}
        if settings_json not in (None, ''):
            try:
                settings = json.loads(str(settings_json))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f'settings_json is invalid JSON: {exc}')
        settings = {
            **settings,
            'image_preprocess_mode': 'coloring_book',
            'image_raw_print': True,
        }
        requested_input_type = str(settings.pop('input_type', 'auto'))
        return await preview_sketch_centerline(
            file=file,
            margin_m=settings.get('margin_m'),
            max_image_dim=settings.get('max_image_dim'),
            min_component_area_px=settings.get('min_component_area_px'),
            min_stroke_length_px=settings.get('min_stroke_length_px'),
            simplify_epsilon_px=settings.get('simplify_epsilon_px'),
            line_sensitivity=settings.get('line_sensitivity'),
            sketch_extraction_method=settings.get('sketch_extraction_method'),
            skeleton_prune_px=settings.get('skeleton_prune_px'),
            merge_gap_px=settings.get('merge_gap_px'),
            merge_max_angle_deg=settings.get('merge_max_angle_deg'),
            optimization_preset=settings.get('optimization_preset'),
            preview_geometry_mode=settings.get('preview_geometry_mode'),
            curve_tolerance_px=settings.get('curve_tolerance_px'),
            curve_tolerance_m=settings.get('curve_tolerance_m'),
            scale_percent=settings.get('scale_percent'),
            center_x_m=settings.get('center_x_m'),
            center_y_m=settings.get('center_y_m'),
            fit_to_safe_area=settings.get('fit_to_safe_area'),
            optimize_stroke_order=settings.get('optimize_stroke_order'),
            path_optimizer=settings.get('path_optimizer'),
            preserve_tiny_details=settings.get('preserve_tiny_details'),
            minimum_drawable_feature_m=settings.get('minimum_drawable_feature_m'),
            tiny_detail_candidate_max_feature_m=settings.get('tiny_detail_candidate_max_feature_m'),
            tiny_detail_expand_mode=settings.get('tiny_detail_expand_mode'),
            tiny_detail_max_expansions=settings.get('tiny_detail_max_expansions'),
            thin_line_min_width_mm=settings.get('thin_line_min_width_mm'),
            enable_face_handling=settings.get('enable_face_handling'),
            capture_all_details=settings.get('capture_all_details'),
            text_as_outline=settings.get('text_as_outline'),
            collect_svg=settings.get('collect_svg'),
            vectorization_method=settings.get('vectorization_method'),
            requested_input_type=requested_input_type,
            image_preprocess_mode='coloring_book',
            image_raw_print=True,
            image_target_resolution=settings.get('image_target_resolution'),
            image_force_solid_black_lines=settings.get('image_force_solid_black_lines'),
            image_photo_lineart_model=settings.get('image_photo_lineart_model'),
            image_nano_banana_prompt=settings.get('image_nano_banana_prompt'),
            image_google_api_key=settings.get('image_google_api_key'),
            curve_fit_time_limit_ms=settings.get('curve_fit_time_limit_ms'),
            autotrace_speckle_strength=settings.get('autotrace_speckle_strength'),
        )

    @app.post('/api/draw')
    @app.post('/api/preview/draw')
    async def draw_cached_preview(request: Request) -> JSONResponse:
        raw = await h._load_json_request(
            request,
            name='cached preview draw request',
            max_bytes=4096,
        )
        h._reject_extra_fields(raw, {'preview_id'}, 'cached preview draw request')
        if raw.get('preview_id') is None:
            raise HTTPException(status_code=400, detail='preview_id is required')
        entry = _load_preview(raw.get('preview_id'))
        return JSONResponse(_draw_cached_preview_response(entry))

    @app.delete('/api/preview/{preview_id}')
    async def clear_cached_preview(preview_id: str) -> JSONResponse:
        normalized_id = h._validate_preview_id(preview_id)
        removed = preview_cache.pop(normalized_id, None)
        if removed is None:
            raise HTTPException(status_code=404, detail='preview_id is unknown')
        return JSONResponse({'ok': True, 'preview_id': normalized_id, 'cleared': True})

    def _preview_settings(raw_settings: Any, *, name: str) -> dict[str, Any]:
        if raw_settings is None:
            return {}
        if not isinstance(raw_settings, dict):
            raise HTTPException(status_code=422, detail=f'{name}.settings must be an object')
        return dict(raw_settings)

    def _preview_json_builder_raw(raw: dict[str, Any], *, required_key: str) -> dict[str, Any]:
        settings = _preview_settings(raw.get('settings'), name='preview request')
        payload = {
            str(key): value
            for key, value in settings.items()
            if str(key) not in {'path_optimizer', 'optimize_stroke_order'}
        }
        payload[required_key] = raw.get(required_key)
        if raw.get('placement') is not None and 'placement' not in payload:
            payload['placement'] = raw.get('placement')
        return payload

    def _svg_upload_requested(upload: Any, content: bytes, requested_input_type: str) -> bool:
        if requested_input_type == 'svg':
            return True
        if requested_input_type not in {'auto', ''}:
            return False
        suffix = Path(getattr(upload, 'filename', '') or '').suffix.lower()
        normalized_type = str(getattr(upload, 'content_type', '') or '').split(';', 1)[0].strip().lower()
        stripped = content.lstrip()[:256].lower()
        return (
            suffix == '.svg'
            or normalized_type in {'image/svg+xml', 'application/svg+xml', 'text/svg+xml'}
            or stripped.startswith(b'<svg')
            or b'<svg' in stripped
        )

    def _json_text_preview_response(raw: dict[str, Any]) -> JSONResponse:
        settings = _preview_settings(raw.get('settings'), name='preview request')
        path_optimizer = _normalize_path_optimizer(settings.get('path_optimizer'))
        optimize_stroke_order = h._coerce_bool(
            settings.get('optimize_stroke_order'),
            field_name='preview request.settings.optimize_stroke_order',
            default=path_optimizer != 'none',
        )
        builder_raw = _preview_json_builder_raw(raw, required_key='text')
        placed_groups, placed_strokes, canonical_plan, placement_result, writable_bounds, commit_request, _, plan_preview, outside_safe_points, build_timings = _build_text_vector(
            builder_raw,
            request_name='preview text request',
        )
        # Compute the continuation cursor: the (X, Y) where the next text
        # draw should resume on the same final line, with a word-spacing gap
        # already added. If that point overflows the writable width, OR if
        # the remaining space is too narrow to fit even a short word, we
        # roll the cursor onto a brand-new line at the left margin. This
        # avoids the failure mode where a partial line of remaining space
        # would force the vectorizer's internal wrap to land at the same
        # X (because each wrap starts at cursor_x=0 in glyph-local space,
        # which translates back to text_start.x in board space) and stack
        # words on top of each other.
        next_cursor: dict[str, float] | None = None
        text_bottom_y: float | None = None
        preview_text_column = str(commit_request.get('text_column') or 'full')
        try:
            preview_text_column = h._normalize_text_column(preview_text_column)
        except ValueError:
            preview_text_column = 'full'
        if placed_groups:
            try:
                last_line_index = max(g.line_index for g in placed_groups)
                last_line_glyphs = [g for g in placed_groups if g.line_index == last_line_index]
                if last_line_glyphs:
                    glyph_height_m = float(commit_request.get('glyph_height_m') or 0.0)
                    safe_bounds_now = runtime.node.carriage_safe_safe_bounds()
                    text_column = preview_text_column
                    column_x_min, column_x_max = h._text_column_x_bounds(
                        safe_bounds_now,
                        text_column,
                    )
                    column_start_x = (
                        column_x_min + float(text_layout_defaults.left_margin)
                    )
                    line_x_max = (
                        column_x_max
                        if text_column != 'full'
                        else float(safe_bounds_now['x_max'])
                    )
                    # Word-spacing gap between successive draws: equivalent
                    # to one full glyph width (~0.65 × glyph_height) so the
                    # gap reads as a single space-bar press, not just a
                    # cramped letter-spacing gap.
                    word_gap_m = max(0.025, glyph_height_m * 0.65)
                    next_x = max(g.bbox.x_max for g in last_line_glyphs) + word_gap_m
                    last_line_top_y = min(g.bbox.y_min for g in last_line_glyphs)
                    # Threshold below which we treat the remaining line as
                    # "effectively full". Set to roughly 4 glyph widths so
                    # any reasonable next word fits at the left margin of
                    # a fresh line rather than getting squeezed into the
                    # tail of the current one.
                    min_useful_remaining_m = glyph_height_m * 4.0
                    remaining_m = line_x_max - next_x
                    line_overflows = (
                        next_x > line_x_max
                        or remaining_m < min_useful_remaining_m
                    )
                    if line_overflows:
                        next_x = column_start_x
                        # Use a generous line gap (1.6x glyph height) so the
                        # new line clears the descenders ('g', 'p', 'y') of
                        # the previous line. With glyph_height=86mm this
                        # gives a 138mm baseline-to-baseline distance.
                        line_height_setting = float(commit_request.get('line_height') or 1.75)
                        line_gap_m = glyph_height_m * line_height_setting
                        proposed_next_y = last_line_top_y + line_gap_m
                        wrap_floor_y = (
                            h._full_wrap_overlap_floor_y(runtime, line_gap_m)
                            if text_column == 'full'
                            else h._text_ink_floor_y(runtime, text_column, line_gap_m)
                        )
                        next_y = h._bump_row_top_below_ink_floor(
                            proposed_next_y,
                            wrap_floor_y,
                        )
                    else:
                        next_y = last_line_top_y
                    safe_max_y = (
                        safe_bounds_now['y_max']
                        - float(text_layout_defaults.bottom_margin)
                    )
                    if next_y >= safe_max_y:
                        next_cursor = None  # no room for another line
                    else:
                        next_cursor = {'x': float(next_x), 'y': float(next_y)}
            except (TypeError, ValueError):
                next_cursor = None
            try:
                h._note_text_column_bottom_from_groups(
                    runtime,
                    placed_groups,
                    text_column=preview_text_column,
                )
                text_bottom_y = h._text_bottom_y_from_groups(placed_groups)
            except (TypeError, ValueError):
                pass
        preview_start = time.perf_counter()
        preview_payload = h._preview_payload_from_strokes(
            placed_strokes,
            placement_result,
            outside_safe_points=outside_safe_points,
            normalized_plan=plan_preview,
            canonical_plan=canonical_plan,
            preview_sampling_policy=preview_sampling_policy,
            runtime_sampling_policy=runtime_sampling_policy,
        )
        build_timings['preview_sample_ms'] = _elapsed_ms(preview_start)
        build_timings['publish_ms'] = 0.0
        _record_last_plan_debug(
            source_type='text',
            canonical_plan=canonical_plan,
            preview_payload=preview_payload,
            timings=build_timings,
            committed=False,
        )
        preview_metadata: dict[str, Any] = {
            'text_column': preview_text_column,
            'text_next_cursor': next_cursor,
        }
        if text_bottom_y is not None:
            preview_metadata['text_bottom_y'] = float(text_bottom_y)
        preview_entry = _store_preview(
            source_type='text',
            canonical_plan=canonical_plan,
            preview_payload=preview_payload,
            commit_request=commit_request,
            input_type='text',
            pipeline_mode='text_vector',
            source_hash=h._content_hash({'text': builder_raw.get('text'), 'settings': commit_request}),
            settings={**commit_request, 'path_optimizer': path_optimizer, 'optimize_stroke_order': optimize_stroke_order},
            metadata=preview_metadata,
            optimize_stroke_order=optimize_stroke_order,
            path_optimizer=path_optimizer,
            writable_bounds=writable_bounds,
        )
        return JSONResponse(
            _attach_preview_contract(
                {
                    'ok': True,
                    'source_type': 'text',
                    'preview': preview_payload,
                    'preview_svg': preview_entry.execution_preview_svg,
                    'commit_request': commit_request,
                },
                preview_entry,
            )
        )

    def _json_svg_preview_response(raw: dict[str, Any], *, source_hash: str | None = None) -> JSONResponse:
        settings = _preview_settings(raw.get('settings'), name='preview request')
        path_optimizer = _normalize_path_optimizer(settings.get('path_optimizer'))
        optimize_stroke_order = h._coerce_bool(
            settings.get('optimize_stroke_order'),
            field_name='preview request.settings.optimize_stroke_order',
            default=path_optimizer != 'none',
        )
        builder_raw = _preview_json_builder_raw(raw, required_key='svg')
        placed_strokes, canonical_plan, placement_result, writable_bounds, commit_request, _, plan_preview, outside_safe_points, build_timings = _build_svg_vector(
            builder_raw,
            request_name='preview svg request',
        )
        preview_start = time.perf_counter()
        preview_payload = h._preview_payload_from_strokes(
            placed_strokes,
            placement_result,
            outside_safe_points=outside_safe_points,
            normalized_plan=plan_preview,
            canonical_plan=canonical_plan,
            preview_sampling_policy=preview_sampling_policy,
            runtime_sampling_policy=runtime_sampling_policy,
        )
        build_timings['preview_sample_ms'] = _elapsed_ms(preview_start)
        build_timings['publish_ms'] = 0.0
        _record_last_plan_debug(
            source_type='svg',
            canonical_plan=canonical_plan,
            preview_payload=preview_payload,
            timings=build_timings,
            committed=False,
        )
        _record_curve_fit_unavailable('svg')
        preview_entry = _store_preview(
            source_type='svg',
            canonical_plan=canonical_plan,
            preview_payload=preview_payload,
            commit_request=commit_request,
            input_type='svg',
            pipeline_mode='svg_vector',
            source_hash=source_hash or h._content_hash(builder_raw.get('svg')),
            settings={
                key: value
                for key, value in commit_request.items()
                if key != 'svg'
            } | {'path_optimizer': path_optimizer, 'optimize_stroke_order': optimize_stroke_order},
            optimize_stroke_order=optimize_stroke_order,
            path_optimizer=path_optimizer,
            writable_bounds=writable_bounds,
        )
        return JSONResponse(
            _attach_preview_contract(
                {
                    'ok': True,
                    'source_type': 'svg',
                    'preview': preview_payload,
                    'preview_svg': preview_entry.execution_preview_svg,
                    'commit_request': commit_request,
                },
                preview_entry,
            )
        )

    @app.post('/api/preview')
    async def generate_preview(request: Request) -> JSONResponse:
        content_type = str(request.headers.get('content-type') or '').lower()
        if 'multipart/form-data' in content_type:
            form = await request.form()
            upload = form.get('file')
            if upload is None or not hasattr(upload, 'read'):
                raise HTTPException(status_code=422, detail='preview file upload requires a file field')
            requested_input_type = str(form.get('input_type') or 'auto').strip().lower()
            if requested_input_type not in {'auto', 'sketch_image', 'sketch', 'svg'}:
                raise HTTPException(status_code=422, detail='input_type must be one of: auto, sketch_image, sketch, svg')
            settings_json = form.get('settings_json')
            if settings_json in (None, ''):
                settings = {}
            else:
                try:
                    settings = json.loads(str(settings_json))
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=422, detail=f'settings_json is invalid JSON: {exc}')
                settings = _preview_settings(settings, name='preview file request')
            form_settings = {
                str(key): value
                for key, value in form.items()
                if str(key) not in {'file', 'input_type', 'settings_json'}
            }
            settings = {**form_settings, **settings}
            content = await upload.read(_MAX_UPLOAD_BYTES + 1)
            if _svg_upload_requested(upload, content, requested_input_type):
                try:
                    svg_text = content.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise HTTPException(status_code=422, detail=f'preview SVG upload is not UTF-8: {exc}')
                return _json_svg_preview_response(
                    {
                        'input_type': 'svg',
                        'svg': svg_text,
                        'settings': settings,
                    },
                    source_hash=h._content_hash(content),
                )
            if requested_input_type == 'svg':
                raise HTTPException(status_code=422, detail='selected SVG input is not an SVG upload')
            try:
                await upload.seek(0)
            except AttributeError:
                upload.file.seek(0)
            return await preview_sketch_centerline(
                file=upload,
                margin_m=settings.get('margin_m'),
                max_image_dim=settings.get('max_image_dim'),
                min_component_area_px=settings.get('min_component_area_px'),
                min_stroke_length_px=settings.get('min_stroke_length_px'),
                simplify_epsilon_px=settings.get('simplify_epsilon_px'),
                line_sensitivity=settings.get('line_sensitivity'),
                sketch_extraction_method=settings.get('sketch_extraction_method'),
                skeleton_prune_px=settings.get('skeleton_prune_px'),
                merge_gap_px=settings.get('merge_gap_px'),
                merge_max_angle_deg=settings.get('merge_max_angle_deg'),
                optimization_preset=settings.get('optimization_preset'),
                preview_geometry_mode=settings.get('preview_geometry_mode'),
                curve_tolerance_px=settings.get('curve_tolerance_px'),
                curve_tolerance_m=settings.get('curve_tolerance_m'),
                scale_percent=settings.get('scale_percent'),
                center_x_m=settings.get('center_x_m'),
                center_y_m=settings.get('center_y_m'),
                fit_to_safe_area=settings.get('fit_to_safe_area'),
                optimize_stroke_order=settings.get('optimize_stroke_order'),
                path_optimizer=settings.get('path_optimizer'),
                preserve_tiny_details=settings.get('preserve_tiny_details'),
                minimum_drawable_feature_m=settings.get('minimum_drawable_feature_m'),
                tiny_detail_candidate_max_feature_m=settings.get('tiny_detail_candidate_max_feature_m'),
                tiny_detail_expand_mode=settings.get('tiny_detail_expand_mode'),
                tiny_detail_max_expansions=settings.get('tiny_detail_max_expansions'),
                thin_line_min_width_mm=settings.get('thin_line_min_width_mm'),
                enable_face_handling=settings.get('enable_face_handling'),
                capture_all_details=settings.get('capture_all_details'),
                text_as_outline=settings.get('text_as_outline'),
                collect_svg=settings.get('collect_svg'),
                vectorization_method=settings.get('vectorization_method'),
                requested_input_type=requested_input_type,
                image_preprocess_mode=settings.get('image_preprocess_mode'),
                image_raw_print=settings.get('image_raw_print'),
                image_target_resolution=settings.get('image_target_resolution'),
                image_force_solid_black_lines=settings.get('image_force_solid_black_lines'),
                image_photo_lineart_model=settings.get('image_photo_lineart_model'),
                image_nano_banana_prompt=settings.get('image_nano_banana_prompt'),
                image_google_api_key=settings.get('image_google_api_key'),
                curve_fit_time_limit_ms=settings.get('curve_fit_time_limit_ms'),
                autotrace_speckle_strength=settings.get('autotrace_speckle_strength'),
            )

        raw = await h._load_json_request(
            request,
            name='preview request',
            max_bytes=_MAX_VECTOR_REQUEST_BYTES,
        )
        h._reject_extra_fields(raw, {'input_type', 'text', 'svg', 'settings', 'placement'}, 'preview request')
        input_type = str(raw.get('input_type') or 'auto').strip().lower()
        if input_type == 'text':
            return await h._run_cpu_bound(_json_text_preview_response, raw)
        if input_type == 'svg':
            return await h._run_cpu_bound(_json_svg_preview_response, raw)
        raise HTTPException(status_code=422, detail='JSON preview input_type must be text or svg')

    @app.post('/api/mode')
    async def set_mode(request: Request) -> JSONResponse:
        raw = await h._load_json_request(
            request,
            name='mode request',
            max_bytes=1024,
        )
        h._reject_extra_fields(raw, {'mode'}, 'mode request')
        mode = raw.get('mode')
        if mode not in VALID_MODES:
            raise HTTPException(status_code=422, detail=f'mode must be one of {VALID_MODES}')
        snapshot = runtime.node.switch_mode(mode)
        # Switching out of text mode discards the continuation cursor so the
        # next time the user comes back to text mode they start at the top
        # of the board again.
        if mode != MODE_TEXT:
            runtime.reset_text_cursors()
        return JSONResponse({'ok': True, 'active_mode': snapshot['active_mode'], 'runtime': snapshot})

    @app.post('/api/emergency/stop')
    async def emergency_stop() -> JSONResponse:
        snapshot = runtime.node.emergency_stop()
        runtime.reset_text_cursors()
        return JSONResponse({
            'ok': True,
            'active_mode': snapshot['active_mode'],
            'executor_cancelled': True,
            'runtime': snapshot,
        })

    @app.post('/api/text/reset_cursor')
    async def reset_text_cursor(request: Request) -> JSONResponse:
        """Reset text continuation cursor(s). Optional body: {"column": "left", "clear_ink": true}."""
        column: str | None = None
        clear_ink = True
        if request.headers.get('content-length', '0') not in ('', '0'):
            raw = await h._load_json_request(
                request,
                name='text reset cursor request',
                max_bytes=1024,
            )
            if raw:
                h._reject_extra_fields(raw, {'column', 'clear_ink'}, 'text reset cursor request')
                if raw.get('column') is not None:
                    try:
                        column = h._normalize_text_column(raw.get('column'))
                    except ValueError as exc:
                        raise HTTPException(status_code=422, detail=str(exc)) from exc
                clear_ink = h._coerce_bool(
                    raw.get('clear_ink'),
                    field_name='text reset cursor request.clear_ink',
                    default=True,
                )
        runtime.reset_text_cursors(column, clear_ink=clear_ink)
        return JSONResponse({'ok': True, 'text_column': column, 'text_cursor': None, 'clear_ink': clear_ink})

    @app.post('/api/text/undo_last_write')
    async def undo_last_text_write(request: Request) -> JSONResponse:
        """Restore ink/cursor state from before the last text draw in a column."""
        column: str | None = None
        if request.headers.get('content-length', '0') not in ('', '0'):
            raw = await h._load_json_request(
                request,
                name='text undo last write request',
                max_bytes=1024,
            )
            if raw:
                h._reject_extra_fields(raw, {'column'}, 'text undo last write request')
                if raw.get('column') is not None:
                    try:
                        column = h._normalize_text_column(raw.get('column'))
                    except ValueError as exc:
                        raise HTTPException(status_code=422, detail=str(exc)) from exc
        restored = runtime.undo_last_text_write(column)
        if not restored:
            raise HTTPException(
                status_code=409,
                detail='No text write to undo for this column.',
            )
        normalized = h._normalize_text_column(column) if column is not None else 'full'
        cursor_x, cursor_y = runtime.get_text_cursor(normalized)
        return JSONResponse({
            'ok': True,
            'text_column': normalized,
            'restored': True,
            'text_cursor': (
                {'x': cursor_x, 'y': cursor_y}
                if cursor_x is not None and cursor_y is not None
                else None
            ),
        })

    @app.post('/api/manual/pen')
    async def set_manual_pen_mode(request: Request) -> JSONResponse:
        raw = await h._load_json_request(
            request,
            name='manual pen request',
            max_bytes=1024,
        )
        h._reject_extra_fields(raw, {'mode'}, 'manual pen request')
        mode = raw.get('mode')
        if mode not in VALID_MANUAL_PEN_MODES:
            raise HTTPException(
                status_code=422,
                detail=f'mode must be one of {VALID_MANUAL_PEN_MODES}',
            )
        snapshot = runtime.node.set_manual_pen_mode(mode)
        return JSONResponse({'ok': True, 'manual_pen_mode': mode, 'runtime': snapshot})

    def _elapsed_ms(start_time: float) -> float:
        return max(0.0, (time.perf_counter() - start_time) * 1000.0)

    def _record_last_plan_debug(
        *,
        source_type: str,
        canonical_plan: CanonicalPathPlan,
        preview_payload: dict[str, Any],
        timings: dict[str, float],
        optimizer_stats: dict[str, Any] | None = None,
        route_metadata: dict[str, Any] | None = None,
        transport: dict[str, Any] | None = None,
        committed: bool,
        command_metadata: tuple[dict[str, Any] | None, ...] | None = None,
    ) -> None:
        diagnostics = preview_payload.get('diagnostics') or {}
        runtime.record_last_plan_debug(
            {
                'available': True,
                'source_type': source_type,
                'committed': bool(committed),
                'transport': transport,
                'plan': canonical_plan_debug_payload(
                    canonical_plan,
                    sampling_policy=runtime_sampling_policy,
                    command_metadata=command_metadata,
                ),
                'optimizer_stats': optimizer_stats or {},
                'route_metadata': route_metadata or {},
                'preview_sampling': diagnostics.get('preview_sampling'),
                'runtime_sampling': diagnostics.get('runtime_sampling'),
                'parity': diagnostics.get('parity'),
                'point_budget': diagnostics.get('point_budget'),
                'timings_ms': {key: float(value) for key, value in timings.items()},
            }
        )

    def _record_last_curve_fit_debug(payload: dict[str, Any] | None) -> None:
        runtime.record_last_curve_fit_debug(payload or {'available': False})

    def _record_last_execution_debug(
        *,
        source_type: str,
        preview_payload: dict[str, Any],
        transport: dict[str, Any],
        timings: dict[str, float],
    ) -> None:
        diagnostics = preview_payload.get('diagnostics') or {}
        runtime.record_last_execution_debug(
            {
                'available': True,
                'source_type': source_type,
                'chosen_transport': transport.get('preferred_transport'),
                'published_transports': transport.get('published'),
                'transport_topics': transport.get('topics'),
                'preview_runtime_sampling': {
                    'preview': diagnostics.get('preview_sampling'),
                    'runtime': diagnostics.get('runtime_sampling'),
                    'parity': diagnostics.get('parity'),
                    'point_budget': diagnostics.get('point_budget'),
                },
                'timings_ms': {key: float(value) for key, value in timings.items()},
            }
        )

    def _build_text_vector(
        raw: dict[str, Any],
        *,
        request_name: str,
    ) -> tuple[
        tuple[TextGlyphOutline, ...],
        tuple[tuple[tuple[float, float], ...], ...],
        CanonicalPathPlan,
        Any,
        dict[str, float],
        dict[str, Any],
        PrimitivePathPlan,
        dict[str, Any],
        int,
        dict[str, float],
    ]:
        allowed = {
            'text',
            'placement',
            'font_source',
            'line_height',
            'column_seed_gap',
            'text_column',
            'curve_tolerance',
            'simplify_epsilon',
            'fit_padding',
        }
        h._reject_extra_fields(raw, allowed, request_name)
        text = h._validate_text_value(raw.get('text'), f'{request_name}.text')
        try:
            normalized_text = normalize_text_plan_input(
                text,
                decode_escaped_line_breaks=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'{request_name}.text invalid: {exc}')
        try:
            font_source = h._normalize_text_font_source(raw.get('font_source', 'relief_singleline'))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f'{request_name}.font_source invalid: {exc}',
            )
        default_line_height = 1.75
        line_height = h._coerce_float(
            raw.get('line_height', default_line_height),
            field_name=f'{request_name}.line_height',
            minimum=0.3,
            maximum=4.0,
        )
        default_column_seed_gap = 1.75
        column_seed_gap = h._coerce_float(
            raw.get('column_seed_gap', default_column_seed_gap),
            field_name=f'{request_name}.column_seed_gap',
            minimum=0.5,
            maximum=4.0,
        )
        try:
            text_column = h._normalize_text_column(raw.get('text_column', 'full'))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f'{request_name}.text_column invalid: {exc}',
            ) from exc
        curve_tolerance = h._coerce_float(
            raw.get('curve_tolerance', 0.008),
            field_name=f'{request_name}.curve_tolerance',
            minimum=0.0005,
            maximum=0.2,
        )
        simplify_epsilon = h._coerce_float(
            raw.get('simplify_epsilon', 0.0),
            field_name=f'{request_name}.simplify_epsilon',
            minimum=0.0,
            maximum=0.2,
        )
        fit_padding = h._coerce_float(
            raw.get('fit_padding', 0.9),
            field_name=f'{request_name}.fit_padding',
            minimum=0.1,
            maximum=1.0,
        )
        writable_bounds = runtime.node.carriage_safe_writable_bounds()
        safe_bounds = runtime.node.carriage_safe_safe_bounds()
        column_x_min, column_x_max = h._text_column_x_bounds(safe_bounds, text_column)
        column_start_x = column_x_min + float(text_layout_defaults.left_margin)
        line_origin_x = (
            column_start_x
            if text_column != 'full'
            else float(safe_bounds['x_min']) + float(text_layout_defaults.left_margin)
        )
        # Continuation cursor: previous text draw saved an (X, Y) where the
        # next text should resume on the same line, with a word-spacing gap
        # already added. The placement layer below wraps the new text onto
        # the next line when it would overflow the writable width.
        cursor_x, cursor_y = runtime.get_text_cursor(text_column)
        continuation_row_top_y: float | None = None
        seeded_first_row = False
        if cursor_x is not None and cursor_y is not None:
            continuation_row_top_y = float(cursor_y)
        placement_scale = 1.0
        raw_placement = raw.get('placement')
        if isinstance(raw_placement, dict) and raw_placement.get('scale') is not None:
            placement_scale = h._coerce_float(
                raw_placement.get('scale'),
                field_name=f'{request_name}.placement.scale',
                minimum=0.05,
                maximum=10.0,
            )
        glyph_scale_m_est = float(text_layout_defaults.glyph_height) * placement_scale
        line_gap_m_est = glyph_scale_m_est * line_height
        column_seed_gap_m = glyph_scale_m_est * column_seed_gap
        min_y = safe_bounds['y_min'] + float(text_layout_defaults.top_margin)
        safe_max_y = safe_bounds['y_max'] - float(text_layout_defaults.bottom_margin)
        if cursor_x is None and cursor_y is None:
            column_bottom = runtime.get_text_column_bottom_y(text_column)
            seeded_y: float | None = None
            if h._is_cross_mode_text_seed(
                runtime,
                text_column,
                has_column_bottom=column_bottom is not None,
            ):
                anchor_y = h._cross_mode_seed_anchor_y(
                    runtime,
                    text_column,
                    column_seed_gap_m=column_seed_gap_m,
                )
                if anchor_y is not None:
                    seeded_y = anchor_y
            elif text_column != 'full' and column_bottom is not None:
                seeded_y = float(column_bottom) + line_gap_m_est
            if seeded_y is not None:
                if seeded_y < min_y:
                    seeded_y = min_y
                if seeded_y < safe_max_y:
                    cursor_x = line_origin_x
                    cursor_y = seeded_y
                    seeded_first_row = True
        # When we have a live cursor we treat the UI's placement panel as
        # implicit defaults and override (x, y) with the cursor; the scale
        # stays as the user picked it. Without a cursor (first draw, or
        # after reset) we honour the placement payload as-is.
        use_continuation_cursor = cursor_x is not None and cursor_y is not None
        text_start = h._resolve_text_start_placement(
            raw.get('placement'),
            request_name=request_name,
            writable_bounds=writable_bounds,
            safe_bounds=safe_bounds,
            text_layout_defaults=text_layout_defaults,
            default_x_override=cursor_x,
            default_y_override=cursor_y,
            use_continuation_cursor=use_continuation_cursor,
        )
        line_x_max = column_x_max if text_column != 'full' else float(safe_bounds['x_max'])
        initial_cursor_x_em = 0.0
        if text_column != 'full' and not use_continuation_cursor:
            text_start = VectorPlacement(
                x=column_start_x,
                y=text_start.y,
                scale=text_start.scale,
            )
        glyph_scale_m = float(text_layout_defaults.glyph_height) * text_start.scale
        if glyph_scale_m <= 0.0:
            raise HTTPException(status_code=422, detail=f'{request_name}.placement.scale must be > 0')
        line_gap_m = glyph_scale_m * line_height
        if text_column == 'full':
            ink_floor_y = h._full_wrap_overlap_floor_y(runtime, line_gap_m)
        else:
            ink_floor_y = h._text_ink_floor_y(runtime, text_column, line_gap_m)
        if use_continuation_cursor:
            if text_start.x >= line_x_max:
                initial_cursor_x_em = 0.0
            else:
                initial_cursor_x_em = max(
                    0.0,
                    (float(text_start.x) - line_origin_x) / glyph_scale_m,
                )
            if initial_cursor_x_em <= 0.0 and continuation_row_top_y is not None and not seeded_first_row:
                continuation_row_top_y = h._bump_row_top_below_ink_floor(
                    continuation_row_top_y,
                    ink_floor_y,
                )
            text_start = VectorPlacement(
                x=line_origin_x,
                y=text_start.y,
                scale=text_start.scale,
            )
            available_width_m = line_x_max - line_origin_x
        else:
            available_width_m = line_x_max - text_start.x
        if available_width_m <= 0.0:
            raise HTTPException(
                status_code=422,
                detail=f'{request_name}.placement.x leaves no carriage-safe width for text',
            )
        max_line_width_units = available_width_m / glyph_scale_m
        if max_line_width_units <= 0.25:
            raise HTTPException(
                status_code=422,
                detail=f'{request_name}.placement.scale is too large for the remaining carriage-safe width',
            )
        build_timings: dict[str, float] = {}
        try:
            ingest_start = time.perf_counter()
            grouped_source = vectorize_text_grouped(
                normalized_text,
                font_source=font_source,
                line_height=line_height,
                curve_tolerance=curve_tolerance,
                simplify_epsilon=simplify_epsilon,
                max_line_width_units=max_line_width_units,
                initial_cursor_x_em=initial_cursor_x_em,
            )
            build_timings['ingest_ms'] = _elapsed_ms(ingest_start)
            source_bounds = h._grouped_text_bounds(grouped_source)
            board_width = writable_bounds['x_max'] - writable_bounds['x_min']
            board_height = writable_bounds['y_max'] - writable_bounds['y_min']
            fit_scale = min(
                (board_width * fit_padding) / max(source_bounds['width'], 1.0e-9),
                (board_height * fit_padding) / max(source_bounds['height'], 1.0e-9),
            )
            if fit_scale <= 0.0:
                raise ValueError('invalid fit scale for text placement')
            wrapped_shift = 0.0
            if use_continuation_cursor:
                row_top_y = (
                    continuation_row_top_y
                    if continuation_row_top_y is not None
                    else float(text_start.y)
                )
                placement_center_x = text_start.x + (
                    (source_bounds['x_min'] + 0.5 * source_bounds['width'])
                    * glyph_scale_m
                )
                placement_center_y = h._continuation_placement_center_y(
                    grouped_source,
                    row_top_y=row_top_y,
                    glyph_scale_m=glyph_scale_m,
                    grouped_fit_scale=fit_scale,
                    writable_bounds=writable_bounds,
                    fit_padding=fit_padding,
                )
                wrapped_shift = 0.0
                if text_column == 'full':
                    wrapped_shift = h._wrapped_lines_global_y_shift_amount(
                        grouped_source,
                        placement_center_y=placement_center_y,
                        glyph_scale_m=glyph_scale_m,
                        grouped_fit_scale=fit_scale,
                        writable_bounds=writable_bounds,
                        fit_padding=fit_padding,
                        floor_y=ink_floor_y,
                    )
                placement_center_y += wrapped_shift
            else:
                placement_center_x = text_start.x + (
                    0.5 * source_bounds['width'] * glyph_scale_m
                )
                placement_center_y = text_start.y + (
                    0.5 * source_bounds['height'] * glyph_scale_m
                )
            placement = VectorPlacement(
                x=placement_center_x,
                y=placement_center_y,
                scale=glyph_scale_m / fit_scale,
            )
            place_start = time.perf_counter()
            placed_groups, placement_result = place_grouped_text_on_board(
                grouped_source,
                writable_bounds=writable_bounds,
                placement=placement,
                fit_padding=fit_padding,
                text_upward_bias_em=0.0,
            )
            if use_continuation_cursor and wrapped_shift > 0.0:
                first_line_index = min(glyph.line_index for glyph in placed_groups)
                placed_groups = h._shift_placed_glyph_lines_y(
                    placed_groups,
                    line_index=first_line_index,
                    delta_y=-wrapped_shift,
                )
            build_timings['place_ms'] = _elapsed_ms(place_start)
            placed_strokes = tuple(
                stroke for glyph in placed_groups for stroke in glyph.strokes
            )
            canonical_plan = text_glyph_outlines_to_canonical_plan(
                placed_groups,
                theta_ref=draw_execution_defaults.fixed_draw_theta_rad,
            )
            build_timings['optimize_ms'] = 0.0
            runtime_export_start = time.perf_counter()
            primitive_plan_msg = h._build_execution_transport_message(
                canonical_plan,
                writable_bounds=writable_bounds,
                shared_config=shared,
                sampling_policy=runtime_sampling_policy,
            )
            build_timings['runtime_export_ms'] = _elapsed_ms(runtime_export_start)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'{request_name} failed: {exc}')
        placed_strokes = canonical_plan_to_draw_strokes(
            canonical_plan,
            sampling_policy=runtime_sampling_policy,
        )
        outside_safe_points = h._interpolated_outside_safe_workspace_count(
            placed_strokes,
            shared,
            step_m=h._sampling_validation_step_m(runtime_sampling_policy),
        )
        plan_preview = canonical_plan_to_legacy_strokes(
            canonical_plan,
            sampling_policy=preview_sampling_policy,
        )
        commit_request = {
            'text': normalized_text,
            'placement': {'x': text_start.x, 'y': text_start.y, 'scale': text_start.scale},
            'font_source': font_source,
            'glyph_height_m': glyph_scale_m,
            'line_height': line_height,
            'column_seed_gap': column_seed_gap,
            'text_column': text_column,
            'column_x_min': column_x_min,
            'column_x_max': column_x_max,
            'curve_tolerance': curve_tolerance,
            'simplify_epsilon': simplify_epsilon,
            'fit_padding': fit_padding,
        }
        return (
            placed_groups,
            placed_strokes,
            canonical_plan,
            placement_result,
            writable_bounds,
            commit_request,
            primitive_plan_msg,
            plan_preview,
            outside_safe_points,
            build_timings,
        )

    def _build_svg_vector(
        raw: dict[str, Any],
        *,
        request_name: str,
    ) -> tuple[
        tuple[tuple[tuple[float, float], ...], ...],
        CanonicalPathPlan,
        Any,
        dict[str, float],
        dict[str, Any],
        PrimitivePathPlan,
        dict[str, Any],
        int,
        dict[str, float],
    ]:
        allowed = {
            'svg',
            'placement',
            'curve_tolerance',
            'simplify_epsilon',
        }
        h._reject_extra_fields(raw, allowed, request_name)
        svg_payload = raw.get('svg')
        if not isinstance(svg_payload, str):
            raise HTTPException(status_code=422, detail=f'{request_name}.svg must be a string')
        if not svg_payload.strip():
            raise HTTPException(status_code=422, detail=f'{request_name}.svg must not be empty')
        if len(svg_payload.encode('utf-8')) > _MAX_SVG_BYTES:
            raise HTTPException(status_code=413, detail=f'{request_name}.svg exceeds max size')
        curve_tolerance = h._coerce_float(
            raw.get('curve_tolerance', 0.015),
            field_name=f'{request_name}.curve_tolerance',
            minimum=0.0005,
            maximum=0.2,
        )
        simplify_epsilon = h._coerce_float(
            raw.get('simplify_epsilon', 0.0),
            field_name=f'{request_name}.simplify_epsilon',
            minimum=0.0,
            maximum=2.0,
        )
        writable_bounds = runtime.node.carriage_safe_writable_bounds()
        build_timings: dict[str, float] = {}
        try:
            placement = normalize_placement(raw.get('placement'), writable_bounds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'{request_name}.placement invalid: {exc}')
        try:
            ingest_start = time.perf_counter()
            source_strokes = vectorize_svg(
                svg_payload,
                curve_tolerance=curve_tolerance,
                simplify_epsilon=simplify_epsilon,
            )
            source_plan = draw_strokes_to_canonical_plan(
                source_strokes,
                theta_ref=draw_execution_defaults.fixed_draw_theta_rad,
            )
            build_timings['ingest_ms'] = _elapsed_ms(ingest_start)
            place_start = time.perf_counter()
            placed_plan, placement_result = place_canonical_plan_on_board(
                source_plan,
                writable_bounds=writable_bounds,
                placement=placement,
                fit_margin_m=draw_execution_defaults.draw_scale_fit_margin_m,
            )
            build_timings['place_ms'] = _elapsed_ms(place_start)
            optimize_start = time.perf_counter()
            canonical_plan = cleanup_canonical_plan(
                placed_plan,
                simplify_tolerance_m=draw_execution_defaults.draw_path_simplify_tolerance_m,
            )
            canonical_plan = optimize_canonical_plan(
                canonical_plan,
                policy=svg_optimization_policy,
            ).plan
            build_timings['optimize_ms'] = _elapsed_ms(optimize_start)
            runtime_export_start = time.perf_counter()
            primitive_plan_msg = h._build_execution_transport_message(
                canonical_plan,
                writable_bounds=writable_bounds,
                shared_config=shared,
                sampling_policy=runtime_sampling_policy,
            )
            build_timings['runtime_export_ms'] = _elapsed_ms(runtime_export_start)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'{request_name} failed: {exc}')
        cleaned_strokes = canonical_plan_to_draw_strokes(
            canonical_plan,
            sampling_policy=runtime_sampling_policy,
        )
        outside_safe_points = h._interpolated_outside_safe_workspace_count(
            cleaned_strokes,
            shared,
            step_m=h._sampling_validation_step_m(runtime_sampling_policy),
        )
        plan_preview = canonical_plan_to_legacy_strokes(
            canonical_plan,
            sampling_policy=preview_sampling_policy,
        )
        commit_request = {
            'svg': svg_payload,
            'placement': {'x': placement.x, 'y': placement.y, 'scale': placement.scale},
            'curve_tolerance': curve_tolerance,
            'simplify_epsilon': simplify_epsilon,
        }
        return (
            cleaned_strokes,
            canonical_plan,
            placement_result,
            writable_bounds,
            commit_request,
            primitive_plan_msg,
            plan_preview,
            outside_safe_points,
            build_timings,
        )


    def _uploaded_svg_text(
        metadata: dict[str, Any],
        payload: bytes,
        *,
        request_name: str,
    ) -> str:
        try:
            upload_details = classify_uploaded_vector_file(
                metadata.get('original_filename'),
                metadata.get('normalized_content_type') or metadata.get('content_type'),
                payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'{request_name} failed: {exc}')
        if upload_details.source_type != 'svg' or upload_details.svg_text is None:
            raise HTTPException(status_code=422, detail=f'{request_name} failed: stored upload is not svg')
        return upload_details.svg_text

    def _record_curve_fit_unavailable(source_type: str) -> None:
        _record_last_curve_fit_debug({
            'available': False,
            'source_type': source_type,
        })

    def _uploaded_commit_request(upload_id: str, commit_request: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in commit_request.items()
            if key != 'svg'
        }
        payload['upload_id'] = upload_id
        return payload

    @app.post('/api/draw/plan')
    async def submit_draw_plan(request: Request) -> JSONResponse:
        raise HTTPException(
            status_code=409,
            detail='raw /api/draw/plan has been removed; use /api/preview then /api/draw with preview_id',
        )

    return app



def register_preview_routes(app: FastAPI, state: AppState) -> None:
    _register_preview_draw_routes(app, state)

def register_draw_routes(app: FastAPI, state: AppState) -> None:
    pass  # registered together with preview routes
