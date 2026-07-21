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
    @app.websocket('/api/voice/stream')
    async def voice_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        session: _voice_stream.WhisperVADSession | None = None
        try:
            await websocket.send_json({'type': 'status', 'message': 'Loading speech model…'})
            await asyncio.to_thread(_voice_stream.warmup_models)
            session = _voice_stream.create_stream_session()
        except _voice_stream.WhisperUnavailable as exc:
            await websocket.send_json({'type': 'error', 'message': str(exc)})
            await websocket.close(code=1011, reason='whisper unavailable')
            return

        await websocket.send_json({'type': 'ready'})
        try:
            while True:
                message = await websocket.receive()
                if message.get('type') == 'websocket.disconnect':
                    break
                data = message.get('bytes')
                if data:
                    try:
                        events = await asyncio.to_thread(session.feed_pcm, data)
                    except Exception as exc:
                        await websocket.send_json({'type': 'error', 'message': str(exc)})
                        continue
                    for event in events:
                        await websocket.send_json(event)
                    continue
                text = message.get('text')
                if text:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(payload, dict)
                        and payload.get('type') == 'control'
                        and payload.get('action') == 'reset'
                    ):
                        session.reset()
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                try:
                    flush_events = await asyncio.to_thread(session.flush)
                    for event in flush_events:
                        with contextlib.suppress(RuntimeError):
                            await websocket.send_json(event)
                finally:
                    session.close()


