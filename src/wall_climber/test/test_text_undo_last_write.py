"""Tests for text write undo snapshots and API."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.web_server import BackendRuntime


def _bare_runtime() -> BackendRuntime:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}
    runtime._last_text_draw_column = None
    runtime._text_ink_undo_stacks = {
        key: [] for key in ('full', 'left', 'center', 'right')
    }
    return runtime


def test_push_and_undo_text_ink_snapshot_restores_state() -> None:
    runtime = _bare_runtime()

    runtime.set_text_cursor(1.0, 2.0, 'left')
    runtime.note_text_column_bottom_y('left', 2.5)
    runtime.note_text_global_bottom_y(2.5)
    runtime.set_last_text_draw_column('left')

    runtime.push_text_ink_snapshot('left')

    runtime.set_text_cursor(3.0, 4.0, 'left')
    runtime.note_text_column_bottom_y('left', 4.5)
    runtime.note_text_global_bottom_y(4.5)
    runtime.set_last_text_draw_column('left')

    assert runtime.undo_last_text_write('left') is True
    assert runtime.get_text_cursor('left') == (1.0, 2.0)
    assert runtime.get_text_column_bottom_y('left') == 2.5
    assert runtime.get_text_global_bottom_y() == 2.5
    assert runtime.get_last_text_draw_column() == 'left'
    assert runtime.undo_last_text_write('left') is False


def test_undo_last_write_endpoint_restores_column() -> None:
    runtime = _bare_runtime()
    runtime.push_text_ink_snapshot('full')
    runtime.set_text_cursor(0.2, 0.3, 'full')
    runtime.note_text_full_width_bottom_y(1.0)

    client = TestClient(web_server.create_app(runtime))
    response = client.post('/api/text/undo_last_write', json={'column': 'full'})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['restored'] is True
    assert payload['text_column'] == 'full'
    assert payload['text_cursor'] is None
    assert runtime.get_text_cursor('full') == (None, None)


def test_undo_last_write_endpoint_returns_409_when_empty() -> None:
    runtime = _bare_runtime()
    client = TestClient(web_server.create_app(runtime))
    response = client.post('/api/text/undo_last_write', json={'column': 'right'})
    assert response.status_code == 409, response.text
