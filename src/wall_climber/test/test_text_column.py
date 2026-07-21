"""Unit tests for board text column layout helpers."""

from __future__ import annotations

import pytest

from wall_climber.web_server import _normalize_text_column, _text_column_x_bounds


def test_normalize_text_column_defaults_to_full() -> None:
    assert _normalize_text_column(None) == 'full'
    assert _normalize_text_column('LEFT') == 'left'


def test_normalize_text_column_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        _normalize_text_column('middle')


def test_text_column_x_bounds_split_board_into_thirds() -> None:
    safe_bounds = {'x_min': 0.0, 'x_max': 6.0, 'y_min': 0.0, 'y_max': 3.0}

    assert _text_column_x_bounds(safe_bounds, 'full') == (0.0, 6.0)
    assert _text_column_x_bounds(safe_bounds, 'left') == (0.0, 2.0)
    assert _text_column_x_bounds(safe_bounds, 'center') == (2.0, 4.0)
    assert _text_column_x_bounds(safe_bounds, 'right') == (4.0, 6.0)
