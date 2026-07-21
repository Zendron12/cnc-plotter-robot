"""Tests for column text wrap width and continuation cursor layout."""

from __future__ import annotations

import threading
from pathlib import Path

from wall_climber.vector_pipeline import VectorPlacement, place_grouped_text_on_board, vectorize_text_grouped
from wall_climber.web_server import _grouped_text_bounds


class _FakeTextNode:
    def carriage_safe_writable_bounds(self):
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self):
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}

    def publish_execution_plan(self, *_args, **_kwargs):
        return {
            'published': 'primitive_path_plan',
            'preferred_transport': 'primitive_path_plan',
            'primitive_transport_published': True,
            'topics': {},
        }


class _FakeTextRuntime:
    def __init__(self) -> None:
        self.node = _FakeTextNode()
        self.web_dir = Path('src/wall_climber/web')
        self._text_cursors: dict = {}
        self._text_full_width_bottom_y = None
        self._text_global_bottom_y = None
        self._text_column_bottom_y: dict = {}
        self._last_text_draw_column = None
        self._text_cursor_lock = threading.Lock()

    def get_text_cursor(self, column=None):
        return self._text_cursors.get((column or 'full').strip().lower(), (None, None))

    def set_text_cursor(self, x, y, column=None):
        key = (column or 'full').strip().lower()
        if x is None or y is None:
            self._text_cursors.pop(key, None)
        else:
            self._text_cursors[key] = (float(x), float(y))

    def clear_text_cursor_position(self, column=None):
        key = (column or 'full').strip().lower()
        self._text_cursors.pop(key, None)

    def reset_text_cursors(self, column=None, *, clear_ink=True):
        if column is None:
            self._text_cursors.clear()
            if clear_ink:
                self._text_full_width_bottom_y = None
                self._text_global_bottom_y = None
                self._text_column_bottom_y.clear()
                self._last_text_draw_column = None
        else:
            key = str(column).strip().lower()
            self._text_cursors.pop(key, None)
            if clear_ink:
                if key in {'left', 'center', 'right'}:
                    self._text_column_bottom_y.pop(key, None)
                elif key == 'full':
                    self._text_full_width_bottom_y = None

    def get_text_full_width_bottom_y(self):
        return self._text_full_width_bottom_y

    def note_text_full_width_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if self._text_full_width_bottom_y is None or value > self._text_full_width_bottom_y:
            self._text_full_width_bottom_y = value

    def get_text_global_bottom_y(self):
        return self._text_global_bottom_y

    def note_text_global_bottom_y(self, bottom_y: float) -> None:
        value = float(bottom_y)
        if self._text_global_bottom_y is None or value > self._text_global_bottom_y:
            self._text_global_bottom_y = value

    def get_text_column_bottom_y(self, column=None):
        key = (column or 'full').strip().lower()
        if key not in {'left', 'center', 'right'}:
            return None
        return self._text_column_bottom_y.get(key)

    def note_text_column_bottom_y(self, column, bottom_y: float) -> None:
        key = (column or 'full').strip().lower()
        if key not in {'left', 'center', 'right'}:
            return
        value = float(bottom_y)
        existing = self._text_column_bottom_y.get(key)
        if existing is None or value > existing:
            self._text_column_bottom_y[key] = value

    def get_last_text_draw_column(self):
        return self._last_text_draw_column

    def set_last_text_draw_column(self, column=None) -> None:
        self._last_text_draw_column = (column or 'full').strip().lower()

    def record_last_plan_debug(self, *_args, **_kwargs) -> None:
        pass

    def record_last_execution_debug(self, *_args, **_kwargs) -> None:
        pass

    def record_last_curve_fit_debug(self, *_args, **_kwargs) -> None:
        pass


def _glyph_min_x(glyph) -> float:
    if not glyph.strokes:
        return 0.0
    return min(point[0] for stroke in glyph.strokes for point in stroke)


def test_initial_cursor_x_em_offsets_first_glyph() -> None:
    glyphs_at_zero = vectorize_text_grouped(
        'A',
        font_source='relief_singleline',
        max_line_width_units=20.0,
        initial_cursor_x_em=0.0,
    )
    glyphs_offset = vectorize_text_grouped(
        'A',
        font_source='relief_singleline',
        max_line_width_units=20.0,
        initial_cursor_x_em=5.0,
    )

    assert _glyph_min_x(glyphs_offset[0]) > _glyph_min_x(glyphs_at_zero[0]) + 4.0


