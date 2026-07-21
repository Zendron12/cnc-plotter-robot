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

import math

import cv2  # type: ignore
import numpy

_FaceBox = tuple[int, int, int, int]

# Cached cascade classifiers (loaded once). ``False`` means "tried and failed".
_FACE_CASCADE = None
_PROFILE_CASCADE = None


def _load_haar_cascade(filename: str):
    try:
        data = getattr(cv2, 'data', None)
        if data is None or not getattr(data, 'haarcascades', None):
            return None
        path = data.haarcascades + filename
        cascade = cv2.CascadeClassifier(path)
        if cascade.empty():
            return None
        return cascade
    except Exception:
        return None


def _load_face_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE or None
    cascade = _load_haar_cascade('haarcascade_frontalface_default.xml')
    _FACE_CASCADE = cascade if cascade is not None else False
    return cascade


def _load_profile_cascade():
    global _PROFILE_CASCADE
    if _PROFILE_CASCADE is not None:
        return _PROFILE_CASCADE or None
    cascade = _load_haar_cascade('haarcascade_profileface.xml')
    _PROFILE_CASCADE = cascade if cascade is not None else False
    return cascade


def detect_face_regions(gray: numpy.ndarray) -> list[_FaceBox]:
    """Detect frontal/profile face bounding boxes ``(x, y, w, h)``.

    Returns an empty list when no face is found or the cascade is unavailable
    (never raises).
    """
    if gray.dtype != numpy.uint8:
        gray = numpy.clip(gray, 0, 255).astype(numpy.uint8)
    short_side = max(1, min(gray.shape[:2]))
    min_size = max(16, short_side // 12)
    detect_kwargs = {
        'scaleFactor': 1.08,
        'minNeighbors': 4,
        'minSize': (min_size, min_size),
    }
    faces: list[_FaceBox] = []
    for cascade_loader in (_load_face_cascade, _load_profile_cascade):
        cascade = cascade_loader()
        if cascade is None:
            continue
        try:
            detected = cascade.detectMultiScale(gray, **detect_kwargs)
        except Exception:
            continue
        faces.extend((int(x), int(y), int(w), int(h)) for (x, y, w, h) in detected)
    if not faces:
        return []
    # Drop heavily overlapping boxes; keep the larger candidate.
    faces.sort(key=lambda box: box[2] * box[3], reverse=True)
    kept: list[_FaceBox] = []
    for candidate in faces:
        cx = candidate[0] + (candidate[2] * 0.5)
        cy = candidate[1] + (candidate[3] * 0.5)
        duplicate = False
        for existing in kept:
            ex = existing[0] + (existing[2] * 0.5)
            ey = existing[1] + (existing[3] * 0.5)
            overlap_x = min(candidate[0] + candidate[2], existing[0] + existing[2]) - max(candidate[0], existing[0])
            overlap_y = min(candidate[1] + candidate[3], existing[1] + existing[3]) - max(candidate[1], existing[1])
            if overlap_x <= 0 or overlap_y <= 0:
                continue
            overlap_area = overlap_x * overlap_y
            min_area = min(candidate[2] * candidate[3], existing[2] * existing[3])
            if overlap_area >= (0.45 * min_area):
                duplicate = True
                break
            if math.hypot(cx - ex, cy - ey) <= min(candidate[2], candidate[3], existing[2], existing[3]) * 0.35:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


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
