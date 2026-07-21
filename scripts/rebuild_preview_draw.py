#!/usr/bin/env python3
"""Rebuild preview/draw routes as a single coherent module."""
from __future__ import annotations

from pathlib import Path

HTTP = Path('/workspaces/ros2wsalt/src/wall_climber/wall_climber/http')
ROUTES = HTTP / 'routes'

LIVE_STROKE = '''
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
        encoded_payload = h._normalize_stroke_payload(raw, writable_bounds)
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

'''

HEADER = Path(ROUTES / 'draw.py').read_text().split('def register_routes')[0]

PREAMBLE = '''def _register_preview_draw_routes(app: FastAPI, state: AppState) -> None:
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


def extract_body(path: Path) -> str:
    text = path.read_text()
    start = text.index('def register_routes')
    body = text[start:]
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    skipping = True
    for line in lines[1:]:
        if skipping:
            if line.strip().startswith('@app.') or line.strip().startswith('async def ') or (
                line.strip().startswith('def ') and not line.strip().startswith('def register_')
            ):
                skipping = False
                out.append(line)
            continue
        out.append(line)
    return ''.join(out)


def main() -> None:
    preview_body = extract_body(ROUTES / 'preview.py')
    draw_body = extract_body(ROUTES / 'draw.py')

    # Fix MAX_DRAW_PLAN_BYTES reference in live stroke - already uses _MAX_DRAW_PLAN_BYTES
    live = LIVE_STROKE

    header = HEADER.replace(
        'from wall_climber.http.runtime import (',
        'from wall_climber.http.runtime import (\n    _MAX_DRAW_PLAN_BYTES,',
    )
    combined = (
        header
        + PREAMBLE
        + preview_body
        + live
        + draw_body
        + '\n\n'
        + 'def register_preview_routes(app: FastAPI, state: AppState) -> None:\n'
        + '    _register_preview_draw_routes(app, state)\n\n'
        + 'def register_draw_routes(app: FastAPI, state: AppState) -> None:\n'
        + '    pass  # registered together with preview routes\n'
    )

    (ROUTES / 'preview_draw.py').write_text(combined)

    (ROUTES / 'preview.py').write_text(
        'from wall_climber.http.routes.preview_draw import register_preview_routes as register_routes\n'
    )
    (ROUTES / 'draw.py').write_text(
        'from wall_climber.http.routes.preview_draw import register_preview_routes as register_routes\n'
    )

    app_state = (HTTP / 'app_state.py').read_text()
    app_state = app_state.replace(
        '        preview.register_routes(app, self)\n        draw.register_routes(app, self)',
        '        preview.register_routes(app, self)',
    )
    (HTTP / 'app_state.py').write_text(app_state)
    print('rebuilt preview_draw.py')


if __name__ == '__main__':
    main()