def test_initial_cursor_uses_full_line_width_not_narrow_remainder() -> None:
    """Full line width wrap should use fewer lines than a 2em remainder strip."""
    text = 'hello world test'
    glyphs_full_width = vectorize_text_grouped(
        text,
        font_source='relief_singleline',
        max_line_width_units=20.0,
        initial_cursor_x_em=18.0,
    )
    glyphs_narrow_strip = vectorize_text_grouped(
        text,
        font_source='relief_singleline',
        max_line_width_units=2.0,
        initial_cursor_x_em=0.0,
    )

    lines_full = {glyph.line_index for glyph in glyphs_full_width}
    lines_narrow = {glyph.line_index for glyph in glyphs_narrow_strip}

    assert len(lines_full) < len(lines_narrow)


def test_bounds_relative_placement_honors_initial_cursor_x_em() -> None:
    """Append placement must use source_bounds x_min so ink starts at cursor X."""
    glyphs = vectorize_text_grouped(
        'hi',
        font_source='relief_singleline',
        max_line_width_units=20.0,
        initial_cursor_x_em=5.0,
    )
    source_bounds = _grouped_text_bounds(glyphs)
    assert source_bounds['x_min'] > 4.0

    text_start_x = 0.5
    glyph_scale_m = 0.086
    writable_bounds = {'x_min': 0.0, 'x_max': 6.0, 'y_min': 0.0, 'y_max': 3.0}
    placement = VectorPlacement(
        x=text_start_x
        + ((source_bounds['x_min'] + 0.5 * source_bounds['width']) * glyph_scale_m),
        y=0.5
        + ((source_bounds['y_min'] + 0.5 * source_bounds['height']) * glyph_scale_m),
        scale=glyph_scale_m,
    )
    placed_groups, _placement_result = place_grouped_text_on_board(
        glyphs,
        writable_bounds=writable_bounds,
        placement=placement,
        fit_padding=0.9,
    )
    placed_min_x = min(glyph.bbox.x_min for glyph in placed_groups)

    legacy_placement = VectorPlacement(
        x=text_start_x + (0.5 * source_bounds['width'] * glyph_scale_m),
        y=0.5 + (0.5 * source_bounds['height'] * glyph_scale_m),
        scale=glyph_scale_m,
    )
    legacy_groups, _legacy_result = place_grouped_text_on_board(
        glyphs,
        writable_bounds=writable_bounds,
        placement=legacy_placement,
        fit_padding=0.9,
    )
    legacy_min_x = min(glyph.bbox.x_min for glyph in legacy_groups)

    assert placed_min_x > text_start_x + 0.3
    assert placed_min_x > legacy_min_x + 0.2


