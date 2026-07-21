"""Tests for Informative Drawings tiled inference helpers."""

from __future__ import annotations

import numpy as np

from wall_climber.image_pipeline.ai_preprocess.informative_model import (
    INFORMATIVE_TILE_THRESHOLD,
    _resize_bgr_to_target_long,
)


def test_resize_bgr_to_target_long_keeps_smaller_images() -> None:
    image = np.zeros((800, 600, 3), dtype=np.uint8)
    same = _resize_bgr_to_target_long(image, 2048)
    assert same.shape == image.shape


def test_tile_threshold_constant() -> None:
    assert INFORMATIVE_TILE_THRESHOLD >= 1536
