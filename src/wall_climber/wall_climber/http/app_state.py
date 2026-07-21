from __future__ import annotations

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
    canonical_plan_to_draw_strokes,
    canonical_plan_to_primitive_path_plan,
)
from wall_climber.canonical_builders import draw_strokes_to_canonical_plan
from wall_climber.canonical_optimizer import CanonicalOptimizationPolicy, optimize_canonical_plan
from wall_climber.canonical_path import CanonicalPathPlan
from wall_climber.canonical_tiny_details import expand_tiny_details_in_canonical_plan
from wall_climber.image_pipeline.types import DrawingPathPlan
from wall_climber.optimizers import vpype_optimizer
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



class AppState:
    def __init__(self, runtime: BackendRuntime) -> None:
        self.runtime = runtime
        self.app = FastAPI(title='Four-Cable Drawing Robot UI Backend', version='1.0.0')
        shared = load_shared_config()
        text_layout_defaults = shared.text_layout
        draw_execution_defaults = shared.draw_execution
        preview_sampling_policy = h._preview_sampling_policy(shared)
        runtime_sampling_policy = h._runtime_sampling_policy(shared)
        svg_optimization_policy = h._draw_optimization_policy(
            shared,
            label='svg',
            reorder_units=True,
            fit_arcs=True,
        )
        sketch_draw_optimization_policy = h._sketch_draw_optimization_policy(shared)
        preview_cache: TTLCache[PreviewCacheEntry] = TTLCache(
            max_entries=int(_PREVIEW_CACHE_MAX_ENTRIES),
            ttl_seconds=float(_PREVIEW_CACHE_TTL_SECONDS),
        )
        lineart_cache: TTLCache[LineartCacheEntry] = TTLCache(
            max_entries=int(_LINEART_CACHE_MAX_ENTRIES),
            ttl_seconds=float(_LINEART_CACHE_TTL_SECONDS),
        )
        def _is_sketch_source_type(source_type: str) -> bool:
            return str(source_type) in {'sketch_centerline', 'sketch_image'}

        def _preview_optimization_policy(source_type: str) -> CanonicalOptimizationPolicy:
            if _is_sketch_source_type(source_type):
                return sketch_draw_optimization_policy
            if source_type == 'svg':
                return svg_optimization_policy
            return h._draw_optimization_policy(
                shared,
                label=f'{source_type}_preview',
                reorder_units=True,
                fit_arcs=False,
            )

        def _preview_allowed_modes(source_type: str) -> tuple[str, ...]:
            return (MODE_TEXT,) if source_type == 'text' else (MODE_DRAW,)

        def _carriage_safe_writable_bounds_for_sketch() -> dict[str, float]:
            try:
                writable_bounds = runtime.node.carriage_safe_writable_bounds()
            except Exception:
                writable_bounds = shared.carriage_safe_writable_bounds()
            try:
                safe_workspace_bounds = runtime.node.carriage_safe_safe_bounds()
            except Exception:
                safe_workspace_bounds = shared.carriage_safe_workspace_bounds()
            bounds = {
                'x_min': max(float(writable_bounds['x_min']), float(safe_workspace_bounds['x_min'])),
                'x_max': min(float(writable_bounds['x_max']), float(safe_workspace_bounds['x_max'])),
                'y_min': max(float(writable_bounds['y_min']), float(safe_workspace_bounds['y_min'])),
                'y_max': min(float(writable_bounds['y_max']), float(safe_workspace_bounds['y_max'])),
            }
            return h._bounds_payload(bounds)

        def _board_bounds_for_sketch() -> dict[str, float]:
            return {
                'x_min': 0.0,
                'x_max': float(shared.board.width),
                'y_min': 0.0,
                'y_max': float(shared.board.height),
                'width': float(shared.board.width),
                'height': float(shared.board.height),
            }

        def _preview_writable_bounds_for_source(source_type: str) -> dict[str, float]:
            if _is_sketch_source_type(source_type):
                return _carriage_safe_writable_bounds_for_sketch()
            return runtime.node.carriage_safe_writable_bounds()

        def _normalize_path_optimizer(value: Any, *, field_name: str = 'path_optimizer') -> str:
            optimizer = str('internal' if value in (None, '') else value).strip().lower()
            if optimizer in {'off', 'false', 'disabled'}:
                optimizer = 'none'
            if optimizer not in {'internal', 'vpype', 'none'}:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field_name} must be one of: internal, vpype, none",
                )
            return optimizer

        def _tiny_detail_policy_for_preview(
            source_type: str,
            settings_payload: dict[str, Any],
        ) -> dict[str, Any]:
            raw_settings = settings_payload.get('settings')
            settings = raw_settings if isinstance(raw_settings, dict) else {}
            def setting_value(key: str, default: Any) -> Any:
                value = settings.get(key, default)
                return default if value is None else value

            eligible = _is_sketch_source_type(str(source_type))
            if eligible:
                preserve = h._coerce_bool(
                    settings.get('preserve_tiny_details'),
                    field_name='preserve_tiny_details',
                    default=False,
                )
            else:
                preserve = False
            runtime_draw_step = runtime_sampling_policy.draw_step_m
            default_min_feature = max(
                0.0035,
                float(runtime_draw_step) * 1.5 if runtime_draw_step is not None else 0.0045,
            )
            minimum_feature = h._coerce_float(
                setting_value('minimum_drawable_feature_m', default_min_feature),
                field_name='minimum_drawable_feature_m',
                minimum=0.0005,
                maximum=0.03,
            )
            candidate_max = h._coerce_float(
                setting_value('tiny_detail_candidate_max_feature_m', minimum_feature * 0.75),
                field_name='tiny_detail_candidate_max_feature_m',
                minimum=0.0001,
                maximum=0.03,
            )
            expand_mode = str(setting_value('tiny_detail_expand_mode', 'micro_cross')).strip().lower()
            if expand_mode not in {'micro_cross', 'micro_loop'}:
                raise HTTPException(
                    status_code=422,
                    detail="tiny_detail_expand_mode must be 'micro_cross' or 'micro_loop'",
                )
            max_expansions = h._coerce_int(
                setting_value('tiny_detail_max_expansions', 512),
                field_name='tiny_detail_max_expansions',
                minimum=0,
                maximum=10_000,
            )
            context_radius = h._coerce_float(
                setting_value('tiny_detail_context_radius_m', 0.08),
                field_name='tiny_detail_context_radius_m',
                minimum=0.0,
                maximum=0.5,
            )
            return {
                'eligible': bool(eligible),
                'preserve_tiny_details': bool(preserve),
                'minimum_drawable_feature_m': float(minimum_feature),
                'tiny_detail_candidate_max_feature_m': float(candidate_max),
                'tiny_detail_expand_mode': expand_mode,
                'tiny_detail_max_expansions': int(max_expansions),
                'tiny_detail_context_radius_m': float(context_radius),
            }

        def _build_executable_preview_payload(
            canonical_plan: CanonicalPathPlan,
            *,
            source_type: str,
            settings_payload: dict[str, Any],
            writable_bounds: dict[str, float],
            optimize_stroke_order: bool,
            path_optimizer: str = 'internal',
            existing_optimizer_stats: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            executable_plan = canonical_plan
            path_optimizer = _normalize_path_optimizer(path_optimizer)
            tiny_detail_policy = _tiny_detail_policy_for_preview(source_type, settings_payload)
            effective_settings_payload = dict(settings_payload)
            effective_settings_payload['tiny_detail_policy'] = tiny_detail_policy
            effective_settings_payload['path_optimizer'] = path_optimizer
            tiny_detail_metrics = {
                'preserve_tiny_details': bool(tiny_detail_policy['preserve_tiny_details']),
                'tiny_detail_expand_mode': tiny_detail_policy['tiny_detail_expand_mode'],
                'minimum_drawable_feature_m': float(tiny_detail_policy['minimum_drawable_feature_m']),
                'tiny_detail_candidate_max_feature_m': float(
                    tiny_detail_policy['tiny_detail_candidate_max_feature_m']
                ),
                'tiny_detail_max_expansions': int(tiny_detail_policy['tiny_detail_max_expansions']),
                'tiny_detail_context_radius_m': float(tiny_detail_policy['tiny_detail_context_radius_m']),
                'tiny_details_detected': 0,
                'tiny_details_preserved': 0,
                'tiny_details_expanded': 0,
                'tiny_details_skipped_by_limit': 0,
                'tiny_details_skipped_as_isolated': 0,
                'tiny_details_expansion_added_commands': 0,
            }
            if bool(tiny_detail_policy['preserve_tiny_details']):
                try:
                    tiny_detail_result = expand_tiny_details_in_canonical_plan(
                        executable_plan,
                        preserve=True,
                        minimum_drawable_feature_m=float(tiny_detail_policy['minimum_drawable_feature_m']),
                        candidate_max_feature_m=float(
                            tiny_detail_policy['tiny_detail_candidate_max_feature_m']
                        ),
                        expand_mode=str(tiny_detail_policy['tiny_detail_expand_mode']),
                        max_expansions=int(tiny_detail_policy['tiny_detail_max_expansions']),
                        context_radius_m=float(tiny_detail_policy['tiny_detail_context_radius_m']),
                        bounds=writable_bounds,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                executable_plan = tiny_detail_result.plan
                tiny_detail_metrics = dict(tiny_detail_result.metrics)
            optimization_stats = dict(existing_optimizer_stats or {})
            optimization_ms = 0.0
            pre_optimization_sampled_paths = None
            optimizer_warnings: list[str] = []
            optimizer_available = True
            optimizer_used = 'none'
            if optimize_stroke_order and path_optimizer != 'none':
                pre_optimization_sampled_paths = h._validated_runtime_sampled_paths(
                    executable_plan,
                    writable_bounds=writable_bounds,
                    shared_config=shared,
                    sampling_policy=runtime_sampling_policy,
                )
                optimization_started = time.perf_counter()
                if path_optimizer == 'vpype':
                    vpype_plan, vpype_metadata = vpype_optimizer.optimize_with_vpype(executable_plan)
                    optimizer_available = bool(vpype_metadata.get('available'))
                    optimizer_warnings.extend(str(item) for item in vpype_metadata.get('warnings') or ())
                    if vpype_plan is not None:
                        executable_plan = vpype_plan
                        optimization_stats = dict(vpype_metadata)
                        optimizer_used = 'vpype'
                    else:
                        internal_result = optimize_canonical_plan(
                            executable_plan,
                            policy=_preview_optimization_policy(source_type),
                        )
                        executable_plan = internal_result.plan
                        optimization_stats = {
                            'fallback_from': 'vpype',
                            'vpype': dict(vpype_metadata),
                            'internal': internal_result.stats.to_dict(),
                        }
                        optimizer_used = 'internal'
                else:
                    optimization_result = optimize_canonical_plan(
                        executable_plan,
                        policy=_preview_optimization_policy(source_type),
                    )
                    executable_plan = optimization_result.plan
                    optimization_stats = optimization_result.stats.to_dict()
                    optimizer_used = 'internal'
                optimization_ms = max(0.0, (time.perf_counter() - optimization_started) * 1000.0)

            sampled_paths = h._validated_runtime_sampled_paths(
                executable_plan,
                writable_bounds=writable_bounds,
                shared_config=shared,
                sampling_policy=runtime_sampling_policy,
            )
            primitive_descriptor = canonical_plan_to_primitive_path_plan(executable_plan)
            primitive_plan = h._primitive_path_plan_message_from_descriptor(primitive_descriptor)
            primitive_hash = h._stable_hash(primitive_descriptor)
            execution_payload = h._sampled_paths_stable_payload(sampled_paths)
            execution_hash = h._stable_hash(execution_payload)
            cpp_available = getattr(_canonical_adapters, '_geometry_cpp', None) is not None
            draw_path_count = sum(1 for sampled in sampled_paths if sampled.draw)
            travel_path_count = sum(1 for sampled in sampled_paths if not sampled.draw)
            draw_sample_count = sum(len(sampled.points) for sampled in sampled_paths if sampled.draw)
            travel_sample_count = sum(len(sampled.points) for sampled in sampled_paths if not sampled.draw)
            canonical_geometry = h._canonical_geometry_metrics(executable_plan)
            executable_geometry = h._executable_geometry_metrics(sampled_paths)
            source_metadata = dict(getattr(canonical_plan, 'metadata', {}) or {})
            color_lineart_metrics = dict(source_metadata.get('color_lineart') or {})
            travel_before_m = None
            path_count_before = None
            if pre_optimization_sampled_paths is not None:
                travel_before_m = h._sampled_paths_length(pre_optimization_sampled_paths, draw=False)
                path_count_before = sum(1 for sampled in pre_optimization_sampled_paths if sampled.draw)
            travel_after_m = h._sampled_paths_length(sampled_paths, draw=False)
            path_count_after = int(draw_path_count)
            optimizer_metrics = {
                'name': optimizer_used,
                'requested': path_optimizer,
                'used': optimizer_used,
                'available': bool(optimizer_available),
                'warnings': tuple(optimizer_warnings),
                'travel_before_m': travel_before_m,
                'travel_after_m': travel_after_m,
                'path_count_before': path_count_before,
                'path_count_after': path_count_after,
            }
            return {
                'executable_canonical_plan': executable_plan,
                'executable_canonical_hash': h.canonical_plan_hash(executable_plan),
                'primitive_descriptor': primitive_descriptor,
                'primitive_plan': primitive_plan,
                'primitive_hash': primitive_hash,
                'execution_preview_svg': h._execution_preview_svg_from_sampled_paths(
                    sampled_paths,
                    board_width_m=float(shared.board.width),
                    board_height_m=float(shared.board.height),
                    pen_tip_radius_m=float(shared.pen.tip_radius),
                ),
                'execution_hash': execution_hash,
                'settings_hash': h.settings_hash(effective_settings_payload),
                'metrics': {
                    'execution_preview_source': 'cpp_geometry_binding' if cpp_available else 'python_runtime_sampling',
                    'cpp_exact_preview': bool(cpp_available),
                    **tiny_detail_metrics,
                    'optimized': bool(optimize_stroke_order),
                    'optimization': optimization_stats,
                    'optimization_ms': float(optimization_ms),
                    'optimizer': optimizer_metrics,
                    'canonical_command_count': int(len(canonical_plan.commands)),
                    'executable_canonical_command_count': int(len(executable_plan.commands)),
                    'canonical_geometry': canonical_geometry,
                    'executable_geometry': executable_geometry,
                    'primitive_count': int(len(primitive_descriptor.get('primitives') or ())),
                    'draw_path_count': int(draw_path_count),
                    'travel_path_count': int(travel_path_count),
                    'draw_sample_count': int(draw_sample_count),
                    'travel_sample_count': int(travel_sample_count),
                    'draw_length_m': h._sampled_paths_length(sampled_paths, draw=True),
                    'travel_length_m': h._sampled_paths_length(sampled_paths, draw=False),
                    'bounds': h._sampled_paths_bounds(sampled_paths),
                    'runtime_sampling_policy': h._stable_payload(runtime_sampling_policy),
                    'color_lineart': color_lineart_metrics,
                },
            }

        def _preview_cache_expired(entry: PreviewCacheEntry, *, now: float) -> bool:
            return preview_cache.is_expired(entry, now=now)

        def _cleanup_preview_cache(*, now: float | None = None) -> None:
            preview_cache.prune(now=now)

        def _store_preview(
            *,
            preview_id: str | None = None,
            source_type: str,
            canonical_plan: CanonicalPathPlan,
            preview_payload: dict[str, Any],
            commit_request: dict[str, Any] | None,
            input_type: str,
            pipeline_mode: str,
            source_hash: str | None = None,
            settings: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None,
            warnings: tuple[str, ...] = (),
            source_filename: str = '',
            drawing_plan: DrawingPathPlan | None = None,
            command_metadata: tuple[dict[str, Any] | None, ...] | None = None,
            optimizer_stats: dict[str, Any] | None = None,
            route_metadata: dict[str, Any] | None = None,
            curve_fit_payload: dict[str, Any] | None = None,
            optimize_stroke_order: bool = False,
            path_optimizer: str = 'internal',
            writable_bounds: dict[str, float] | None = None,
        ) -> PreviewCacheEntry:
            _cleanup_preview_cache()
            normalized_id = uuid.uuid4().hex if preview_id is None else h._validate_preview_id(preview_id)
            canonical_hash = h.canonical_plan_hash(canonical_plan)
            normalized_path_optimizer = _normalize_path_optimizer(path_optimizer)
            geometry_settings = {
                'source_type': str(source_type),
                'input_type': str(input_type),
                'pipeline_mode': str(pipeline_mode),
                'settings': dict(settings or {}),
                'optimize_stroke_order': bool(optimize_stroke_order),
                'path_optimizer': normalized_path_optimizer,
            }
            executable_payload = _build_executable_preview_payload(
                canonical_plan,
                source_type=str(source_type),
                settings_payload=geometry_settings,
                writable_bounds=writable_bounds or _preview_writable_bounds_for_source(str(source_type)),
                optimize_stroke_order=bool(optimize_stroke_order),
                path_optimizer=normalized_path_optimizer,
                existing_optimizer_stats=optimizer_stats,
            )
            enriched_preview = dict(preview_payload)
            enriched_preview['canonical_hash'] = canonical_hash
            enriched_preview['executable_canonical_hash'] = executable_payload['executable_canonical_hash']
            enriched_preview['primitive_hash'] = executable_payload['primitive_hash']
            enriched_preview['execution_hash'] = executable_payload['execution_hash']
            enriched_preview['settings_hash'] = executable_payload['settings_hash']
            enriched_preview['preview_id'] = normalized_id
            enriched_commit_request = dict(commit_request or {})
            enriched_commit_request['preview_id'] = normalized_id
            entry = PreviewCacheEntry(
                preview_id=normalized_id,
                source_type=str(source_type),
                canonical_plan=canonical_plan,
                canonical_hash=canonical_hash,
                executable_canonical_plan=executable_payload['executable_canonical_plan'],
                executable_canonical_hash=executable_payload['executable_canonical_hash'],
                primitive_descriptor=executable_payload['primitive_descriptor'],
                primitive_plan=executable_payload['primitive_plan'],
                primitive_hash=executable_payload['primitive_hash'],
                execution_preview_svg=executable_payload['execution_preview_svg'],
                execution_hash=executable_payload['execution_hash'],
                settings_hash=executable_payload['settings_hash'],
                metrics=dict(executable_payload['metrics']),
                preview_payload=enriched_preview,
                commit_request=enriched_commit_request,
                created_at_unix=time.time(),
                input_type=str(input_type),
                pipeline_mode=str(pipeline_mode),
                source_hash=source_hash,
                settings=dict(settings or {}),
                metadata=dict(metadata or {}),
                warnings=tuple(str(item) for item in warnings),
                source_filename=str(source_filename or ''),
                drawing_plan=drawing_plan,
                command_metadata=command_metadata,
                optimizer_stats=dict(optimizer_stats or {}),
                route_metadata=dict(route_metadata or {}),
                curve_fit_payload=dict(curve_fit_payload or {}),
            )
            preview_cache.store(normalized_id, entry)
            return entry

        def _load_preview(preview_id: Any) -> PreviewCacheEntry:
            from wall_climber import web_server

            normalized_id = h._validate_preview_id(preview_id)
            entry = preview_cache.entries().get(normalized_id)
            if entry is None:
                raise HTTPException(status_code=404, detail='preview_id is unknown')
            age_s = time.time() - float(entry.created_at_unix)
            if age_s > float(web_server._PREVIEW_CACHE_TTL_SECONDS):
                preview_cache.pop(normalized_id, None)
                raise HTTPException(status_code=410, detail='preview_id has expired')
            preview_cache.entries().move_to_end(normalized_id)
            return entry

        def _preview_contract_payload(entry: PreviewCacheEntry) -> dict[str, Any]:
            from wall_climber import web_server

            expires_at_unix = float(entry.created_at_unix) + float(web_server._PREVIEW_CACHE_TTL_SECONDS)
            return {
                'preview_id': entry.preview_id,
                'canonical_hash': entry.canonical_hash,
                'executable_canonical_hash': entry.executable_canonical_hash,
                'primitive_hash': entry.primitive_hash,
                'execution_hash': entry.execution_hash,
                'settings_hash': entry.settings_hash,
                'execution_preview_svg': entry.execution_preview_svg,
                'metrics': dict(entry.metrics),
                'source_type': entry.source_type,
                'input_type': entry.input_type,
                'pipeline_mode': entry.pipeline_mode,
                'source_hash': entry.source_hash,
                'created_at_unix': float(entry.created_at_unix),
                'expires_at_unix': expires_at_unix,
                'ttl_seconds': max(0.0, expires_at_unix - time.time()),
            }

        def _attach_preview_contract(payload: dict[str, Any], entry: PreviewCacheEntry) -> dict[str, Any]:
            enriched = dict(payload)
            existing_metrics = dict(enriched.get('metrics') or {})
            contract = _preview_contract_payload(entry)
            contract_metrics = dict(contract.get('metrics') or {})
            existing_metrics.update(contract_metrics)
            contract['metrics'] = existing_metrics
            enriched.update(contract)
            enriched['preview'] = dict(enriched.get('preview') or {})
            enriched['preview']['canonical_hash'] = entry.canonical_hash
            enriched['preview']['executable_canonical_hash'] = entry.executable_canonical_hash
            enriched['preview']['primitive_hash'] = entry.primitive_hash
            enriched['preview']['execution_hash'] = entry.execution_hash
            enriched['preview']['settings_hash'] = entry.settings_hash
            enriched['preview']['preview_id'] = entry.preview_id
            enriched['preview']['execution_preview_svg'] = entry.execution_preview_svg
            enriched['commit_request'] = dict(enriched.get('commit_request') or entry.commit_request)
            enriched['commit_request']['preview_id'] = entry.preview_id
            enriched['draw_request'] = dict(enriched.get('draw_request') or enriched['commit_request'])
            enriched['draw_request']['preview_id'] = entry.preview_id
            return enriched


        self.shared = shared
        self.text_layout_defaults = text_layout_defaults
        self.draw_execution_defaults = draw_execution_defaults
        self.preview_sampling_policy = preview_sampling_policy
        self.runtime_sampling_policy = runtime_sampling_policy
        self.svg_optimization_policy = svg_optimization_policy
        self.sketch_draw_optimization_policy = sketch_draw_optimization_policy
        self.preview_cache = preview_cache
        self.lineart_cache = lineart_cache
        self._is_sketch_source_type = _is_sketch_source_type
        self._preview_optimization_policy = _preview_optimization_policy
        self._preview_allowed_modes = _preview_allowed_modes
        self._carriage_safe_writable_bounds_for_sketch = _carriage_safe_writable_bounds_for_sketch
        self._board_bounds_for_sketch = _board_bounds_for_sketch
        self._preview_writable_bounds_for_source = _preview_writable_bounds_for_source
        self._normalize_path_optimizer = _normalize_path_optimizer
        self._tiny_detail_policy_for_preview = _tiny_detail_policy_for_preview
        self._build_executable_preview_payload = _build_executable_preview_payload
        self._preview_cache_expired = _preview_cache_expired
        self._cleanup_preview_cache = _cleanup_preview_cache
        self._store_preview = _store_preview
        self._load_preview = _load_preview
        self._preview_contract_payload = _preview_contract_payload
        self._attach_preview_contract = _attach_preview_contract


    def bind(self, app: FastAPI) -> None:
        from wall_climber.http.routes import draw, health, preview, static, voice

        static.register_routes(app, self)
        voice.register_routes(app, self)
        health.register_routes(app, self)
        preview.register_routes(app, self)