def test_continuation_vertical_anchor_aligns_with_last_line_row() -> None:
    """Append ink must share the same row top Y as the previous line."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    base_text = 'Press Dictate to speak into the text box (pause briefly every few words).'
    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'text_column': 'left',
        'line_height': 1.75,
    }

    first = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': base_text, 'settings': settings},
    ).json()
    client.post('/api/draw', json={'preview_id': first['preview_id']})

    _cursor_x, last_line_y_min = runtime.get_text_cursor('left')
    assert _cursor_x is not None and last_line_y_min is not None
    last_line_x_max = max(
        point[0]
        for stroke in first['preview']['strokes']
        if min(point[1] for point in stroke) >= last_line_y_min - 0.001
        for point in stroke
    )

    append = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': ' hello hisham', 'settings': settings},
    ).json()
    append_x = [point[0] for stroke in append['preview']['strokes'] for point in stroke]
    append_y = [point[1] for stroke in append['preview']['strokes'] for point in stroke]

    assert min(append_x) >= last_line_x_max - 0.02
    assert abs(min(append_y) - last_line_y_min) < 0.002


def test_full_wrap_stays_below_left_column_ink() -> None:
    """Full append that wraps must not overlap ink already drawn in Left."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
    }

    title = 'Board Title'
    full_settings = {**base_settings, 'text_column': 'full'}
    title_preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': title, 'settings': full_settings},
    ).json()
    client.post('/api/draw', json={'preview_id': title_preview['preview_id']})

    left_text = (
        'Left column paragraph one with enough words to wrap across multiple lines. '
        'Left column paragraph two continues with even more words so the left ink '
        'extends well below the short full-width title.'
    )
    left_settings = {**base_settings, 'text_column': 'left'}
    left_preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': left_text, 'settings': left_settings},
    ).json()
    client.post('/api/draw', json={'preview_id': left_preview['preview_id']})

    left_y_max = max(
        point[1]
        for stroke in left_preview['preview']['strokes']
        for point in stroke
    )

    wrap_suffix = (
        ' and this continuation should wrap onto a brand new full-width line with '
        'many additional words that cannot possibly fit on the same row as the title'
    )
    append_preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': wrap_suffix, 'settings': full_settings},
    ).json()
    append_ys = [point[1] for stroke in append_preview['preview']['strokes'] for point in stroke]
    title_y_max = max(
        point[1]
        for stroke in title_preview['preview']['strokes']
        for point in stroke
    )
    wrapped_line_ys = [y for y in append_ys if y > title_y_max + 0.05]
    assert wrapped_line_ys, 'expected wrapped full-width line below title row'
    assert min(wrapped_line_ys) >= left_y_max - 0.02


def test_full_same_column_append_stays_on_same_row() -> None:
    """Full append on the same row must not float above the prior line when suffix wraps."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    sentence = (
        'Press Dictate to speak into the text box (pause briefly every few words).'
    )
    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    full_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'text_column': 'full',
        'line_height': 1.75,
    }

    first_preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': sentence, 'settings': full_settings},
    ).json()
    client.post('/api/draw', json={'preview_id': first_preview['preview_id']})

    _cursor_x, last_line_y_min = runtime.get_text_cursor('full')
    assert _cursor_x is not None and last_line_y_min is not None

    append_preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': sentence, 'settings': full_settings},
    ).json()
    append_ys = [point[1] for stroke in append_preview['preview']['strokes'] for point in stroke]
    first_line_y_max = max(
        point[1]
        for stroke in first_preview['preview']['strokes']
        for point in stroke
        if min(point[1] for point in stroke) >= last_line_y_min - 0.001
    )
    same_row_append_ys = [y for y in append_ys if y <= first_line_y_max + 0.02]
    assert same_row_append_ys, 'expected append ink on the continuation row'
    assert abs(min(same_row_append_ys) - last_line_y_min) < 0.002


def test_first_draw_source_bounds_y_min_is_negative() -> None:
    """First-draw glyphs use negative em y_min; placement must not shift on that."""
    glyphs = vectorize_text_grouped(
        'HELLO',
        font_source='relief_singleline',
        max_line_width_units=50.0,
        initial_cursor_x_em=0.0,
    )
    source_bounds = _grouped_text_bounds(glyphs)
    assert source_bounds['y_min'] < 0.0


def _preview_draw(client, *, text: str, settings: dict) -> dict:
    preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': text, 'settings': settings},
    ).json()
    client.post('/api/draw', json={'preview_id': preview['preview_id']})
    return preview


def test_partial_column_starts_below_full_ink() -> None:
    """Center first write must start below full-width ink, not at board top."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }

    full_preview = _preview_draw(
        client,
        text='Board Title Line',
        settings={**base_settings, 'text_column': 'full'},
    )
    full_y_max = max(
        point[1]
        for stroke in full_preview['preview']['strokes']
        for point in stroke
    )

    center_preview = _preview_draw(
        client,
        text='Center column opening line.',
        settings={**base_settings, 'text_column': 'center'},
    )
    center_y_min = min(
        point[1]
        for stroke in center_preview['preview']['strokes']
        for point in stroke
    )
    assert center_y_min > full_y_max + 0.05


