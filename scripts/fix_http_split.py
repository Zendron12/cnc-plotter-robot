#!/usr/bin/env python3
"""Fix http package split: expose AppState attrs and wire route modules."""
from __future__ import annotations

import re
from pathlib import Path

HTTP = Path('/workspaces/ros2wsalt/src/wall_climber/wall_climber/http')
ROUTES = HTTP / 'routes'


def patch_app_state() -> None:
    text = (HTTP / 'app_state.py').read_text()
    if 'self.shared = shared' in text:
        return

    insert = '''
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

'''
    text = text.replace('\n\n    def bind(self, app: FastAPI)', insert + '\n    def bind(self, app: FastAPI)')
    # Add missing imports
    extra_imports = '''
from wall_climber.canonical_adapters import canonical_plan_to_primitive_path_plan
from wall_climber.canonical_tiny_details import expand_tiny_details_in_canonical_plan
from wall_climber.canonical_optimizer import optimize_canonical_plan
from wall_climber.image_pipeline.types import DrawingPathPlan
from wall_climber.optimizers import vpype_optimizer
'''
    if 'expand_tiny_details_in_canonical_plan' not in text:
        text = text.replace('from wall_climber.http import helpers as h\n', extra_imports + 'from wall_climber.http import helpers as h\n')
    (HTTP / 'app_state.py').write_text(text)


def rewrite_route_module(path: Path) -> None:
    text = path.read_text()
    # Standard register preamble
    preamble = '''def register_routes(app: FastAPI, state: AppState) -> None:
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
'''
    text = re.sub(
        r'def register_routes\(app: FastAPI, state: AppState\) -> None:\n(?:    .+\n)+?(?=\n    (?:@app\.|async def |def _))',
        preamble,
        text,
        count=1,
    )
    (path / path.name).write_text(text) if False else path.write_text(text)


def fix_web_server() -> None:
    pkg = HTTP.parent / 'web_server.py'
    pkg.write_text('''from __future__ import annotations

import threading
import webbrowser

import uvicorn

from wall_climber.http import create_app, BackendRuntime, WebBackendNode
from wall_climber.http.runtime import _ROS_IMPORT_ERROR, _web_ui_diagnostics, rclpy
from wall_climber.http import helpers as _helpers
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.port_utils import bind_listening_socket

# Backward-compatible re-exports for tests and monkeypatches.
for _name, _value in vars(_helpers).items():
    if not _name.startswith('__'):
        globals()[_name] = _value

''' + (HTTP.parent / 'web_server.py').read_text().split('def main')[1].join(['def main']))


def main() -> None:
    patch_app_state()
    for name in ('preview.py', 'draw.py', 'static.py', 'health.py', 'voice.py'):
        rewrite_route_module(ROUTES / name)

    # Fix web_server re-exports
    ws = HTTP.parent / 'web_server.py'
    main_part = ws.read_text().split('def main(args=None)', 1)[1]
    ws.write_text(
        '''from __future__ import annotations

import threading
import webbrowser

import uvicorn

from wall_climber.http import create_app, BackendRuntime, WebBackendNode
from wall_climber.http.runtime import _ROS_IMPORT_ERROR, _web_ui_diagnostics, rclpy
from wall_climber.http import helpers as _helpers
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.port_utils import bind_listening_socket

for _name, _value in vars(_helpers).items():
    if not _name.startswith('__'):
        globals()[_name] = _value


def main(args=None)'''
        + main_part
    )
    print('fixed')


if __name__ == '__main__':
    main()
