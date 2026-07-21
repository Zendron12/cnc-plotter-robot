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

    @app.get('/assets/{asset_path:path}')
    async def assets(asset_path: str) -> FileResponse:
        return FileResponse(_resolve_web_asset_path(runtime.web_dir, asset_path))

    @app.get('/vendor/{asset_path:path}')
    async def vendor_compat(asset_path: str) -> FileResponse:
        # Backward-compatible alias for older index.html versions.
        return FileResponse(
            _resolve_web_asset_path(runtime.web_dir, f'vendor/{asset_path}')
        )

    @app.get('/styles/{asset_path:path}')
    async def styles_compat(asset_path: str) -> FileResponse:
        return FileResponse(
            _resolve_web_asset_path(runtime.web_dir, f'styles/{asset_path}')
        )

    @app.get('/js/{asset_path:path}')
    async def js_compat(asset_path: str) -> FileResponse:
        return FileResponse(
            _resolve_web_asset_path(runtime.web_dir, f'js/{asset_path}')
        )

    @app.get('/')
    async def index() -> FileResponse:
        return FileResponse(
            runtime.web_dir / 'index.html',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
            },
        )

    @app.websocket('/rosbridge')
    async def rosbridge_proxy(websocket: WebSocket) -> None:
        await websocket.accept()
        if websockets is None:
            await websocket.close(code=1011, reason='websockets package unavailable')
            return

        try:
            upstream_url = f'ws://127.0.0.1:{runtime.node.rosbridge_port}'
            async with websockets.connect(upstream_url, max_size=None) as upstream:
                async def client_to_upstream() -> None:
                    try:
                        while True:
                            message = await websocket.receive()
                            if message.get('type') == 'websocket.disconnect':
                                break
                            text = message.get('text')
                            data = message.get('bytes')
                            if text is not None:
                                await upstream.send(text)
                            elif data is not None:
                                await upstream.send(data)
                    except WebSocketDisconnect:
                        pass
                    finally:
                        with contextlib.suppress(Exception):
                            await upstream.close()

                async def upstream_to_client() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                tasks = {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in pending:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                for task in done:
                    task.result()
        except Exception:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011, reason='rosbridge upstream unavailable')


