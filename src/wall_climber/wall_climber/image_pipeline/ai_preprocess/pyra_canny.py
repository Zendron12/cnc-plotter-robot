"""Multi-scale (pyramid) Canny edge fusion for photo Path A."""

from __future__ import annotations

import cv2  # type: ignore
import numpy


def _single_canny(
    gray: numpy.ndarray,
    *,
    low: int,
    high: int,
) -> numpy.ndarray:
    low_value = max(0, min(255, int(low)))
    high_value = max(low_value + 1, min(255, int(high)))
    blurred = cv2.GaussianBlur(gray, (5, 5), 1.2)
    return cv2.Canny(blurred, low_value, high_value)


def pyra_canny(
    gray: numpy.ndarray,
    *,
    low: int,
    high: int,
    scales: tuple[float, ...] = (1.0, 0.75, 0.5),
) -> numpy.ndarray:
    """Fuse Canny responses across image pyramid levels.

    Returns a uint8 edge map (255=edge, 0=background) at the input resolution.
    """
    if gray.ndim != 2:
        raise ValueError('pyra_canny expects a grayscale image.')
    height, width = gray.shape[:2]
    if height < 2 or width < 2:
        return numpy.zeros_like(gray)

    fused = numpy.zeros((height, width), dtype=numpy.uint8)
    for scale in scales:
        if scale >= 0.999:
            level = gray
        else:
            scaled_width = max(2, int(round(width * scale)))
            scaled_height = max(2, int(round(height * scale)))
            level = cv2.resize(gray, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
        edges = _single_canny(level, low=low, high=high)
        if edges.shape[:2] != (height, width):
            edges = cv2.resize(edges, (width, height), interpolation=cv2.INTER_NEAREST)
        fused = numpy.maximum(fused, edges)
    return fused


def canny_edges_to_lineart_bitmap(edges: numpy.ndarray) -> numpy.ndarray:
    """Convert fused Canny edges to black-on-white lineart (0=ink)."""
    if edges.ndim != 2:
        raise ValueError('edges must be single-channel.')
    ink = edges > 0
    return numpy.where(ink, 0, 255).astype(numpy.uint8)
