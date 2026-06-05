"""Unit tests for face-region detection and feature-preserving thresholding.

These avoid relying on real face photos: detection is asserted on a no-face
image (empty result), threshold-merge is asserted to touch only pixels inside
the supplied boxes, and graceful degradation is asserted by simulating an
unavailable Haar cascade.
"""

from __future__ import annotations

import numpy as np
import pytest

from wall_climber.image_pipeline import _face_regions
from wall_climber.image_pipeline._face_regions import (
    apply_face_preserving_threshold,
    detect_face_regions,
)


def test_detect_no_face_returns_empty() -> None:
    gray = np.full((200, 200), 200, dtype=np.uint8)  # flat, no face
    assert detect_face_regions(gray) == []


def test_apply_threshold_only_touches_inside_box() -> None:
    rng = np.random.default_rng(1234)
    gray = rng.integers(0, 256, size=(120, 160), dtype=np.uint8)
    binary = np.zeros((120, 160), dtype=np.uint8)
    box = (40, 30, 50, 40)  # x, y, w, h
    result = apply_face_preserving_threshold(
        binary, gray, [box], line_sensitivity=0.35,
    )
    x, y, w, h = box
    # Outside the box: byte-for-byte identical to the input mask.
    mask_outside = np.ones(binary.shape, dtype=bool)
    mask_outside[y:y + h, x:x + w] = False
    assert np.array_equal(result[mask_outside], binary[mask_outside])
    # Inside the box: some pixels became foreground (the finer threshold fired).
    assert np.count_nonzero(result[y:y + h, x:x + w]) > 0


def test_apply_threshold_no_boxes_is_noop() -> None:
    gray = np.full((60, 60), 128, dtype=np.uint8)
    binary = np.zeros((60, 60), dtype=np.uint8)
    binary[10:20, 10:20] = 255
    result = apply_face_preserving_threshold(
        binary, gray, [], line_sensitivity=0.35,
    )
    assert result is binary or np.array_equal(result, binary)


def test_detect_graceful_when_cascade_unavailable(monkeypatch) -> None:
    # Force the loader to behave as if the cascade is unavailable.
    monkeypatch.setattr(_face_regions, '_FACE_CASCADE', False, raising=False)
    monkeypatch.setattr(_face_regions, '_load_face_cascade', lambda: None)
    gray = np.full((100, 100), 200, dtype=np.uint8)
    assert detect_face_regions(gray) == []
