"""Shared bitmap decode, threshold, and board-scaling helpers for CLI vectorizers."""

from __future__ import annotations

import math
from pathlib import Path

import cv2  # type: ignore
import numpy

from wall_climber.image_pipeline.types import PipelineMetrics, Point2D, Stroke

_EPS = 1.0e-9

Pixel = tuple[int, int]
PixelStroke = tuple[Pixel, ...]
SmoothPixel = tuple[float, float]
SmoothPixelStroke = tuple[SmoothPixel, ...]
BoundsM = dict[str, float]

def _read_image_payload(image_bytes_or_path: bytes | bytearray | str | Path) -> tuple[bytes, str | None]:
    if isinstance(image_bytes_or_path, (bytes, bytearray)):
        return bytes(image_bytes_or_path), None
    if isinstance(image_bytes_or_path, (str, Path)):
        path = Path(image_bytes_or_path)
        return path.read_bytes(), str(path)
    raise TypeError('image_bytes_or_path must be bytes, str, or pathlib.Path.')


def _decode_grayscale(
    image_bytes_or_path: bytes | bytearray | str | Path,
) -> tuple[numpy.ndarray, tuple[int, int], str | None]:
    payload, source_path = _read_image_payload(image_bytes_or_path)
    if not payload:
        raise ValueError('Image payload is empty.')
    array = numpy.frombuffer(payload, dtype=numpy.uint8)
    color = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if color is None:
        raise ValueError('Failed to decode PNG/JPG image payload.')
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    return gray, (int(width), int(height)), source_path


def _resize_for_processing(gray: numpy.ndarray, *, max_image_dim: int) -> tuple[numpy.ndarray, float]:
    if max_image_dim <= 0:
        return gray, 1.0
    height, width = gray.shape[:2]
    longest = max(width, height)
    if longest <= max_image_dim:
        return gray, 1.0
    scale = float(max_image_dim) / float(longest)
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    return cv2.resize(gray, (resized_width, resized_height), interpolation=cv2.INTER_AREA), scale


def _normalize_grayscale(gray: numpy.ndarray) -> numpy.ndarray:
    low, high = numpy.percentile(gray, (2.0, 98.0))
    if high - low <= 1.0:
        return gray.copy()
    stretched = (gray.astype(numpy.float32) - float(low)) * (255.0 / float(high - low))
    return numpy.clip(stretched, 0.0, 255.0).astype(numpy.uint8)


def _border_pixels(gray: numpy.ndarray) -> numpy.ndarray:
    if gray.size == 0:
        return gray.reshape((0,))
    return numpy.concatenate((gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]))


def _otsu_foreground(gray: numpy.ndarray, *, line_sensitivity: float) -> tuple[numpy.ndarray, dict[str, object]]:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    threshold, _unused = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    sensitivity = max(0.0, min(0.95, float(line_sensitivity)))
    border_median = float(numpy.median(_border_pixels(blurred)))
    dark_foreground = border_median >= float(threshold)
    if dark_foreground:
        effective_threshold = float(threshold) + ((255.0 - float(threshold)) * sensitivity)
    else:
        effective_threshold = float(threshold) * (1.0 - sensitivity)
    effective_threshold = max(0.0, min(255.0, effective_threshold))
    threshold_type = cv2.THRESH_BINARY_INV if dark_foreground else cv2.THRESH_BINARY
    _threshold, binary = cv2.threshold(blurred, effective_threshold, 255, threshold_type)
    return binary.astype(numpy.uint8), {
        'threshold_method': 'otsu',
        'threshold_value': float(effective_threshold),
        'otsu_threshold_value': float(threshold),
        'effective_threshold_value': float(effective_threshold),
        'line_sensitivity': sensitivity,
        'foreground_polarity': 'dark_on_light' if dark_foreground else 'light_on_dark',
        'border_median': border_median,
    }