def test_left_continues_after_column_switch() -> None:
    """Left append after writing Center must continue below Left ink, not re-seed."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }
    left_settings = {**base_settings, 'text_column': 'left'}

    _preview_draw(
        client,
        text='Full width header',
        settings={**base_settings, 'text_column': 'full'},
    )
    _preview_draw(
        client,
        text='Left line one with enough words to wrap.',
        settings=left_settings,
    )
    _preview_draw(
        client,
        text=' Left line two continues.',
        settings=left_settings,
    )
    _preview_draw(
        client,
        text=' Left line three continues.',
        settings=left_settings,
    )

    _preview_draw(
        client,
        text='Center side note.',
        settings={**base_settings, 'text_column': 'center'},
    )

    _cursor_x, last_line_y = runtime.get_text_cursor('left')
    assert _cursor_x is not None and last_line_y is not None

    fourth = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': ' Left line four continues.',
            'settings': left_settings,
        },
    ).json()
    fourth_y_min = min(
        point[1]
        for stroke in fourth['preview']['strokes']
        for point in stroke
    )
    full_y_max = runtime.get_text_full_width_bottom_y()
    assert full_y_max is not None
    assert abs(fourth_y_min - last_line_y) < 0.02
    assert fourth_y_min > full_y_max + 0.05


def test_column_seed_gap_slider_affects_drop() -> None:
    """Larger column_seed_gap places first partial-column line further below Full ink."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
    }

    def center_first_y_min(seed_gap: float) -> float:
        runtime = _FakeTextRuntime()
        client = TestClient(web_server.create_app(runtime))
        _preview_draw(
            client,
            text='Full width header',
            settings={**base_settings, 'text_column': 'full'},
        )
        center_preview = _preview_draw(
            client,
            text='Center opening.',
            settings={
                **base_settings,
                'text_column': 'center',
                'column_seed_gap': seed_gap,
            },
        )
        return min(
            point[1]
            for stroke in center_preview['preview']['strokes']
            for point in stroke
        )

    tight_y = center_first_y_min(1.0)
    wide_y = center_first_y_min(2.5)
    assert wide_y > tight_y + 0.05


def test_cursor_reset_preserves_column_bottom() -> None:
    """First-write cursor reset must not wipe ink floors for the column."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }
    _preview_draw(
        client,
        text='Left column paragraph one.',
        settings={**base_settings, 'text_column': 'left'},
    )
    left_bottom_before = runtime.get_text_column_bottom_y('left')
    assert left_bottom_before is not None

    response = client.post(
        '/api/text/reset_cursor',
        json={'column': 'left', 'clear_ink': False},
    )
    assert response.status_code == 200
    assert runtime.get_text_column_bottom_y('left') == left_bottom_before
    assert runtime.get_text_cursor('left') == (None, None)


def test_full_reentry_starts_new_line_after_left() -> None:
    """Full re-entry after partial columns starts below tallest partial ink."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }
    full_settings = {**base_settings, 'text_column': 'full'}
    title = 'Board Title Line'
    tall_left = (
        'Left column paragraph one with enough words to wrap across multiple lines. '
        'Left column paragraph two continues with more words to grow taller than Full.'
    )

    first_full = _preview_draw(client, text=title, settings=full_settings)
    first_full_y_max = max(
        point[1]
        for stroke in first_full['preview']['strokes']
        for point in stroke
    )

    left_preview = _preview_draw(
        client,
        text=tall_left,
        settings={**base_settings, 'text_column': 'left'},
    )
    left_y_max = max(
        point[1]
        for stroke in left_preview['preview']['strokes']
        for point in stroke
    )
    assert left_y_max > first_full_y_max + 0.02
    assert runtime.get_text_cursor('full') == (None, None)

    _preview_draw(
        client,
        text='Center note.',
        settings={**base_settings, 'text_column': 'center'},
    )

    second_full = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': ' Second full paragraph.',
            'settings': full_settings,
        },
    ).json()
    second_y_min = min(
        point[1]
        for stroke in second_full['preview']['strokes']
        for point in stroke
    )
    glyph_height = 0.012
    expected_floor = max(first_full_y_max, left_y_max) + base_settings['column_seed_gap'] * glyph_height
    assert second_y_min >= expected_floor - 0.02
    assert second_y_min > first_full_y_max + 0.05


