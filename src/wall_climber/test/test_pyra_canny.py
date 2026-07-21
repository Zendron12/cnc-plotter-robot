"""Focused tests for Pyra Canny edge fusion."""

from __future__ import annotations

import cv2  # type: ignore
import numpy as np

from wall_climber.image_pipeline.ai_preprocess.pyra_canny import pyra_canny


def test_pyra_canny_shape_and_non_empty_edges() -> None:
    gray = np.full((128, 128), 255, dtype=np.uint8)
    cv2.rectangle(gray, (24, 24), (104, 104), 0, 2)
    edges = pyra_canny(gray, low=50, high=150)
    assert edges.shape == gray.shape
    assert int(np.count_nonzero(edges)) > 0