def _background_flattened_grayscale(gray: numpy.ndarray) -> numpy.ndarray:
    height, width = gray.shape[:2]
    max_kernel = max(3, min(61, min(height, width) // 8))
    kernel_size = max_kernel if max_kernel % 2 == 1 else max_kernel - 1
    if kernel_size < 3:
        return gray.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    flattened = gray.astype(numpy.int16) + (255 - background.astype(numpy.int16))
    return numpy.clip(flattened, 0, 255).astype(numpy.uint8)


def _hysteresis_ink_foreground(
    gray: numpy.ndarray,
    *,
    line_sensitivity: float,
) -> tuple[numpy.ndarray, dict[str, object]]:
    flattened = _background_flattened_grayscale(gray)
    normalized = _normalize_grayscale(flattened)
    blurred = cv2.GaussianBlur(normalized, (3, 3), 0)
    border_median = float(numpy.median(_border_pixels(blurred)))
    dark_on_light = border_median >= 128.0
    inkness = (255 - blurred) if dark_on_light else blurred
    nonzero = inkness[inkness > 0]
    if nonzero.size == 0:
        fallback, metadata = _otsu_foreground(gray, line_sensitivity=line_sensitivity)
        metadata['threshold_method'] = 'hysteresis_ink_fallback_otsu'
        metadata['hysteresis_fallback_reason'] = 'empty_inkness'
        return fallback, metadata

    sensitivity = max(0.0, min(0.95, float(line_sensitivity)))
    strong_percentile = float(numpy.percentile(nonzero, 88.0))
    weak_percentile = float(numpy.percentile(nonzero, 62.0))
    strong_threshold = max(24.0, strong_percentile) * (1.0 - 0.32 * sensitivity)
    weak_threshold = max(8.0, min(weak_percentile, strong_threshold * 0.55)) * (1.0 - 0.42 * sensitivity)
    strong_threshold = max(10.0, min(255.0, float(strong_threshold)))
    weak_threshold = max(4.0, min(strong_threshold, float(weak_threshold)))

    strong = (inkness >= strong_threshold).astype(numpy.uint8)
    weak = (inkness >= weak_threshold).astype(numpy.uint8)
    strong_count = int(numpy.count_nonzero(strong))
    if strong_count == 0:
        fallback, metadata = _otsu_foreground(gray, line_sensitivity=line_sensitivity)
        metadata['threshold_method'] = 'hysteresis_ink_fallback_otsu'
        metadata['hysteresis_fallback_reason'] = 'no_strong_ink'
        metadata['strong_ink_threshold'] = float(strong_threshold)
        metadata['weak_ink_threshold'] = float(weak_threshold)
        return fallback, metadata

    kernel = numpy.ones((3, 3), dtype=numpy.uint8)
    keep = strong.copy()
    for _iteration in range(64):
        expanded = cv2.dilate(keep, kernel, iterations=1)
        expanded = numpy.logical_and(expanded > 0, weak > 0).astype(numpy.uint8)
        if numpy.array_equal(expanded, keep):
            break
        keep = expanded
    closed = cv2.morphologyEx((keep * 255).astype(numpy.uint8), cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed.astype(numpy.uint8), {
        'threshold_method': 'hysteresis_ink',
        'threshold_value': float(weak_threshold),
        'effective_threshold_value': float(weak_threshold),
        'strong_ink_threshold': float(strong_threshold),
        'weak_ink_threshold': float(weak_threshold),
        'line_sensitivity': sensitivity,
        'foreground_polarity': 'dark_on_light' if dark_on_light else 'light_on_dark',
        'border_median': border_median,
        'strong_ink_pixel_count': strong_count,
        'weak_ink_pixel_count': int(numpy.count_nonzero(weak)),
        'connected_ink_pixel_count': int(numpy.count_nonzero(closed)),
    }


def _adaptive_foreground(gray: numpy.ndarray, *, line_sensitivity: float) -> tuple[numpy.ndarray, dict[str, object]]:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    border_median = float(numpy.median(_border_pixels(blurred)))
    dark_foreground = border_median >= 128.0
    block_size = max(15, min(61, (min(gray.shape[:2]) // 16) | 1))
    sensitivity = max(0.0, min(0.95, float(line_sensitivity)))
    c_value = max(1.0, 9.0 - (6.0 * sensitivity))
    threshold_type = cv2.THRESH_BINARY_INV if dark_foreground else cv2.THRESH_BINARY
    binary = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        threshold_type,
        int(block_size),
        float(c_value),
    )
    return binary.astype(numpy.uint8), {
        'threshold_method': 'adaptive',
        'adaptive_block_size': int(block_size),
        'adaptive_c_value': float(c_value),
        'line_sensitivity': sensitivity,
        'foreground_polarity': 'dark_on_light' if dark_foreground else 'light_on_dark',
        'border_median': border_median,
    }


def _threshold_foreground(
    gray: numpy.ndarray,
    *,
    line_sensitivity: float,
    sketch_extraction_method: str = 'adaptive',
) -> tuple[numpy.ndarray, dict[str, object]]:
    method = str(sketch_extraction_method or 'adaptive').strip().lower()
    if method == 'hysteresis_ink':
        return _hysteresis_ink_foreground(gray, line_sensitivity=line_sensitivity)
    if method == 'otsu':
        return _otsu_foreground(gray, line_sensitivity=line_sensitivity)
    if method == 'adaptive':
        return _adaptive_foreground(gray, line_sensitivity=line_sensitivity)
    raise ValueError("sketch_extraction_method must be one of: hysteresis_ink, otsu, adaptive.")

def _normalize_bounds_m(
    bounds: BoundsM | None,
    *,
    default: BoundsM,
    field_name: str,
) -> BoundsM:
    raw = default if bounds is None else bounds
    required = ('x_min', 'x_max', 'y_min', 'y_max')
    try:
        normalized = {key: float(raw[key]) for key in required}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must contain x_min, x_max, y_min, and y_max.') from exc
    if not all(math.isfinite(value) for value in normalized.values()):
        raise ValueError(f'{field_name} coordinates must be finite.')
    if normalized['x_max'] <= normalized['x_min'] or normalized['y_max'] <= normalized['y_min']:
        raise ValueError(f'{field_name} must define a non-empty rectangle.')
    return normalized


def _bounds_metadata(bounds: BoundsM) -> dict[str, float]:
    return {
        'x_min': float(bounds['x_min']),
        'x_max': float(bounds['x_max']),
        'y_min': float(bounds['y_min']),
        'y_max': float(bounds['y_max']),
        'width': float(bounds['x_max'] - bounds['x_min']),
        'height': float(bounds['y_max'] - bounds['y_min']),
    }


def _scale_strokes_to_board(
    pixel_strokes: tuple[PixelStroke | SmoothPixelStroke, ...],
    *,
    board_width_m: float,
    board_height_m: float,
    margin_m: float,
    scale_percent: float,
    center_x_m: float | None,
    center_y_m: float | None,
    fit_bounds_m: BoundsM | None = None,
    validation_bounds_m: BoundsM | None = None,
) -> tuple[tuple[Stroke, ...], dict[str, object]]:
    if board_width_m <= 0.0 or board_height_m <= 0.0:
        raise ValueError('board_width_m and board_height_m must be > 0.')
    if margin_m < 0.0:
        raise ValueError('margin_m must be >= 0.')
    board_bounds = {
        'x_min': 0.0,
        'x_max': float(board_width_m),
        'y_min': 0.0,
        'y_max': float(board_height_m),
    }
    fit_bounds = _normalize_bounds_m(fit_bounds_m, default=board_bounds, field_name='fit_bounds_m')
    validation_bounds = _normalize_bounds_m(
        validation_bounds_m,
        default=board_bounds,
        field_name='validation_bounds_m',
    )
    available_width = (fit_bounds['x_max'] - fit_bounds['x_min']) - (2.0 * float(margin_m))
    available_height = (fit_bounds['y_max'] - fit_bounds['y_min']) - (2.0 * float(margin_m))
    if available_width <= 0.0 or available_height <= 0.0:
        raise ValueError('margin_m leaves no drawable fit area.')

    points = [point for stroke in pixel_strokes for point in stroke]
    if not points:
        raise ValueError('No skeleton strokes were traced from the image.')
    min_x = min(float(point[0]) for point in points)
    max_x = max(float(point[0]) for point in points)
    min_y = min(float(point[1]) for point in points)
    max_y = max(float(point[1]) for point in points)
    source_width = max_x - min_x
    source_height = max_y - min_y
    if source_width <= _EPS and source_height <= _EPS:
        raise ValueError('Traced sketch geometry is degenerate.')
    if scale_percent <= 0.0:
        raise ValueError('scale_percent must be > 0.')

    scale_candidates = []
    if source_width > _EPS:
        scale_candidates.append(available_width / source_width)
    if source_height > _EPS:
        scale_candidates.append(available_height / source_height)
    base_scale = min(scale_candidates)
    scale = base_scale * (float(scale_percent) / 100.0)
    fitted_width = source_width * scale
    fitted_height = source_height * scale
    auto_center_x = fit_bounds['x_min'] + float(margin_m) + (available_width * 0.5)
    auto_center_y = fit_bounds['y_min'] + float(margin_m) + (available_height * 0.5)
    target_center_x = auto_center_x if center_x_m is None else float(center_x_m)
    target_center_y = auto_center_y if center_y_m is None else float(center_y_m)
    source_center_x = (min_x + max_x) * 0.5
    source_center_y = (min_y + max_y) * 0.5
    offset_x = target_center_x - (source_center_x * scale)
    offset_y = target_center_y - (source_center_y * scale)

    strokes: list[Stroke] = []
    for index, pixel_stroke in enumerate(pixel_strokes):
        board_points: list[Point2D] = []
        for point in pixel_stroke:
            board_point = Point2D(
                x=(float(point[0]) * scale) + offset_x,
                y=(float(point[1]) * scale) + offset_y,
            )
            if board_points and board_points[-1] == board_point:
                continue
            board_points.append(board_point)
        if len(board_points) >= 2:
            strokes.append(Stroke(points=tuple(board_points), pen_down=True, label=f'sketch_stroke_{index}'))

    if not strokes:
        raise ValueError('No non-degenerate sketch strokes remained after scaling.')

    board_points = [point for stroke in strokes for point in stroke.points]
    min_board_x = min(point.x for point in board_points)
    max_board_x = max(point.x for point in board_points)
    min_board_y = min(point.y for point in board_points)
    max_board_y = max(point.y for point in board_points)
    if (
        min_board_x < validation_bounds['x_min'] - 1.0e-7
        or min_board_y < validation_bounds['y_min'] - 1.0e-7
        or max_board_x > validation_bounds['x_max'] + 1.0e-7
        or max_board_y > validation_bounds['y_max'] + 1.0e-7
    ):
        raise ValueError(
            'Sketch placement is outside the robot-safe drawable bounds; reduce scale_percent '
            'or choose a center_x_m/center_y_m inside the safe drawable area.'
        )

    return tuple(strokes), {
        'source_bounds_px': {
            'x_min': min_x,
            'x_max': max_x,
            'y_min': min_y,
            'y_max': max_y,
        },
        'base_scale_m_per_px': float(base_scale),
        'scale_m_per_px': float(scale),
        'fitted_width_m': float(fitted_width),
        'fitted_height_m': float(fitted_height),
        'scale_percent': float(scale_percent),
        'center_x_m': float(target_center_x),
        'center_y_m': float(target_center_y),
        'requested_center_x_m': None if center_x_m is None else float(center_x_m),
        'requested_center_y_m': None if center_y_m is None else float(center_y_m),
        'offset_x_m': float(offset_x),
        'offset_y_m': float(offset_y),
        'offset_m': {'x': float(offset_x), 'y': float(offset_y)},
        'board_size_m': {'width': float(board_width_m), 'height': float(board_height_m)},
        'fit_bounds_m': _bounds_metadata(fit_bounds),
        'validation_bounds_m': _bounds_metadata(validation_bounds),
        'margin_m': float(margin_m),
    }


def _drawing_length(points: tuple[Point2D, ...]) -> float:
    return sum(
        math.hypot(end.x - start.x, end.y - start.y)
        for start, end in zip(points[:-1], points[1:])
    )


def _metrics(
    strokes: tuple[Stroke, ...],
    *,
    points_before_simplification: int,
    processing_time_ms: float,
    warnings: tuple[str, ...] = (),
) -> PipelineMetrics:
    total_drawing_length = sum(_drawing_length(stroke.points) for stroke in strokes)
    pen_up_travel = 0.0
    for previous, current in zip(strokes[:-1], strokes[1:]):
        pen_up_travel += math.hypot(
            current.points[0].x - previous.points[-1].x,
            current.points[0].y - previous.points[-1].y,
        )
    return PipelineMetrics(
        stroke_count=len(strokes),
        points_before_simplification=points_before_simplification,
        points_after_simplification=sum(len(stroke.points) for stroke in strokes),
        total_drawing_length_m=total_drawing_length,
        pen_up_travel_length_m=pen_up_travel,
        pen_lift_count=len(strokes),
        processing_time_ms=processing_time_ms,
        warnings=warnings,
    )