def test_full_two_commands_normal_line_gap() -> None:
    """Two Full commands on separate rows should use line_height, not double bump."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'text_column': 'full',
        'line_height': 1.75,
    }
    glyph_height = 0.012
    line_spacing_gap = settings['line_height'] * glyph_height
    line_one = (
        'Press Dictate to speak into the text box pause briefly every few words '
        * 5
    )

    first = _preview_draw(client, text=line_one, settings=settings)
    first_y_max = max(
        point[1]
        for stroke in first['preview']['strokes']
        for point in stroke
    )
    _cursor_x, cursor_y = runtime.get_text_cursor('full')
    assert _cursor_x is not None and cursor_y is not None
    assert cursor_y > first_y_max + 0.005, 'expected cursor on a fresh row below prior ink'

    cursor_row_gap = cursor_y - first_y_max
    assert abs(cursor_row_gap - line_spacing_gap) < 0.03
    assert cursor_row_gap < line_spacing_gap * 1.5

    second = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': ' Second full-width line starts here.',
            'settings': settings,
        },
    ).json()
    second_y_min = min(
        point[1]
        for stroke in second['preview']['strokes']
        for point in stroke
    )
    assert abs(second_y_min - cursor_y) < 0.002
    assert abs((second_y_min - first_y_max) - line_spacing_gap) < 0.03


def test_full_single_draw_internal_wrap_spacing() -> None:
    """Single Full draw that wraps internally should use line_height between rows."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'text_column': 'full',
        'line_height': 1.75,
    }
    glyph_height = 0.012
    line_spacing_gap = settings['line_height'] * glyph_height
    fill_line = (
        'Press Dictate to speak into the text box pause briefly every few words '
        'Press Dictate to speak into the text box pause briefly every few words'
    )

    preview = client.post(
        '/api/preview',
        json={'input_type': 'text', 'text': fill_line, 'settings': settings},
    ).json()
    row_tops = sorted(
        {
            round(min(point[1] for point in stroke), 4)
            for stroke in preview['preview']['strokes']
        }
    )
    assert len(row_tops) >= 2, 'expected internal wrap onto a second row'
    row_step = row_tops[1] - row_tops[0]
    assert abs(row_step - line_spacing_gap) < 0.025


def test_full_to_full_append_uses_line_spacing_not_drop_gap() -> None:
    """Full append on a wrapped new row uses line_height, not column_seed_gap."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    fill_line = (
        'Press Dictate to speak into the text box pause briefly every few words '
        'Press Dictate to speak into the text box pause briefly every few words'
    )
    settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'text_column': 'full',
        'line_height': 1.75,
        'column_seed_gap': 2.5,
    }
    glyph_height = 0.012
    line_spacing_gap = settings['line_height'] * glyph_height
    drop_gap = settings['column_seed_gap'] * glyph_height

    def wrapped_row_step(client_local) -> float:
        first = client_local.post(
            '/api/preview',
            json={'input_type': 'text', 'text': fill_line, 'settings': settings},
        ).json()
        client_local.post('/api/draw', json={'preview_id': first['preview_id']})
        second = client_local.post(
            '/api/preview',
            json={'input_type': 'text', 'text': fill_line, 'settings': settings},
        ).json()
        row_tops = sorted(
            {
                round(
                    min(point[1] for point in stroke),
                    4,
                )
                for stroke in second['preview']['strokes']
            }
        )
        assert len(row_tops) >= 2, 'expected second Full draw to wrap onto another row'
        return row_tops[1] - row_tops[0]

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    row_step = wrapped_row_step(client)
    assert row_step < 0.5 * drop_gap
    assert abs(row_step - line_spacing_gap) < 0.025


def test_full_wrap_uses_line_height_not_fixed_gap() -> None:
    """Between-draw Full wrap should use line_height for the next row step."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    fill_line = (
        'Press Dictate to speak into the text box pause briefly every few words '
        'Press Dictate to speak into the text box pause briefly every few words'
    )

    def second_row_offset(line_height: float) -> float:
        runtime_local = _FakeTextRuntime()
        client_local = TestClient(web_server.create_app(runtime_local))
        settings = {
            'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
            'font_source': 'relief_singleline',
            'text_column': 'full',
            'line_height': line_height,
        }
        first = client_local.post(
            '/api/preview',
            json={'input_type': 'text', 'text': fill_line, 'settings': settings},
        ).json()
        client_local.post('/api/draw', json={'preview_id': first['preview_id']})
        first_top = min(
            point[1]
            for stroke in first['preview']['strokes']
            for point in stroke
        )
        second = client_local.post(
            '/api/preview',
            json={
                'input_type': 'text',
                'text': ' Overflow paragraph starts here.',
                'settings': settings,
            },
        ).json()
        second_top = min(
            point[1]
            for stroke in second['preview']['strokes']
            for point in stroke
        )
        assert second_top > first_top + 0.02, 'expected second draw on a new row'
        return second_top - first_top

    tight_step = second_row_offset(1.2)
    wide_step = second_row_offset(2.2)
    assert wide_step > tight_step + 0.03


