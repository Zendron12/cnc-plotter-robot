#!/usr/bin/env python3
"""Split wall_climber.web_server into wall_climber.http package."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path('/workspaces/ros2wsalt/src/wall_climber/wall_climber')
HTTP = PKG / 'http'
ROUTES = HTTP / 'routes'


def read_lines(path: Path) -> list[str]:
    return path.read_text().splitlines(keepends=True)


def join(lines: list[str], start: int, end: int) -> str:
    return ''.join(lines[start - 1 : end])


def main() -> None:
    ws = read_lines(PKG / 'web_server.py')
    ROUTES.mkdir(parents=True, exist_ok=True)

    # --- runtime.py ---
    runtime_imports = join(ws, 1, 190).replace(
        'from wall_climber.port_utils import bind_listening_socket\n', ''
    )
    runtime_body = join(ws, 192, 776) + join(ws, 2237, 2297)
    (HTTP / 'runtime.py').write_text(runtime_imports + runtime_body)

    # --- helpers.py ---
    helpers_imports = runtime_imports.replace(
        'import uvicorn\n', ''
    ).replace(
        'from fastapi.responses import FileResponse, JSONResponse, Response\n',
        'from fastapi.responses import JSONResponse, Response\n',
    )
    helpers_imports += (
        'from wall_climber.http.runtime import BackendRuntime, LineartCacheEntry, PreviewCacheEntry\n'
        'from wall_climber.http.runtime import _run_cpu_bound\n'
    )
    helpers_body = join(ws, 778, 2235)
    (HTTP / 'helpers.py').write_text(helpers_imports + helpers_body)

    create_app_src = join(ws, 2300, 4705)
    main_src = join(ws, 4708, 4756)

    # Identify route blocks by decorator lines
    create_lines = create_app_src.splitlines(keepends=True)
    route_markers = []
    for i, line in enumerate(create_lines):
        if re.match(r'\s+@app\.(get|post|put|delete|websocket)\(', line):
            route_markers.append(i)

    # Route ranges (start index in create_lines)
    static_start = route_markers[0]   # /assets
    voice_start = route_markers[4]    # /api/voice/stream (after rosbridge)
    health_start = route_markers[5]   # /api/health
    preview_fn_start = route_markers[9]  # preview_sketch_centerline (async def, no decorator yet - actually @app.get debug ends at 2931, preview_sketch at 2933)
    # Re-scan: markers at 2764,2768,2775,2786,2837,2890,2912,2916,2921,2928 then 3583...
    # preview_sketch_centerline is `async def` not @app - at line 2933 in file = index in create_app

    # Find preview_sketch_centerline
    preview_sketch_idx = next(
        i for i, l in enumerate(create_lines)
        if 'async def preview_sketch_centerline(' in l
    )
    draw_start = next(
        i for i, l in enumerate(create_lines)
        if "@app.post('/api/draw/live-stroke')" in l
    )
    mode_start = next(
        i for i, l in enumerate(create_lines)
        if "@app.post('/api/mode')" in l
    )
    draw_plan_start = next(
        i for i, l in enumerate(create_lines)
        if "@app.post('/api/draw/plan')" in l
    )

    setup_block = ''.join(create_lines[:route_markers[0]])
    static_block = ''.join(create_lines[route_markers[0]:route_markers[4]])
    voice_block = ''.join(create_lines[route_markers[4]:route_markers[5]])
    health_block = ''.join(create_lines[route_markers[5]:preview_sketch_idx])
    preview_block = ''.join(create_lines[preview_sketch_idx:draw_start])
    draw_block = ''.join(create_lines[draw_start:draw_plan_start])
    draw_plan_block = ''.join(create_lines[draw_plan_start:])

    helper_names = re.findall(r'^def (_\w+|settings_hash|canonical_plan_\w+)\(', helpers_body, re.M)
    helper_names += ['_run_cpu_bound']
    helper_names = sorted(set(helper_names), key=len, reverse=True)

    def prefix_helpers(block: str) -> str:
        for name in helper_names:
            block = re.sub(rf'(?<![.\w]){re.escape(name)}\(', f'h.{name}(', block)
            block = re.sub(rf'(?<![.\w]){re.escape(name)}(?=[,\s\)\]])', f'h.{name}', block)
        # Fix double prefix
        block = block.replace('h.h.', 'h.')
        return block

    def transform_setup(block: str) -> str:
        block = prefix_helpers(block)
        block = block.replace('def create_app(runtime: BackendRuntime) -> FastAPI:\n', '')
        block = block.replace('PreviewCacheEntry', 'PreviewCacheEntry')
        block = block.replace('_resolve_web_asset_path', '_resolve_web_asset_path')
        block = block.replace('_web_ui_diagnostics', '_web_ui_diagnostics')
        return block

    setup_transformed = transform_setup(setup_block)
    setup_transformed = setup_transformed.replace(
        'PreviewCacheEntry',
        'PreviewCacheEntry',
    )

    route_header = '''from __future__ import annotations

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

'''

    def make_route_module(name: str, register_body: str, extra_imports: str = '') -> str:
        body = prefix_helpers(register_body)
        body = body.replace('@app.', '@router.')
        return f'''{route_header}{extra_imports}
from wall_climber.http.app_state import AppState


def register_routes(router, state: AppState) -> None:
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
{body}
'''

    # app_state.py - setup as AppState.__init__ body
    state_init = setup_transformed.replace('    app = FastAPI', '    self.app = FastAPI')
    state_init = re.sub(r'^    ', '        ', state_init, flags=re.M)

    app_state_py = f'''{route_header}

class AppState:
    def __init__(self, runtime: BackendRuntime) -> None:
        self.runtime = runtime
{state_init}

    def bind(self, app: FastAPI) -> None:
        from wall_climber.http.routes import draw, health, preview, static, voice

        static.register_routes(app, self)
        voice.register_routes(app, self)
        health.register_routes(app, self)
        preview.register_routes(app, self)
        draw.register_routes(app, self)
'''
    (HTTP / 'app_state.py').write_text(app_state_py)

    static_reg = static_block.replace('@app.', '    @router.').replace('async def ', '    async def ')
    static_reg = re.sub(r'^    @router', '    @router', static_reg, flags=re.M)
    static_py = f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
{prefix_helpers(static_block.replace("@app.", "    @app."))}
'''
    # Fix static - use app directly for websockets on app
    static_body = prefix_helpers(static_block)
    (ROUTES / 'static.py').write_text(f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
{static_body}
''')

    voice_body = prefix_helpers(voice_block)
    (ROUTES / 'voice.py').write_text(f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
{voice_body}
''')

    health_body = prefix_helpers(health_block)
    (ROUTES / 'health.py').write_text(f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
{health_body}
''')

    preview_body = prefix_helpers(preview_block)
    (ROUTES / 'preview.py').write_text(f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
    shared = state.shared
    text_layout_defaults = state.text_layout_defaults
    preview_sampling_policy = state.preview_sampling_policy
    runtime_sampling_policy = state.runtime_sampling_policy
    preview_cache = state.preview_cache
    lineart_cache = state.lineart_cache
{preview_body}
''')

    draw_body = prefix_helpers(draw_block + draw_plan_block)
    (ROUTES / 'draw.py').write_text(f'''{route_header}
from wall_climber.http.app_state import AppState


def register_routes(app: FastAPI, state: AppState) -> None:
    runtime = state.runtime
    shared = state.shared
    text_layout_defaults = state.text_layout_defaults
    preview_sampling_policy = state.preview_sampling_policy
    runtime_sampling_policy = state.runtime_sampling_policy
    preview_cache = state.preview_cache
{draw_body}
''')

    (HTTP / 'app_factory.py').write_text(f'''{route_header}

def create_app(runtime: BackendRuntime) -> FastAPI:
    from wall_climber.http.app_state import AppState

    state = AppState(runtime)
    app = state.app
    state.bind(app)
    return app
''')

    (HTTP / '__init__.py').write_text(
        'from wall_climber.http.app_factory import create_app\n'
        'from wall_climber.http.runtime import BackendRuntime, WebBackendNode\n'
    )
    (ROUTES / '__init__.py').write_text('')

    thin = f'''from __future__ import annotations

import threading
import webbrowser

import uvicorn

from wall_climber.http import create_app, BackendRuntime, WebBackendNode
from wall_climber.http.runtime import _ROS_IMPORT_ERROR, _web_ui_diagnostics, rclpy
from wall_climber.http import helpers as _helpers
from wall_climber.http.helpers import *  # noqa: F403
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.port_utils import bind_listening_socket

{main_src}'''
    (PKG / 'web_server.py').write_text(thin)
    print('done', HTTP)


if __name__ == '__main__':
    main()
