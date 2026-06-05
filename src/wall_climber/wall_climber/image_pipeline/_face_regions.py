"""Face-region detection and feature-preserving thresholding.

Portraits trace well for the body/scene but human faces distort: the eyes and
mouth are small, high-contrast regions that the global adaptive threshold fills
as solid blobs, and skeletonization then collapses them into dots/dashes. This
module detects face boxes and, inside those boxes only, re-thresholds with a
finer block size so eye/mouth outlines stay open instead of filling solid.

Everything degrades gracefully: if the Haar cascade is unavailable, detection
returns an empty list and the caller proceeds with standard processing. Pixels
outside detected face boxes are never modified.
"""

from __future__ import annotations

import cv2  # type: ignore
import numpy

_FaceBox = tuple[int, int, int, int]

# Cached cascade classifier (loaded once). ``False`` means "tried and failed".
_FACE_CASCADE = None


def _load_face_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE or None
    try:
        data = getattr(cv2, 'data', None)
        if data is None or not getattr(data, 'haarcascades', None):
            _FACE_CASCADE = False
            return None
        path = data.haarcascades + 'haarcascade_frontalface_default.xml'
        cascade = cv2.CascadeClassifier(path)
        if cascade.empty():
            _FACE_CASCADE = False
            return None
        _FACE_CASCADE = cascade
        return cascade
    except Exception:
        _FACE_CASCADE = False
        return None


def detect_face_regions(gray: numpy.ndarray) -> list[_FaceBox]:
    """Detect frontal-face bounding boxes ``(x, y, w, h)``.

    Returns an empty list when no face is found or the cascade is unavailable
    (never raises).
    """
    cascade = _load_face_cascade()
    if cascade is None:
        return []
    if gray.dtype != numpy.uint8:
        gray = numpy.clip(gray, 0, 255).astype(numpy.uint8)
    short_side = max(1, min(gray.shape[:2]))
    min_size = max(24, short_side // 8)
    try:
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(min_size, min_size),
        )
    except Exception:
        return []
    return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]


def apply_face_preserving_threshold(
    binary: numpy.ndarray,
    gray: numpy.ndarray,
    face_boxes: list[_FaceBox],
    *,
    line_sensitivity: float,
) -> numpy.ndarray:
    """Re-threshold inside each face box with a finer block size and OR the
    result over the global mask, touching only pixels inside the boxes.

    Returns a fresh mask; pixels outside every box are byte-for-byte identical
    to ``binary``.
    """
    if not face_boxes:
        return binary
    result = binary.copy()
    height, width = binary.shape[:2]
    sensitivity = max(0.0, min(0.95, float(line_sensitivity)))
    if gray.dtype != numpy.uint8:
        gray = numpy.clip(gray, 0, 255).astype(numpy.uint8)

    # Detect background polarity once from the global image border.
    border = numpy.concatenate(
        (gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1])
    )
    dark_foreground = float(numpy.median(border)) >= 128.0
    threshold_type = cv2.THRESH_BINARY_INV if dark_foreground else cv2.THRESH_BINARY

    for (x, y, w, h) in face_boxes:
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(width, int(x + w))
        y1 = min(height, int(y + h))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        roi = gray[y0:y1, x0:x1]
        roi_short = min(roi.shape[:2])
        # Finer block than the global adaptive threshold so small features
        # (eyes/mouth) resolve as thin outlines instead of filled blobs.
        block_size = max(7, (roi_short // 24) | 1)
        c_value = max(1.0, 7.0 - (4.0 * sensitivity))
        try:
            roi_binary = cv2.adaptiveThreshold(
                roi,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                threshold_type,
                int(block_size),
                float(c_value),
            )
        except Exception:
            continue
        # Union the finer face detail with the global mask inside the box only.
        result[y0:y1, x0:x1] = numpy.maximum(result[y0:y1, x0:x1], roi_binary)
    return result


__all__ = ['detect_face_regions', 'apply_face_preserving_threshold']
