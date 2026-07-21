"""Tests for pipeline preview stroke thumbnails."""

from __future__ import annotations

import base64

import cv2  # type: ignore
import numpy as np

from wall_climber.image_pipeline.ai_preprocess.preview_encode import (
    encode_gray_base64,
    letterbox_for_compare,
    rasterize_board_strokes_board_frame,
    rasterize_board_strokes_thumbnail,
    rasterize_strokes_on_lineart_frame,
)


def _decode_thumbnail(encoded: str) -> np.ndarray:
    raw = base64.b64decode(encoded)
    array = np.frombuffer(raw, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    return decoded


def test_rasterize_board_strokes_thumbnail_letterboxes_content_bounds() -> None:
    """Small centered strokes should fill the canvas instead of shrinking to board scale."""
    strokes = [
        [[3.0, 1.5], [3.2, 1.5], [3.4, 1.7]],
        [[3.1, 1.6], [3.3, 1.8]],
    ]
    encoded, width, height = rasterize_board_strokes_thumbnail(
        strokes,
        board_width_m=6.3,
        board_height_m=3.0,
        canvas_size=640,
    )
    canvas = _decode_thumbnail(encoded)
    assert width == 640 and height == 640

    ink = canvas == 0
    ys, xs = np.nonzero(ink)
    assert xs.size > 0
    ink_span_x = int(xs.max() - xs.min())
    ink_span_y = int(ys.max() - ys.min())
    assert ink_span_x > 200
    assert ink_span_y > 100


def test_rasterize_strokes_on_lineart_frame_matches_lineart_letterbox() -> None:
    lineart = np.full((400, 300), 255, dtype=np.uint8)
    cv2.line(lineart, (40, 80), (260, 320), 0, 3)

    placement = {
        'scale_m_per_px': 0.01,
        'offset_x_m': 1.0,
        'offset_y_m': 0.5,
    }
    strokes = [
        [[1.4, 1.3], [3.6, 3.7]],
    ]
    encoded, width, height = rasterize_strokes_on_lineart_frame(
        strokes,
        lineart,
        board_width_m=6.3,
        board_height_m=3.0,
        placement_metadata=placement,
        canvas_size=640,
    )
    vector_canvas = _decode_thumbnail(encoded)
    lineart_canvas = letterbox_for_compare(lineart, canvas_size=640, color=False)
    _, lineart_width, lineart_height = encode_gray_base64(
        lineart,
        letterbox=True,
        canvas_size=640,
    )

    assert width == lineart_width == 640
    assert height == lineart_height == 640

    lineart_ink = lineart_canvas == 0
    vector_ink = vector_canvas == 0
    lineart_ys, lineart_xs = np.nonzero(lineart_ink)
    vector_ys, vector_xs = np.nonzero(vector_ink)
    assert lineart_xs.size > 0 and vector_xs.size > 0

    lineart_center = (int(lineart_xs.mean()), int(lineart_ys.mean()))
    vector_center = (int(vector_xs.mean()), int(vector_ys.mean()))
    assert abs(lineart_center[0] - vector_center[0]) < 80
    assert abs(lineart_center[1] - vector_center[1]) < 80

    # Vector stage must not include the original lineart bitmap ink.
    lineart_only = lineart_ink & ~vector_ink
    assert int(np.count_nonzero(lineart_only)) > 100


def test_rasterize_board_strokes_board_frame_outputs_square_canvas() -> None:
    strokes = [[[0.5, 0.5], [1.5, 1.0]]]
    encoded, width, height = rasterize_board_strokes_board_frame(
        strokes,
        board_width_m=6.3,
        board_height_m=3.0,
        canvas_size=640,
    )
    assert width == 640 and height == 640
    canvas = _decode_thumbnail(encoded)
    assert canvas.shape == (640, 640)
