"""Tests for line art polarity normalization."""

from __future__ import annotations

import cv2  # type: ignore
import numpy

from wall_climber.image_pipeline.ai_preprocess.binary_lineart import (
    binary_lineart_from_gray,
    bitmap_for_vectorizer,
    ensure_black_ink_on_white,
    light_binarize_for_vectorizer,
    solid_black_lines_from_gray,
)


def test_ensure_black_ink_on_white_inverts_white_lines_on_black() -> None:
    """Informative-style output: bright strokes, dark border."""
    gray = numpy.zeros((64, 64), dtype=numpy.uint8)
    gray[20:44, 20:44] = 255
    result = ensure_black_ink_on_white(gray)
    assert int(result[0, 0]) == 255
    assert int(result[32, 32]) == 0


def test_ensure_black_ink_on_white_keeps_black_lines_on_white() -> None:
    gray = numpy.full((64, 64), 255, dtype=numpy.uint8)
    gray[20:44, 20:44] = 0
    result = ensure_black_ink_on_white(gray)
    assert int(result[0, 0]) == 255
    assert int(result[32, 32]) == 0


def test_ensure_black_ink_on_white_inverts_when_border_is_bright_but_field_is_dark() -> None:
    """Tight anime crops: bright edge pixels must not block inversion."""
    gray = numpy.zeros((128, 128), dtype=numpy.uint8)
    gray[0:8, :] = 200
    gray[:, :12] = 220
    gray[:, -12:] = 220
    for y in range(20, 108, 6):
        cv2.line(gray, (24, y), (104, y), 230, 1)
    result = ensure_black_ink_on_white(gray)
    assert int(result[62, 64]) < 64
    assert float(numpy.mean(result)) > 127.0


def test_binary_lineart_from_gray_always_has_white_paper_border() -> None:
    gray = numpy.zeros((96, 96), dtype=numpy.uint8)
    gray[30:66, 30:66] = 255
    binary = binary_lineart_from_gray(gray)
    border = numpy.concatenate((binary[0, :], binary[-1, :], binary[:, 0], binary[:, -1]))
    assert float(numpy.median(border)) >= 127.0
    assert numpy.any(binary == 0)


def test_solid_black_preserves_faint_gray_strokes_on_white_paper() -> None:
    """Coloring pages often use pale gray for mouth and fine body lines."""
    canvas = numpy.full((240, 240), 255, dtype=numpy.uint8)
    cv2.line(canvas, (40, 120), (200, 120), 0, 3, lineType=cv2.LINE_AA)
    cv2.line(canvas, (120, 95), (120, 108), 195, 1, lineType=cv2.LINE_AA)
    cv2.line(canvas, (80, 160), (160, 160), 210, 1, lineType=cv2.LINE_AA)

    binary = solid_black_lines_from_gray(canvas)
    assert int(binary[120, 100]) == 0
    assert int(binary[120, 120]) == 0
    assert int(binary[160, 120]) == 0
    assert int(binary[0, 0]) == 255


def test_solid_black_keeps_crisp_black_lines_without_eating_paper() -> None:
    canvas = numpy.full((128, 128), 255, dtype=numpy.uint8)
    cv2.rectangle(canvas, (32, 32), (96, 96), 0, 2)
    binary = solid_black_lines_from_gray(canvas)
    assert int(numpy.median(binary)) >= 200
    assert numpy.count_nonzero(binary == 0) > 0


def test_ensure_black_ink_on_white_keeps_dense_soft_lineart_on_white_paper() -> None:
    """Dense anime-style soft gray must not be treated as a dark field."""
    canvas = numpy.full((512, 512), 255, dtype=numpy.uint8)
    for y in range(0, 512, 4):
        cv2.line(canvas, (0, y), (511, y), 40, 1, lineType=cv2.LINE_AA)
    result = ensure_black_ink_on_white(canvas)
    assert float(numpy.mean(result)) > 127.0
    assert int(result[256, 256]) < 96


def test_light_binarize_soft_informative_lineart() -> None:
    soft = numpy.full((256, 256), 255, dtype=numpy.uint8)
    for y in range(32, 224, 8):
        cv2.line(soft, (40, y), (216, y), 48, 1, lineType=cv2.LINE_AA)
    assert not numpy.any(soft == 0)
    binary = light_binarize_for_vectorizer(soft)
    assert numpy.unique(binary).size <= 2
    assert numpy.any(binary == 0)
    assert int(numpy.median(binary)) >= 127


def test_light_binarize_catches_ultra_faint_strokes() -> None:
    pale = numpy.full((256, 256), 255, dtype=numpy.uint8)
    cv2.line(pale, (40, 128), (216, 128), 252, 1, lineType=cv2.LINE_AA)
    binary = light_binarize_for_vectorizer(pale)
    assert numpy.any(binary == 0)


def test_bitmap_for_vectorizer_passthrough_finalized_binary() -> None:
    """Force Solid Black output must reach Potrace unchanged."""
    canvas = numpy.full((512, 512), 255, dtype=numpy.uint8)
    for y in range(0, 512, 4):
        cv2.line(canvas, (0, y), (511, y), 40, 1, lineType=cv2.LINE_AA)
    finalized = binary_lineart_from_gray(canvas)
    traced = bitmap_for_vectorizer(finalized)
    assert numpy.array_equal(traced, finalized)
    assert numpy.count_nonzero(traced == 0) > 1000
