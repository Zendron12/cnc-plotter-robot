"""Tests for per-column text cursor storage and reset API."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber.vector_pipeline import TextGlyphOutline, VectorBounds
from wall_climber.web_server import (
    BackendRuntime,
    _bump_row_top_below_global_ink,
    _note_text_column_bottom_from_groups,
)


class _FakeRuntime:
    """Minimal runtime fake with per-column cursor semantics."""

    def __init__(self) -> None:
        self.node = MagicMock()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'
        self._text_cursors: dict[str, tuple[float | None, float | None]] = {
            'full': (1.0, 2.0),
            'left': (0.5, 1.5),
        }
        self._text_full_width_bottom_y: float | None = None
        self._text_global_bottom_y: float | None = None

    def get_text_global_bottom_y(self) -> float | None:
        return self._text_global_bottom_y

    def note_text_global_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if self._text_global_bottom_y is None or value > self._text_global_bottom_y:
            self._text_global_bottom_y = value

    def get_text_cursor(self, column: str | None = None) -> tuple[float | None, float | None]:
        key = BackendRuntime._normalize_cursor_column(column)
        return self._text_cursors.get(key, (None, None))

    def set_text_cursor(
        self,
        x: float | None,
        y: float | None,
        column: str | None = None,
    ) -> None:
        key = BackendRuntime._normalize_cursor_column(column)
        if x is None or y is None:
            self._text_cursors.pop(key, None)
        else:
            self._text_cursors[key] = (float(x), float(y))

    def reset_text_cursors(self, column: str | None = None, *, clear_ink: bool = True) -> None:
        if column is None:
            self._text_cursors.clear()
            if clear_ink:
                self._text_full_width_bottom_y = None
                self._text_global_bottom_y = None
            return
        key = BackendRuntime._normalize_cursor_column(column)
        self._text_cursors.pop(key, None)

    def get_text_column_bottom_y(self, column: str | None) -> float | None:
        key = BackendRuntime._normalize_cursor_column(column)
        if key not in {'left', 'center', 'right'}:
            return None
        return getattr(self, '_text_column_bottom_y', {}).get(key)

    def note_text_column_bottom_y(self, column: str | None, bottom_y: float) -> None:
        key = BackendRuntime._normalize_cursor_column(column)
        if key not in {'left', 'center', 'right'}:
            return
        if not hasattr(self, '_text_column_bottom_y'):
            self._text_column_bottom_y = {}
        value = float(bottom_y)
        existing = self._text_column_bottom_y.get(key)
        if existing is None or value > existing:
            self._text_column_bottom_y[key] = value

    def clear_text_cursor_position(self, column: str | None = None) -> None:
        key = BackendRuntime._normalize_cursor_column(column)
        self._text_cursors.pop(key, None)

    def get_text_full_width_bottom_y(self) -> float | None:
        return self._text_full_width_bottom_y

    def note_text_full_width_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if (
            self._text_full_width_bottom_y is None
            or value > self._text_full_width_bottom_y
        ):
            self._text_full_width_bottom_y = value


def test_backend_runtime_per_column_cursors() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}

    runtime.set_text_cursor(1.0, 2.0, 'left')
    runtime.set_text_cursor(3.0, 4.0, 'center')

    assert runtime.get_text_cursor('left') == (1.0, 2.0)
    assert runtime.get_text_cursor('center') == (3.0, 4.0)
    assert runtime.get_text_cursor('full') == (None, None)

    runtime.reset_text_cursors('left')
    assert runtime.get_text_cursor('left') == (None, None)
    assert runtime.get_text_cursor('center') == (3.0, 4.0)

    runtime.reset_text_cursors()
    assert runtime.get_text_cursor('center') == (None, None)


def test_reset_text_cursor_endpoint_resets_one_column() -> None:
    runtime = _FakeRuntime()
    client = TestClient(web_server.create_app(runtime))

    response = client.post('/api/text/reset_cursor', json={'column': 'left'})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['text_column'] == 'left'
    assert runtime.get_text_cursor('left') == (None, None)
    assert runtime.get_text_cursor('full') == (1.0, 2.0)


def test_reset_text_cursor_endpoint_resets_all_without_body() -> None:
    runtime = _FakeRuntime()
    client = TestClient(web_server.create_app(runtime))

    response = client.post('/api/text/reset_cursor')

    assert response.status_code == 200, response.text
    assert response.json()['text_column'] is None
    assert runtime.get_text_cursor('full') == (None, None)
    assert runtime.get_text_cursor('left') == (None, None)


def test_normalize_cursor_column_maps_unknown_to_full() -> None:
    assert BackendRuntime._normalize_cursor_column('bogus') == 'full'


def _glyph_with_bottom(bottom_y: float) -> TextGlyphOutline:
    return TextGlyphOutline(
        line_index=0,
        word_index=0,
        text='A',
        strokes=(((0.0, 0.0), (1.0, 0.0)),),
        bbox=VectorBounds(x_min=0.0, x_max=1.0, y_min=0.0, y_max=bottom_y),
        advance=1.0,
        source='bundled_source',
    )


def test_text_full_width_bottom_y_tracks_max_and_resets() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}

    runtime.note_text_full_width_bottom_y(1.2)
    runtime.note_text_full_width_bottom_y(0.8)
    assert runtime.get_text_full_width_bottom_y() == 1.2

    runtime.reset_text_cursors()
    assert runtime.get_text_full_width_bottom_y() is None


def test_note_text_column_bottom_only_updates_full_width_draws() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}

    glyphs = (_glyph_with_bottom(1.0), _glyph_with_bottom(2.5))

    _note_text_column_bottom_from_groups(runtime, glyphs, text_column='full')
    assert runtime.get_text_full_width_bottom_y() == 2.5
    assert runtime.get_text_global_bottom_y() == 2.5

    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}
    _note_text_column_bottom_from_groups(runtime, glyphs, text_column='left')
    assert runtime.get_text_full_width_bottom_y() is None
    assert runtime.get_text_global_bottom_y() == 2.5
    assert runtime.get_text_column_bottom_y('left') == 2.5


def test_text_column_bottom_y_tracks_max_and_resets() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}

    runtime.note_text_column_bottom_y('left', 1.2)
    runtime.note_text_column_bottom_y('left', 0.8)
    assert runtime.get_text_column_bottom_y('left') == 1.2

    runtime.reset_text_cursors('left')
    assert runtime.get_text_column_bottom_y('left') is None
    assert runtime.get_text_column_bottom_y('center') is None

    runtime.note_text_column_bottom_y('center', 2.0)
    runtime.reset_text_cursors()
    assert runtime.get_text_column_bottom_y('center') is None


def test_text_global_bottom_y_tracks_max_and_resets() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}

    runtime.note_text_global_bottom_y(1.2)
    runtime.note_text_global_bottom_y(0.8)
    assert runtime.get_text_global_bottom_y() == 1.2

    runtime.reset_text_cursors()
    assert runtime.get_text_global_bottom_y() is None


def test_reset_text_cursor_endpoint_preserves_ink_when_clear_ink_false() -> None:
    runtime = _FakeRuntime()
    runtime._text_column_bottom_y = {'left': 2.0}
    runtime.note_text_full_width_bottom_y(1.5)
    runtime.note_text_global_bottom_y(1.5)
    runtime.set_text_cursor(0.5, 1.0, 'left')

    client = TestClient(web_server.create_app(runtime))
    response = client.post(
        '/api/text/reset_cursor',
        json={'column': 'left', 'clear_ink': False},
    )

    assert response.status_code == 200, response.text
    assert runtime.get_text_cursor('left') == (None, None)
    assert runtime.get_text_column_bottom_y('left') == 2.0
    assert runtime.get_text_full_width_bottom_y() == 1.5


def test_bump_row_top_below_global_ink() -> None:
    runtime = BackendRuntime.__new__(BackendRuntime)
    runtime._text_cursors = {}
    runtime._text_cursor_lock = threading.Lock()
    runtime._text_full_width_bottom_y = None
    runtime._text_global_bottom_y = None
    runtime._text_column_bottom_y = {}
    runtime._text_global_bottom_y = 1.0

    bumped = _bump_row_top_below_global_ink(runtime, 0.5, 0.15)
    assert bumped == 1.15