def test_partial_columns_share_full_relative_baseline() -> None:
    """Left, Center, and Right first rows share the same Y below Full ink."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }
    full_text = (
        'Press Dictate to speak into the text box (pause briefly every few words). '
        'Press Dictate to speak into the text box (pause briefly every few words).'
    )
    column_text = (
        'Press Dictate to speak into the text box (pause briefly every few words). '
        'hello column paragraph with enough words to wrap across multiple lines.'
    )

    full_preview = _preview_draw(
        client,
        text=full_text,
        settings={**base_settings, 'text_column': 'full'},
    )
    full_y_max = max(
        point[1]
        for stroke in full_preview['preview']['strokes']
        for point in stroke
    )

    left_preview = _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'left'},
    )
    center_preview = _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'center'},
    )
    right_preview = _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'right'},
    )

    def first_row_y_min(preview: dict) -> float:
        return min(
            point[1]
            for stroke in preview['preview']['strokes']
            for point in stroke
        )

    left_y_min = first_row_y_min(left_preview)
    center_y_min = first_row_y_min(center_preview)
    right_y_min = first_row_y_min(right_preview)

    assert left_y_min > full_y_max + 0.02
    assert center_y_min > full_y_max + 0.02
    assert right_y_min > full_y_max + 0.02
    assert abs(center_y_min - left_y_min) < 0.02
    assert abs(right_y_min - left_y_min) < 0.02


def test_partial_reentry_after_second_full() -> None:
    """Partial columns re-enter below Full block 2, not at round-1 band."""
    from fastapi.testclient import TestClient
    from wall_climber import web_server

    runtime = _FakeTextRuntime()
    client = TestClient(web_server.create_app(runtime))
    base_settings = {
        'placement': {'x': 0.55, 'y': 0.45, 'scale': 1},
        'font_source': 'relief_singleline',
        'line_height': 1.75,
        'column_seed_gap': 1.75,
    }
    glyph_height = 0.012
    full_text = (
        'Press Dictate to speak into the text box (pause briefly every few words). '
        'Press Dictate to speak into the text box (pause briefly every few words).'
    )
    column_text = (
        'Press Dictate to speak into the text box (pause briefly every few words). '
        'hello column paragraph with enough words to wrap across multiple lines.'
    )

    _preview_draw(
        client,
        text=full_text,
        settings={**base_settings, 'text_column': 'full'},
    )
    center_round1 = _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'center'},
    )
    center_round1_y_min = min(
        point[1]
        for stroke in center_round1['preview']['strokes']
        for point in stroke
    )
    _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'left'},
    )
    _preview_draw(
        client,
        text=column_text,
        settings={**base_settings, 'text_column': 'right'},
    )

    full2 = _preview_draw(
        client,
        text=' Second full-width block after partial columns.',
        settings={**base_settings, 'text_column': 'full'},
    )
    full2_y_max = max(
        point[1]
        for stroke in full2['preview']['strokes']
        for point in stroke
    )

    center_round2 = client.post(
        '/api/preview',
        json={
            'input_type': 'text',
            'text': ' Center round two starts here.',
            'settings': {**base_settings, 'text_column': 'center'},
        },
    ).json()
    center_round2_y_min = min(
        point[1]
        for stroke in center_round2['preview']['strokes']
        for point in stroke
    )
    expected_floor = full2_y_max + base_settings['column_seed_gap'] * glyph_height

    assert center_round2_y_min >= expected_floor - 0.02
    assert center_round2_y_min > center_round1_y_min + 0.05
    assert runtime.get_text_cursor('center') == (None, None)
