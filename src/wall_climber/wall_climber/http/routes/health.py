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
from wall_climber.canonical_adapters import canonical_plan_diagnostics
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


from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
    shared = state.shared
    text_layout_defaults = state.text_layout_defaults
    draw_execution_defaults = state.draw_execution_defaults
    preview_sampling_policy = state.preview_sampling_policy
    runtime_sampling_policy = state.runtime_sampling_policy
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

    @app.get('/api/health')
    async def health() -> JSONResponse:
        snapshot = runtime.node.runtime_snapshot()
        swinir_cached = swinir_weights_cached()
        return JSONResponse(
            {
                'ok': True,
                'ready': snapshot['ready'],
                'active_mode': snapshot['active_mode'],
                'observed_statuses': snapshot['observed_statuses'],
                'potrace_available': is_potrace_available(),
                'autotrace_available': is_autotrace_available(),
                'cuda_available': cuda_available(),
                'swinir_weights_cached': swinir_cached,
                'informative_weights_cached': informative_weights_cached(),
                'anilines_weights_cached': anilines_weights_cached(),
                'ai_preprocess_available': True,
                **_voice_stream.model_status(),
                **_web_ui_diagnostics(runtime.web_dir),
            }
        )

    @app.get('/api/runtime')
    async def runtime_state() -> JSONResponse:
        return JSONResponse(runtime.node.runtime_snapshot())

    @app.get('/api/debug/last-plan')
    async def last_plan_debug() -> JSONResponse:
        payload = runtime.last_plan_debug_snapshot()
        return JSONResponse(payload or {'available': False})

    @app.get('/api/debug/last-execution')
    async def last_execution_debug() -> JSONResponse:
        payload = runtime.last_execution_debug_snapshot() or {'available': False}
        payload = dict(payload)
        payload['executor'] = runtime.node.executor_diagnostics_snapshot()
        return JSONResponse(payload)

    @app.get('/api/debug/last-curve-fit')
    async def last_curve_fit_debug() -> JSONResponse:
        payload = runtime.last_curve_fit_debug_snapshot()
        return JSONResponse(payload or {'available': False})


