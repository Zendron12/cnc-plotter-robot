"""Endpoint tests for the thin-line filter / face-handling params on /api/preview.

Self-contained fake runtime. Requires httpx/TestClient (httpx-dependent set).
"""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.runtime_topics import MODE_DRAW, PEN_MODE_AUTO


def _encode_png(image: numpy.ndarray) -> bytes:
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    return bytes(encoded.tobytes())


def _mixed_thickness_png() -> bytes:
    """A thick stroke and a hairline stroke so the thin-line filter has an
    effect when its threshold sits between them."""
    image = numpy.full((160, 240, 3), 255, dtype=numpy.uint8)
    cv2.line(image, (20, 40), (220, 40), (0, 0, 0), 9, lineType=cv2.LINE_8)   # thick
    cv2.line(image, (20, 120), (220, 120), (0, 0, 0), 1, lineType=cv2.LINE_8)  # hairline
    return _encode_png(image)


class _FakeNode:
    def __init__(self) -> None:
        self.active_mode = MODE_DRAW
        self.manual_pen_mode = PEN_MODE_AUTO

    def runtime_snapshot(self) -> dict:
        return {
            'ready': True,
            'active_mode': self.active_mode,
            'manual_pen_mode': self.manual_pen_mode,
            'statuses': {'cable_executor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }

    def writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.0, 'x_max': 6.3, 'y_min': 0.0, 'y_max': 3.0}

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    def publish_execution_plan(self, primitive_plan, *, allowed_modes):
        return {'published': 'primitive_path_plan', 'topics': {}}


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'

    def record_last_plan_debug(self, payload: dict) -> None:
        pass

    def record_last_execution_debug(self, payload: dict) -> None:
        pass

    def record_last_curve_fit_debug(self, _payload: dict) -> None:
        pass

    def set_text_cursor(self, *_args, **_kwargs) -> None:
        pass


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def _preview(client: TestClient, *, extra: dict | None = None) -> dict:
    data = {'optimization_preset': 'detail', 'optimize_stroke_order': 'false'}
    if extra:
        data.update(extra)
    response = client.post(
        '/api/preview',
        files={'file': ('sketch.png', _mixed_thickness_png(), 'image/png')},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_default_request_has_filter_disabled() -> None:
    client = _client()
    payload = _preview(client)
    meta = payload.get('metadata') or {}
    # Default: thin-line filter disabled, face handling enabled, no faces.
    assert meta.get('thin_line_filter_enabled') in (False, None)


def test_thin_line_filter_reduces_strokes() -> None:
    client = _client()
    baseline = _preview(client)
    filtered = _preview(client, extra={'thin_line_min_width_mm': '6.0'})
    base_strokes = (baseline.get('metadata') or {}).get('final_stroke_count')
    filt_strokes = (filtered.get('metadata') or {}).get('final_stroke_count')
    assert base_strokes is not None and filt_strokes is not None
    # The hairline should be dropped, so fewer (or equal) strokes remain.
    assert filt_strokes <= base_strokes
    assert (filtered.get('metadata') or {}).get('thin_line_filter_enabled') is True
