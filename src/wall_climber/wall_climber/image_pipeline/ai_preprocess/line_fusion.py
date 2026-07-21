"""Morphological closing and skeletonization for Path A line fusion."""

from __future__ import annotations

import cv2  # type: ignore
import numpy
from skimage.morphology import skeletonize  # type: ignore


def morph_close_lineart(
    bitmap: numpy.ndarray,
    *,
    kernel_size: int = 3,
) -> numpy.ndarray:
    """Close small gaps in a black-on-white lineart bitmap."""
    if bitmap.ndim != 2:
        raise ValueError('morph_close_lineart expects a single-channel bitmap.')
    size = max(1, int(kernel_size))
    if size % 2 == 0:
        size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    ink = bitmap == 0
    closed_ink = cv2.morphologyEx(
        ink.astype(numpy.uint8),
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1,
    )
    return numpy.where(closed_ink > 0, 0, 255).astype(numpy.uint8)


def skeletonize_lineart(
    bitmap: numpy.ndarray,
) -> numpy.ndarray:
    """Reduce thick strokes to a one-pixel-wide skeleton (black on white)."""
    if bitmap.ndim != 2:
        raise ValueError('skeletonize_lineart expects a single-channel bitmap.')
    ink = bitmap == 0
    if not numpy.any(ink):
        return bitmap.copy()
    skeleton = skeletonize(ink)
    return numpy.where(skeleton, 0, 255).astype(numpy.uint8)


def fuse_broken_lines(
    bitmap: numpy.ndarray,
    *,
    morph_close_kernel: int = 3,
    skeletonize_output: bool = True,
) -> numpy.ndarray:
    closed = morph_close_lineart(bitmap, kernel_size=morph_close_kernel)
    if not skeletonize_output:
        return closed
    return skeletonize_lineart(closed)
