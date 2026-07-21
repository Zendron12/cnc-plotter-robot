"""Strict binary lineart conversion for coloring-book Path B."""

from __future__ import annotations

import cv2  # type: ignore
import numpy

DEFAULT_INK_MARGIN = 18
LIGHT_VECTORIZER_INK_MARGIN = 10
VECTORIZER_INK_MARGINS = (18, 32, 48, 64, 80)


def _border_median(gray: numpy.ndarray) -> float:
    border = numpy.concatenate(
        (gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]),
    )
    if not border.size:
        return float(numpy.mean(gray))
    return float(numpy.median(border))


def _is_binary_black_on_white(gray: numpy.ndarray) -> bool:
    """True when bitmap is strict 0=ink / 255=paper (ink may touch image edges)."""
    normalized = gray.astype(numpy.uint8, copy=False)
    unique = numpy.unique(normalized)
    if unique.size > 2 or 0 not in unique or 255 not in unique:
        return False
    return float(numpy.median(normalized)) >= 127.0


def _looks_like_white_ink_on_dark_field(gray: numpy.ndarray) -> bool:
    """Detect Informative-style output: bright strokes on a predominantly dark field."""
    normalized = gray.astype(numpy.uint8, copy=False)
    if _is_binary_black_on_white(normalized):
        return False

    unique = numpy.unique(normalized)
    if unique.size <= 2 and 0 in unique and 255 in unique:
        return float(numpy.median(normalized)) < 127.0

    mean_luma = float(numpy.mean(normalized))
    border_median = _border_median(normalized)
    bright_fraction = float(numpy.mean(normalized > 160))
    height, width = normalized.shape
    margin_y = max(1, height // 20)
    margin_x = max(1, width // 20)
    interior = normalized[margin_y:-margin_y, margin_x:-margin_x]
    interior_median = float(numpy.median(interior)) if interior.size else mean_luma
    if interior_median < 96 and border_median >= 127.0:
        return True
    if border_median >= 127.0 and interior_median >= 127.0 and bright_fraction > 0.2:
        return False
    if mean_luma < 127.0:
        return True
    if border_median < 127.0:
        return True
    return False


def ensure_black_ink_on_white(gray: numpy.ndarray) -> numpy.ndarray:
    """Ensure 0=ink (black), 255=paper (white) for vectorizers."""
    if gray.ndim != 2:
        raise ValueError('ensure_black_ink_on_white expects a grayscale image.')
    normalized = gray.astype(numpy.uint8, copy=False)
    if _looks_like_white_ink_on_dark_field(normalized):
        return 255 - normalized
    return normalized


def _estimate_paper_level(gray: numpy.ndarray) -> float:
    """Estimate white-paper brightness from border and bright percentiles."""
    border = numpy.concatenate(
        (gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]),
    )
    candidates = [float(numpy.percentile(gray, 99))]
    if border.size:
        candidates.append(float(numpy.percentile(border, 95)))
    return min(255.0, max(candidates))


def _ensure_binary_paper_white(bitmap: numpy.ndarray) -> numpy.ndarray:
    """Keep 0=ink, 255=paper; flip only when the field reads as dark paper."""
    normalized = bitmap.astype(numpy.uint8, copy=False)
    if _is_binary_black_on_white(normalized):
        return normalized
    if float(numpy.median(normalized)) >= 127.0:
        return normalized
    border = numpy.concatenate(
        (normalized[0, :], normalized[-1, :], normalized[:, 0], normalized[:, -1]),
    )
    if border.size and float(numpy.median(border)) < 127.0:
        return (255 - normalized).astype(numpy.uint8)
    return normalized


def _threshold_soft_lineart(
    normalized: numpy.ndarray,
    *,
    paper_level: float,
    ink_margin: int,
    blur: bool = True,
) -> numpy.ndarray:
    threshold = max(1.0, paper_level - float(max(8, int(ink_margin))))
    source = cv2.GaussianBlur(normalized, (3, 3), 0) if blur else normalized
    ink = source < threshold
    return numpy.where(ink, 0, 255).astype(numpy.uint8)


def _adaptive_pale_stroke_threshold(normalized: numpy.ndarray, paper_level: float) -> float:
    stroke_pixels = normalized[normalized < paper_level - 1]
    if stroke_pixels.size < 4:
        return max(1.0, paper_level - float(DEFAULT_INK_MARGIN))
    peak = float(numpy.max(stroke_pixels))
    return min(paper_level, peak + 1.5)


def _adaptive_pale_stroke_bitmap(normalized: numpy.ndarray, paper_level: float) -> numpy.ndarray | None:
    stroke_pixels = normalized[normalized < paper_level - 1]
    if stroke_pixels.size < 4:
        return None
    threshold = _adaptive_pale_stroke_threshold(normalized, paper_level)
    ink = normalized < threshold
    if not numpy.any(ink):
        return None
    return numpy.where(ink, 0, 255).astype(numpy.uint8)


def solid_black_lines_from_gray(
    gray: numpy.ndarray,
    *,
    ink_margin: int = DEFAULT_INK_MARGIN,
) -> numpy.ndarray:
    """Binarize line art while keeping faint gray strokes (coloring-book safe)."""
    if gray.ndim != 2:
        raise ValueError('solid_black_lines_from_gray expects a grayscale image.')
    normalized = ensure_black_ink_on_white(gray)
    paper_level = _estimate_paper_level(normalized)
    result = _threshold_soft_lineart(
        normalized,
        paper_level=paper_level,
        ink_margin=ink_margin,
    )
    return _ensure_binary_paper_white(result)


def _coerce_bitmap_with_ink(
    gray: numpy.ndarray,
    *,
    ink_margins: tuple[int, ...],
    already_normalized: bool = False,
) -> numpy.ndarray:
    normalized = gray if already_normalized else ensure_black_ink_on_white(gray)
    paper_level = _estimate_paper_level(normalized)

    for margin in ink_margins:
        for blur in (True, False):
            result = _threshold_soft_lineart(
                normalized,
                paper_level=paper_level,
                ink_margin=margin,
                blur=blur,
            )
            result = _ensure_binary_paper_white(result)
            if numpy.any(result == 0):
                return result

    adaptive = _adaptive_pale_stroke_bitmap(normalized, paper_level)
    if adaptive is not None:
        return _ensure_binary_paper_white(adaptive)

    return _ensure_binary_paper_white(
        _threshold_soft_lineart(
            normalized,
            paper_level=paper_level,
            ink_margin=DEFAULT_INK_MARGIN,
            blur=False,
        )
    )


def binary_lineart_from_gray(gray: numpy.ndarray) -> numpy.ndarray:
    """Return black-on-white bitmap (0=ink, 255=paper)."""
    return solid_black_lines_from_gray(gray)


def light_binarize_for_vectorizer(gray: numpy.ndarray) -> numpy.ndarray:
    """Gentle single-threshold binarize for Final Lineart → Potrace/AutoTrace."""
    if gray.ndim != 2:
        raise ValueError('light_binarize_for_vectorizer expects a grayscale image.')
    normalized = gray.astype(numpy.uint8, copy=False)
    if _is_binary_black_on_white(normalized) and numpy.any(normalized == 0):
        return normalized
    paper_level = _estimate_paper_level(normalized)
    result = _threshold_soft_lineart(
        normalized,
        paper_level=paper_level,
        ink_margin=LIGHT_VECTORIZER_INK_MARGIN,
        blur=True,
    )
    if not numpy.any(result == 0):
        adaptive = _adaptive_pale_stroke_bitmap(normalized, paper_level)
        if adaptive is not None:
            result = adaptive
    if not numpy.any(result == 0):
        raise ValueError('Line art has no drawable ink pixels after light binarization.')
    return result


def bitmap_for_vectorizer(gray: numpy.ndarray) -> numpy.ndarray:
    """Binarize AI line art (often very soft gray) for Potrace/AutoTrace."""
    if gray.ndim != 2:
        raise ValueError('bitmap_for_vectorizer expects a grayscale image.')
    normalized = ensure_black_ink_on_white(gray)
    if _is_binary_black_on_white(normalized) and numpy.any(normalized == 0):
        return normalized
    result = _coerce_bitmap_with_ink(
        normalized,
        ink_margins=VECTORIZER_INK_MARGINS,
        already_normalized=True,
    )
    if not numpy.any(result == 0):
        raise ValueError(
            'Line art has no drawable ink pixels after binarization. '
            'Try enabling Force Solid Black Lines or use AutoTrace instead of Potrace.'
        )
    return result
