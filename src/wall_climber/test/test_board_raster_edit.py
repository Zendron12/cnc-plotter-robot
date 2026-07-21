"""Tests for board_fab_utils.js helpers and preprocess/lineart endpoint."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wall_climber import web_server
from wall_climber.image_pipeline.ai_preprocess import PreprocessResult, PreprocessStagePreview


class _FakeRuntime:
    def __init__(self) -> None:
        from pathlib import Path

        self.web_dir = Path(__file__).resolve().parents[1] / 'web'


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def board_rect_to_image_crop(
    crop_rect: dict[str, float],
    image_bounds: dict[str, float],
) -> dict[str, float] | None:
    crop = {
        'xMin': min(crop_rect['xMin'], crop_rect['xMax']),
        'xMax': max(crop_rect['xMin'], crop_rect['xMax']),
        'yMin': min(crop_rect['yMin'], crop_rect['yMax']),
        'yMax': max(crop_rect['yMin'], crop_rect['yMax']),
    }
    image = {
        'xMin': min(image_bounds['xMin'], image_bounds['xMax']),
        'xMax': max(image_bounds['xMin'], image_bounds['xMax']),
        'yMin': min(image_bounds['yMin'], image_bounds['yMax']),
        'yMax': max(image_bounds['yMin'], image_bounds['yMax']),
    }
    image_width = image['xMax'] - image['xMin']
    image_height = image['yMax'] - image['yMin']
    if image_width <= 0 or image_height <= 0:
        return None
    return {
        'xMin': _clamp((crop['xMin'] - image['xMin']) / image_width, 0, 1),
        'xMax': _clamp((crop['xMax'] - image['xMin']) / image_width, 0, 1),
        'yMin': _clamp((crop['yMin'] - image['yMin']) / image_height, 0, 1),
        'yMax': _clamp((crop['yMax'] - image['yMin']) / image_height, 0, 1),
    }


def compose_crop_normalized(
    parent_crop: dict[str, float] | None,
    inner_crop: dict[str, float],
) -> dict[str, float] | None:
    parent = parent_crop or {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0}
    parent_width = parent['xMax'] - parent['xMin']
    parent_height = parent['yMax'] - parent['yMin']
    if parent_width <= 0 or parent_height <= 0:
        return None
    next_crop = {
        'xMin': parent['xMin'] + (inner_crop['xMin'] * parent_width),
        'xMax': parent['xMin'] + (inner_crop['xMax'] * parent_width),
        'yMin': parent['yMin'] + (inner_crop['yMin'] * parent_height),
        'yMax': parent['yMin'] + (inner_crop['yMax'] * parent_height),
    }
    if (next_crop['xMax'] - next_crop['xMin']) < 0.001 or (next_crop['yMax'] - next_crop['yMin']) < 0.001:
        return None
    return next_crop


def effective_crop_pixel_size(
    memory_width_px: int,
    memory_height_px: int,
    crop_normalized: dict[str, float] | None,
) -> dict[str, float]:
    crop = crop_normalized or {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0}
    return {
        'width': max(1.0, memory_width_px * (crop['xMax'] - crop['xMin'])),
        'height': max(1.0, memory_height_px * (crop['yMax'] - crop['yMin'])),
    }


def clamp_rect_to_bounds(
    rect: dict[str, float],
    bounds: dict[str, float],
) -> dict[str, float]:
    width = rect['xMax'] - rect['xMin']
    height = rect['yMax'] - rect['yMin']
    x_min = rect['xMin']
    y_min = rect['yMin']
    if x_min < bounds['x_min']:
        x_min = bounds['x_min']
    if y_min < bounds['y_min']:
        y_min = bounds['y_min']
    if x_min + width > bounds['x_max']:
        x_min = bounds['x_max'] - width
    if y_min + height > bounds['y_max']:
        y_min = bounds['y_max'] - height
    return {
        'xMin': x_min,
        'xMax': x_min + width,
        'yMin': y_min,
        'yMax': y_min + height,
    }


def placement_from_image_bounds(
    image_bounds: dict[str, float],
    memory_width_px: int,
    memory_height_px: int,
    fit_bounds: dict[str, float],
    margin_m: float = 0.05,
    crop_normalized: dict[str, float] | None = None,
) -> dict[str, float]:
    width_m = image_bounds['xMax'] - image_bounds['xMin']
    effective = effective_crop_pixel_size(memory_width_px, memory_height_px, crop_normalized)
    avail_w = max(1.0e-6, (fit_bounds['x_max'] - fit_bounds['x_min']) - (2 * margin_m))
    avail_h = max(1.0e-6, (fit_bounds['y_max'] - fit_bounds['y_min']) - (2 * margin_m))
    base_scale = min(avail_w / effective['width'], avail_h / effective['height'])
    base_width_m = effective['width'] * base_scale
    return {
        'center_x_m': (image_bounds['xMin'] + image_bounds['xMax']) * 0.5,
        'center_y_m': (image_bounds['yMin'] + image_bounds['yMax']) * 0.5,
        'scale_percent': max(1.0, min(500.0, (width_m / max(1.0e-6, base_width_m)) * 100.0)),
        'fit_to_safe_area': True,
    }


def placement_from_ink_bounds(
    image_bounds_board: dict[str, float],
    ink_pixel_rect: dict[str, float],
    memory_width_px: int,
    memory_height_px: int,
    fit_bounds: dict[str, float],
    margin_m: float = 0.0,
    crop_normalized: dict[str, float] | None = None,
) -> dict[str, float]:
    crop = crop_normalized or {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0}
    board_width = image_bounds_board['xMax'] - image_bounds_board['xMin']
    board_height = image_bounds_board['yMax'] - image_bounds_board['yMin']
    crop_width = memory_width_px * (crop['xMax'] - crop['xMin'])
    crop_height = memory_height_px * (crop['yMax'] - crop['yMin'])
    crop_sx = memory_width_px * crop['xMin']
    crop_sy = memory_height_px * crop['yMin']
    frac_x_min = (ink_pixel_rect['xMin'] - crop_sx) / crop_width
    frac_x_max = (ink_pixel_rect['xMax'] - crop_sx) / crop_width
    frac_y_min = (ink_pixel_rect['yMin'] - crop_sy) / crop_height
    frac_y_max = (ink_pixel_rect['yMax'] - crop_sy) / crop_height
    ink_board = {
        'xMin': image_bounds_board['xMin'] + (frac_x_min * board_width),
        'xMax': image_bounds_board['xMin'] + (frac_x_max * board_width),
        'yMin': image_bounds_board['yMin'] + (frac_y_min * board_height),
        'yMax': image_bounds_board['yMin'] + (frac_y_max * board_height),
    }
    return placement_from_image_bounds(
        ink_board,
        int(ink_pixel_rect['width']),
        int(ink_pixel_rect['height']),
        fit_bounds,
        margin_m,
        {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0},
    )


def board_rect_to_canvas_rect(
    view: dict[str, float],
    origin_x: float,
    origin_y: float,
    scale: float,
    board_rect: dict[str, float],
) -> dict[str, float]:
    def board_to_canvas(x: float, y: float) -> tuple[float, float]:
        return (
            origin_x + ((x - view['x_min']) * scale),
            origin_y + ((y - view['y_min']) * scale),
        )

    a = board_to_canvas(board_rect['xMin'], board_rect['yMin'])
    b = board_to_canvas(board_rect['xMax'], board_rect['yMax'])
    return {
        'x': min(a[0], b[0]),
        'y': min(a[1], b[1]),
        'width': abs(b[0] - a[0]),
        'height': abs(b[1] - a[1]),
    }


def fit_image_bounds_to_board(
    image_width_px: int,
    image_height_px: int,
    board_bounds: dict[str, float],
    margin_m: float = 0.05,
) -> dict[str, float]:
    avail_w = max(0.01, (board_bounds['x_max'] - board_bounds['x_min']) - (2 * margin_m))
    avail_h = max(0.01, (board_bounds['y_max'] - board_bounds['y_min']) - (2 * margin_m))
    scale = min(avail_w / image_width_px, avail_h / image_height_px)
    width_m = image_width_px * scale
    height_m = image_height_px * scale
    cx = (board_bounds['x_min'] + board_bounds['x_max']) * 0.5
    cy = (board_bounds['y_min'] + board_bounds['y_max']) * 0.5
    return {
        'xMin': cx - width_m * 0.5,
        'xMax': cx + width_m * 0.5,
        'yMin': cy - height_m * 0.5,
        'yMax': cy + height_m * 0.5,
    }


def test_compose_crop_normalized_shrinks_without_refiting() -> None:
    parent = {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0}
    inner = {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 0.8}
    next_crop = compose_crop_normalized(parent, inner)
    assert next_crop is not None
    assert next_crop['yMax'] == pytest.approx(0.8)
    assert next_crop['xMax'] == pytest.approx(1.0)


def test_effective_crop_pixel_dimensions() -> None:
    effective = effective_crop_pixel_size(1000, 800, {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 0.5})
    assert effective['width'] == 1000.0
    assert effective['height'] == 400.0


def test_clamp_image_bounds_stays_inside_safe() -> None:
    safe = {'x_min': 0.2, 'x_max': 2.8, 'y_min': 0.2, 'y_max': 1.8}
    shifted = {'xMin': -0.5, 'xMax': 1.0, 'yMin': 0.4, 'yMax': 1.4}
    clamped = clamp_rect_to_bounds(shifted, safe)
    assert clamped['xMin'] >= safe['x_min']
    assert clamped['yMin'] >= safe['y_min']
    assert clamped['xMax'] <= safe['x_max']
    assert clamped['yMax'] <= safe['y_max']


def test_board_rect_to_canvas_rect_height_is_not_collapsed() -> None:
    board = {'x_min': 0.12, 'x_max': 2.88, 'y_min': 0.12, 'y_max': 1.88}
    image_bounds = fit_image_bounds_to_board(1000, 800, board, margin_m=0.05)
    view = board
    origin_x, origin_y, scale = 16.0, 16.0, 200.0
    canvas_rect = board_rect_to_canvas_rect(view, origin_x, origin_y, scale, image_bounds)
    assert canvas_rect['width'] > 1.0
    assert canvas_rect['height'] > 1.0


def test_placement_from_image_bounds_respects_offset_center() -> None:
    board = {'x_min': 0.0, 'x_max': 3.0, 'y_min': 0.0, 'y_max': 2.0}
    memory_w, memory_h = 1000, 800
    base = placement_from_image_bounds(
        {'xMin': 0.5, 'xMax': 2.5, 'yMin': 0.4, 'yMax': 1.6},
        memory_w,
        memory_h,
        board,
    )
    shifted = placement_from_image_bounds(
        {'xMin': 1.5, 'xMax': 3.0, 'yMin': 0.4, 'yMax': 1.6},
        memory_w,
        memory_h,
        board,
    )
    assert shifted['center_x_m'] > base['center_x_m']
    assert shifted['center_y_m'] == pytest.approx(base['center_y_m'])
    assert shifted['fit_to_safe_area'] is True


def sketch_validation_bounds(
    board: dict[str, float],
    *,
    carriage_width: float = 0.29,
    carriage_height: float = 0.20,
    pen_offset_x: float = 0.203,
    pen_offset_y: float = 0.020,
) -> dict[str, float]:
    half_w = carriage_width * 0.5
    half_h = carriage_height * 0.5
    pen = {
        'x_min': half_w + pen_offset_x,
        'x_max': float(board['width']) - half_w + pen_offset_x,
        'y_min': half_h + pen_offset_y,
        'y_max': float(board['height']) - half_h + pen_offset_y,
    }
    writable = {
        'x_min': float(board['writable_x_min']),
        'x_max': float(board['writable_x_max']),
        'y_min': float(board['writable_y_min']),
        'y_max': float(board['writable_y_max']),
    }
    safe = {
        'x_min': float(board['safe_x_min']),
        'x_max': float(board['safe_x_max']),
        'y_min': float(board['safe_y_min']),
        'y_max': float(board['safe_y_max']),
    }
    workspace = {
        'x_min': max(safe['x_min'], pen['x_min']),
        'x_max': min(safe['x_max'], pen['x_max']),
        'y_min': max(safe['y_min'], pen['y_min']),
        'y_max': min(safe['y_max'], pen['y_max']),
    }
    return {
        'x_min': max(writable['x_min'], workspace['x_min']),
        'x_max': min(writable['x_max'], workspace['x_max']),
        'y_min': max(writable['y_min'], workspace['y_min']),
        'y_max': min(writable['y_max'], workspace['y_max']),
    }


def blank_image_bounds_board(board: dict[str, float]) -> dict[str, float]:
    bounds = sketch_validation_bounds(board)
    return {
        'xMin': bounds['x_min'],
        'xMax': bounds['x_max'],
        'yMin': bounds['y_min'],
        'yMax': bounds['y_max'],
    }


def test_blank_bounds_match_sketch_validation() -> None:
    board = {
        'width': 6.3,
        'height': 3.0,
        'writable_x_min': 0.1,
        'writable_x_max': 6.2,
        'writable_y_min': 0.1,
        'writable_y_max': 2.9,
        'safe_x_min': 0.16,
        'safe_x_max': 6.14,
        'safe_y_min': 0.22,
        'safe_y_max': 2.82,
    }
    blank_bounds = blank_image_bounds_board(board)
    validation = sketch_validation_bounds(board)
    assert blank_bounds['xMin'] == pytest.approx(validation['x_min'])
    assert blank_bounds['xMax'] == pytest.approx(validation['x_max'])
    assert blank_bounds['yMin'] == pytest.approx(validation['y_min'])
    assert blank_bounds['yMax'] == pytest.approx(validation['y_max'])


def test_placement_from_ink_bounds_small_center_circle() -> None:
    board = {
        'width': 6.3,
        'height': 3.0,
        'writable_x_min': 0.1,
        'writable_x_max': 6.2,
        'writable_y_min': 0.1,
        'writable_y_max': 2.9,
        'safe_x_min': 0.16,
        'safe_x_max': 6.14,
        'safe_y_min': 0.22,
        'safe_y_max': 2.82,
    }
    validation = sketch_validation_bounds(board)
    image_bounds = blank_image_bounds_board(board)
    memory_w, memory_h = 2048, 1024
    ink_size = 120
    ink_center_x = memory_w * 0.5
    ink_center_y = memory_h * 0.5
    ink_pixel = {
        'xMin': ink_center_x - (ink_size * 0.5),
        'xMax': ink_center_x + (ink_size * 0.5),
        'yMin': ink_center_y - (ink_size * 0.5),
        'yMax': ink_center_y + (ink_size * 0.5),
        'width': ink_size,
        'height': ink_size,
    }
    placement = placement_from_ink_bounds(
        image_bounds,
        ink_pixel,
        memory_w,
        memory_h,
        validation,
        margin_m=0.0,
    )
    page_placement = placement_from_image_bounds(
        image_bounds,
        memory_w,
        memory_h,
        validation,
        margin_m=0.0,
    )
    assert placement['scale_percent'] < page_placement['scale_percent']
    assert placement['scale_percent'] < 100.0
    assert page_placement['scale_percent'] > 100.0
    assert placement['center_x_m'] == pytest.approx(
        (image_bounds['xMin'] + image_bounds['xMax']) * 0.5,
        rel=0.02,
    )
    assert placement['center_y_m'] == pytest.approx(
        (image_bounds['yMin'] + image_bounds['yMax']) * 0.5,
        rel=0.02,
    )


def raster_move_cursor_allowed(
    *,
    phase: str,
    fixed_bounds: bool,
    board_edit_mode: str | None,
    board_overlay_mode: str | None,
) -> bool:
    return (
        phase == 'edit'
        and not fixed_bounds
        and board_edit_mode is None
        and board_overlay_mode != 'crop'
    )


def test_raster_grab_cursor_blocked_on_blank() -> None:
    assert not raster_move_cursor_allowed(
        phase='edit',
        fixed_bounds=True,
        board_edit_mode=None,
        board_overlay_mode=None,
    )
    assert not raster_move_cursor_allowed(
        phase='preview_ready',
        fixed_bounds=True,
        board_edit_mode=None,
        board_overlay_mode=None,
    )
    assert raster_move_cursor_allowed(
        phase='edit',
        fixed_bounds=False,
        board_edit_mode=None,
        board_overlay_mode=None,
    )


def test_fixed_blank_uses_zero_margin() -> None:
    board = {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}
    image_bounds = {
        'xMin': board['x_min'],
        'xMax': board['x_max'],
        'yMin': board['y_min'],
        'yMax': board['y_max'],
    }
    with_margin = placement_from_image_bounds(
        image_bounds,
        2048,
        1024,
        board,
        margin_m=0.05,
    )
    without_margin = placement_from_image_bounds(
        image_bounds,
        2048,
        1024,
        board,
        margin_m=0.0,
    )
    assert without_margin['scale_percent'] < with_margin['scale_percent']


def test_placement_fit_to_safe_area_true() -> None:
    board = {'x_min': 0.0, 'x_max': 3.0, 'y_min': 0.0, 'y_max': 2.0}
    placement = placement_from_image_bounds(
        {'xMin': 0.5, 'xMax': 2.5, 'yMin': 0.4, 'yMax': 1.6},
        1000,
        800,
        board,
    )
    assert placement['fit_to_safe_area'] is True


def test_sketch_validation_bounds_matches_backend_formula() -> None:
    board = {
        'width': 6.3,
        'height': 3.0,
        'writable_x_min': 0.1,
        'writable_x_max': 6.2,
        'writable_y_min': 0.1,
        'writable_y_max': 2.9,
        'safe_x_min': 0.16,
        'safe_x_max': 6.14,
        'safe_y_min': 0.22,
        'safe_y_max': 2.82,
    }
    bounds = sketch_validation_bounds(board)
    assert bounds['x_min'] == pytest.approx(0.348)
    assert bounds['x_max'] == pytest.approx(6.14)
    assert bounds['y_min'] == pytest.approx(0.22)
    assert bounds['y_max'] == pytest.approx(2.82)


def test_board_rect_to_image_crop_center() -> None:
    normalized = board_rect_to_image_crop(
        {'xMin': 0.25, 'xMax': 0.75, 'yMin': 0.25, 'yMax': 0.75},
        {'xMin': 0.0, 'xMax': 1.0, 'yMin': 0.0, 'yMax': 1.0},
    )
    assert normalized is not None
    assert normalized['xMin'] == 0.25
    assert normalized['xMax'] == 0.75


def test_preprocess_lineart_endpoint_returns_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    png_bytes = io.BytesIO()
    Image.new('RGB', (8, 8), color=(128, 128, 128)).save(png_bytes, format='PNG')
    upload_bytes = png_bytes.getvalue()

    fake_result = PreprocessResult(
        lineart_png=upload_bytes,
        original_preview_png=upload_bytes,
        stages=[
            PreprocessStagePreview(
                stage_id='lineart',
                label='Lineart',
                image_base64='aa',
                width_px=8,
                height_px=8,
            )
        ],
        skipped_preprocess=False,
        metadata={'mode': 'photo', 'photo_lineart_model': 'informative'},
    )

    runtime = _FakeRuntime()
    app = web_server.create_app(runtime)
    monkeypatch.setattr(
        'wall_climber.http.routes.preview_draw._web_server.preprocess_image_to_lineart',
        lambda content, settings: fake_result,
    )

    client = TestClient(app)
    response = client.post(
        '/api/preprocess/lineart',
        files={'file': ('test.png', upload_bytes, 'image/png')},
        data={'settings_json': json.dumps({'image_preprocess_mode': 'photo'})},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['lineart_data_url'].startswith('data:image/png;base64,')
    assert payload['mode'] == 'photo'
