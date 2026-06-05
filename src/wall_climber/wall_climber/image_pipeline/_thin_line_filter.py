"""Thin-line filter for sketch / line-art input.

The physical robot pen has a FIXED stroke width (``pen.tip_radius = 0.003 m`` ->
~6 mm). Source images often contain hairline detail that is thinner than the pen
can render; tracing those lines produces wobbly, unconvincing strokes. This
module removes regions whose *local* stroke width is below a threshold, measured
on the binary mask BEFORE skeletonization (where width information still exists;
the skeleton stage discards it).

Approach (distance transform):
  1. ``cv2.distanceTransform`` gives, for every foreground pixel, the distance to
     the nearest background pixel. For a stroke of width ``w`` the centre-line
     pixels reach a value of about ``w / 2``.
  2. A pixel is part of a "thick enough" core when ``2 * dist >= threshold``.
  3. We keep a connected component only if it contains at least one core pixel,
     i.e. the stroke reaches the threshold width somewhere. This removes hairline
     components entirely instead of chopping thick strokes into pieces.

The geometry of retained components is never altered; only whole components are
kept or dropped. A ``threshold <= 0`` is a no-op (filtering disabled).
"""

from __future__ import annotations

import cv2  # type: ignore
import numpy


def filter_thin_lines(
    binary_mask: numpy.ndarray,
    *,
    min_stroke_width_px: float,
) -> tuple[numpy.ndarray, dict[str, object]]:
    """Drop connected components whose local stroke width never reaches the
    threshold.

    Args:
        binary_mask: uint8 mask, foreground non-zero.
        min_stroke_width_px: minimum stroke width (in pixels) that a component
            must reach somewhere to be kept. ``<= 0`` disables filtering.

    Returns:
        ``(filtered_mask, metadata)`` where ``filtered_mask`` is a fresh uint8
        array (0 / 255) the same shape as the input.
    """
    mask = (binary_mask > 0).astype(numpy.uint8)
    total_components = _count_components(mask)
    metadata: dict[str, object] = {
        'thin_line_min_width_px': float(max(0.0, float(min_stroke_width_px))),
        'components_total': int(total_components),
        'components_kept': int(total_components),
        'components_removed': 0,
        'filter_applied': False,
    }

    # Disabled: keep every non-zero-width component exactly as-is.
    if min_stroke_width_px is None or float(min_stroke_width_px) <= 0.0:
        return (mask * 255).astype(numpy.uint8), metadata

    if not numpy.any(mask):
        return numpy.zeros_like(mask, dtype=numpy.uint8), metadata

    # distanceTransform: value = distance to nearest zero pixel = half local width.
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    core = (2.0 * dist >= float(min_stroke_width_px)).astype(numpy.uint8)

    component_count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    kept = numpy.zeros_like(mask, dtype=numpy.uint8)
    kept_count = 0
    # Labels that own at least one core pixel reach the threshold width somewhere.
    core_labels = set(numpy.unique(labels[core > 0]).tolist())
    core_labels.discard(0)
    for label in range(1, component_count):
        if label in core_labels:
            kept[labels == label] = 255
            kept_count += 1

    real_total = max(0, int(component_count) - 1)
    metadata.update(
        {
            'components_total': int(real_total),
            'components_kept': int(kept_count),
            'components_removed': int(real_total - kept_count),
            'filter_applied': True,
        }
    )
    return kept.astype(numpy.uint8), metadata


def _count_components(mask: numpy.ndarray) -> int:
    if not numpy.any(mask):
        return 0
    component_count, _labels = cv2.connectedComponents(mask, connectivity=8)
    return max(0, int(component_count) - 1)


__all__ = ['filter_thin_lines']
