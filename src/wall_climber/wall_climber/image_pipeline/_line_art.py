"""Heuristics and mask cleanup for digital line art / coloring-book input.

Raster uploads that are already clean black-on-white drawings should not go
through the scan-oriented preprocessing chain (CLAHE, unsharp, background
flatten). This module detects that case and applies a light morphological
cleanup so anti-aliased edges skeletonize as connected strokes instead of
noisy pixel fragments.

Camera photos of coloring-book pages often fail the strict digital heuristic
because of vignetting and uneven lighting. ``flatten_photographed_line_art``
normalizes those photos so they can be classified and thresholded like a scan.
"""

from __future__ import annotations

import cv2  # type: ignore
import numpy


def _border_pixels(gray: numpy.ndarray) -> numpy.ndarray:
    return numpy.concatenate((gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]))


def flatten_photographed_line_art(gray: numpy.ndarray) -> numpy.ndarray:
    """Reduce lighting gradients from camera photos of coloring-book pages."""
    if gray.dtype != numpy.uint8:
        gray = numpy.clip(gray, 0, 255).astype(numpy.uint8)
    height, width = gray.shape[:2]
    if height < 32 or width < 32:
        return gray.copy()

    max_kernel = max(3, min(81, min(height, width) // 6))
    kernel_size = max_kernel if max_kernel % 2 == 1 else max_kernel - 1
    if kernel_size < 3:
        return gray.copy()

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    background = numpy.maximum(background, 1)
    flattened = cv2.divide(gray, background, scale=255)
    low, high = numpy.percentile(flattened, (1.0, 99.0))
    if high - low <= 1.0:
        return flattened.astype(numpy.uint8)
    stretched = (flattened.astype(numpy.float32) - float(low)) * (255.0 / float(high - low))
    return numpy.clip(stretched, 0.0, 255.0).astype(numpy.uint8)


def looks_like_clean_line_art(
    gray: numpy.ndarray,
    *,
    photo_relaxed: bool = False,
) -> bool:
    """Return True when ``gray`` looks like digital B&W line art or a coloring page.

    The heuristic is intentionally conservative: a false negative falls back to
    the general ``detail`` scan pipeline; a false positive would skip useful
    preprocessing on a faint scan.

    When ``photo_relaxed`` is True, thresholds are loosened slightly for images
    that were flattened after a camera capture.
    """
    if gray.dtype != numpy.uint8:
        gray = numpy.clip(gray, 0, 255).astype(numpy.uint8)
    height, width = gray.shape[:2]
    if height < 32 or width < 32:
        return False

    border_min = 150.0 if photo_relaxed else 175.0
    bimodal_min = 0.60 if photo_relaxed else 0.72
    paper_std_max = 36.0 if photo_relaxed else 28.0
    near_black_min = 0.003 if photo_relaxed else 0.004
    near_black_max = 0.10 if photo_relaxed else 0.08

    border_median = float(numpy.median(_border_pixels(gray)))
    if border_median < border_min:
        return False

    step = max(1, min(height, width) // 256)
    sample = gray[::step, ::step].ravel()
    if sample.size < 64:
        return False

    near_white = float(numpy.sum(sample >= 235)) / float(sample.size)
    near_black = float(numpy.sum(sample <= 35)) / float(sample.size)
    bimodal = near_white + near_black
    if bimodal < bimodal_min:
        return False

    if near_black < near_black_min or near_black > near_black_max:
        return False

    paper = sample[sample >= 200]
    if paper.size >= 32 and float(numpy.std(paper)) > paper_std_max:
        return False

    return True


def looks_like_clean_line_art_any(gray: numpy.ndarray) -> bool:
    """Detect digital or photographed coloring-book / line-art pages."""
    if looks_like_clean_line_art(gray):
        return True
    flattened = flatten_photographed_line_art(gray)
    return looks_like_clean_line_art(flattened, photo_relaxed=True)


def normalize_line_art_binary(
    binary: numpy.ndarray,
    *,
    max_half_width_px: float = 1.75,
) -> numpy.ndarray:
    """Trim anti-alias halos and wide AI strokes before skeletonization.

    Only applied when the foreground median half-width exceeds ~2 px; thin
    coloring-book strokes are left untouched because trimming them creates a
    noisy skeleton with hundreds of micro-fragments.
    """
    if binary.size == 0 or not numpy.any(binary):
        return binary
    foreground = (binary > 0).astype(numpy.uint8)
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 3)
    positive = distance[distance > 0.0]
    if positive.size == 0:
        return binary.astype(numpy.uint8)
    if float(numpy.percentile(positive, 90)) <= 3.0:
        return binary.astype(numpy.uint8)

    radius = max(0.75, float(max_half_width_px))
    trimmed = numpy.zeros_like(foreground)
    trimmed[(distance > 0.0) & (distance <= radius)] = 1
    if not numpy.any(trimmed):
        return binary.astype(numpy.uint8)
    kernel = numpy.ones((2, 2), dtype=numpy.uint8)
    closed = cv2.morphologyEx(trimmed * 255, cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed.astype(numpy.uint8)


def connect_line_art_gaps(binary: numpy.ndarray) -> numpy.ndarray:
    """Close single-pixel gaps from anti-aliasing without thickening strokes much."""
    if not numpy.any(binary):
        return binary
    kernel = numpy.ones((2, 2), dtype=numpy.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed.astype(numpy.uint8)


__all__ = [
    'connect_line_art_gaps',
    'flatten_photographed_line_art',
    'looks_like_clean_line_art',
    'looks_like_clean_line_art_any',
    'normalize_line_art_binary',
]
