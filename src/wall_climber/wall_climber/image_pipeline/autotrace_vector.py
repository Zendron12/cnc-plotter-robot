"""Bitmap → SVG (AutoTrace CLI, centerline mode) → board-space DrawingPathPlan."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy

from wall_climber.image_pipeline.ai_preprocess.binary_lineart import light_binarize_for_vectorizer
from wall_climber.image_pipeline._line_art import (
    flatten_photographed_line_art,
    looks_like_clean_line_art,
    looks_like_clean_line_art_any,
)
from wall_climber.image_pipeline._vector_common import (
    _decode_grayscale,
    _metrics,
    _normalize_grayscale,
    _resize_for_processing,
    _scale_strokes_to_board,
    _threshold_foreground,
)
from wall_climber.image_pipeline.types import DrawingPathPlan, PipelineMode
from wall_climber.vector_pipeline import _parse_svg_path_d

PixelStroke = tuple[tuple[float, float], ...]
_SVG_NS_PATTERN = re.compile(r'^\{[^}]+\}')


def is_autotrace_available() -> bool:
    return shutil.which('autotrace') is not None


def _svg_local_tag(tag: str) -> str:
    return _SVG_NS_PATTERN.sub('', str(tag))


def _remove_speckle_noise(bitmap: numpy.ndarray, *, strength: int = 1) -> numpy.ndarray:
    """Drop isolated ink specks before AutoTrace centerline tracing."""
    if bitmap.ndim != 2:
        raise ValueError('speckle removal expects a single-channel bitmap.')
    level = int(strength)
    if level <= 0:
        return bitmap.copy()
    ink = (bitmap == 0).astype(numpy.uint8)
    if not numpy.any(ink):
        return bitmap.copy()
    kernel_size = 3 if level <= 3 else 5
    iterations = min(level, 3) if kernel_size == 3 else max(1, level - 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel, iterations=iterations)
    return numpy.where(opened > 0, 0, 255).astype(numpy.uint8)


def _autotrace_input_from_gray(gray: numpy.ndarray) -> numpy.ndarray:
    """RGB PNG input for AutoTrace (keeps anti-aliasing for centerline mode)."""
    working = gray
    if looks_like_clean_line_art_any(gray) and not looks_like_clean_line_art(gray):
        working = flatten_photographed_line_art(gray)
    normalized = _normalize_grayscale(working)
    _, binary = cv2.threshold(
        normalized,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    if not numpy.any(binary):
        raise ValueError('AutoTrace input has no drawable ink pixels.')
    return cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)


def _thicken_preprocessed_lineart(gray: numpy.ndarray, *, iterations: int = 1) -> numpy.ndarray:
    """Widen 1px skeleton/thin AI lineart so AutoTrace centerline can trace it."""
    if gray.ndim != 2:
        raise ValueError('preprocessed bitmap must be single-channel.')
    ink = (gray == 0).astype(numpy.uint8)
    if not numpy.any(ink):
        return gray.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    dilated = cv2.dilate(ink, kernel, iterations=max(1, int(iterations)))
    return numpy.where(dilated > 0, 0, 255).astype(numpy.uint8)


def _is_purely_binary(gray: numpy.ndarray) -> bool:
    values = numpy.unique(gray)
    return values.size <= 2 and all(int(value) in (0, 255) for value in values)


def _uses_antialiased_autotrace_input(gray: numpy.ndarray) -> bool:
    """Crisp anti-aliased line art keeps gray tones; hard binary AI output does not."""
    if _is_purely_binary(gray):
        return False
    return bool(looks_like_clean_line_art_any(gray))


def _prepare_autotrace_from_preprocessed(
    preprocessed_bitmap: numpy.ndarray,
    *,
    speckle_strength: int,
) -> tuple[numpy.ndarray, numpy.ndarray, bool, bool, bool]:
    """Return processed_gray, trace_input_bgr, line_art_like, photo_flattened, use_antialiased."""
    if _uses_antialiased_autotrace_input(preprocessed_bitmap):
        processed_gray = preprocessed_bitmap.astype(numpy.uint8, copy=False)
        line_art_like = True
        photo_flattened = not looks_like_clean_line_art(processed_gray)
        trace_input = _autotrace_input_from_gray(processed_gray)
        return processed_gray, trace_input, line_art_like, photo_flattened, True

    processed_gray = light_binarize_for_vectorizer(preprocessed_bitmap)
    if speckle_strength > 0:
        processed_gray = _remove_speckle_noise(processed_gray, strength=speckle_strength)
    processed_gray = _thicken_preprocessed_lineart(processed_gray, iterations=1)
    trace_input = cv2.cvtColor(processed_gray, cv2.COLOR_GRAY2BGR)
    return processed_gray, trace_input, False, False, False


def _run_autotrace_svg(
    bitmap: numpy.ndarray,
    *,
    color_count: int = 2,
    error_threshold: float = 1.0,
    filter_iterations: int = 0,
    timeout_sec: float = 30.0,
    background_color: str | None = 'FFFFFF',
) -> str:
    binary = shutil.which('autotrace')
    if binary is None:
        raise ValueError(
            'autotrace is not installed or is not on PATH. '
            'Build from https://github.com/autotrace/autotrace or use Centerline vectorization.'
        )
    if bitmap.size == 0:
        raise ValueError('AutoTrace input image is empty.')

    with tempfile.TemporaryDirectory(prefix='wall-climber-autotrace-') as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / 'input.png'
        output_path = temp_path / 'output.svg'
        if not cv2.imwrite(str(input_path), bitmap):
            raise ValueError('Failed to write AutoTrace input bitmap.')

        command = [
            binary,
            '-centerline',
            '-output-format',
            'svg',
            '-output-file',
            str(output_path),
            '-color-count',
            str(max(2, int(color_count))),
            '-error-threshold',
            str(max(0.1, float(error_threshold))),
            '-filter-iterations',
            str(max(0, int(filter_iterations))),
        ]
        if background_color:
            command.extend(['-background-color', str(background_color)])
        command.append(str(input_path))
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(1.0, float(timeout_sec)),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f'AutoTrace timed out after {timeout_sec:.0f}s; try a lower processing resolution.'
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()
            raise ValueError(
                detail or f'AutoTrace failed with exit code {completed.returncode}.'
            )
        if not output_path.is_file():
            raise ValueError('AutoTrace did not produce an SVG output file.')
        return output_path.read_text(encoding='utf-8', errors='replace')


def _parse_svg_dimensions(root: ET.Element) -> tuple[float, float]:
    width_raw = str(root.attrib.get('width') or '').strip()
    height_raw = str(root.attrib.get('height') or '').strip()
    view_box = str(root.attrib.get('viewBox') or '').strip()
    if width_raw and height_raw:
        try:
            return float(width_raw), float(height_raw)
        except ValueError:
            pass
    if view_box:
        parts = view_box.replace(',', ' ').split()
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                pass
    raise ValueError('AutoTrace SVG is missing width/height attributes.')


def _vectorize_autotrace_svg(
    svg_text: str,
    *,
    curve_tolerance: float = 0.015,
) -> tuple[PixelStroke, ...]:
    """Parse AutoTrace centerline SVG paths into image-space pixel polylines."""
    if not svg_text.strip():
        raise ValueError('AutoTrace SVG payload is empty.')

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError(f'Invalid AutoTrace SVG XML: {exc}') from exc

    svg_width, svg_height = _parse_svg_dimensions(root)
    if svg_width <= 0.0 or svg_height <= 0.0:
        raise ValueError('AutoTrace SVG dimensions must be positive.')

    flattened: list[PixelStroke] = []
    for element in root.iter():
        if _svg_local_tag(element.tag) != 'path':
            continue
        raw_d = str(element.attrib.get('d') or '').strip()
        if not raw_d:
            continue
        for stroke in _parse_svg_path_d(
            raw_d,
            curve_tolerance=float(curve_tolerance),
            simplify_epsilon=0.0,
        ):
            # AutoTrace SVG coordinates already match image pixels (origin top-left, y down).
            image_stroke = tuple((float(x), float(y)) for x, y in stroke)
            if len(image_stroke) >= 2:
                flattened.append(image_stroke)

    if not flattened:
        raise ValueError('AutoTrace SVG produced no drawable paths.')
    return tuple(flattened)


def vectorize_autotrace_image_to_plan(
    image_bytes_or_path: bytes | bytearray | str | Path,
    *,
    board_width_m: float,
    board_height_m: float,
    margin_m: float = 0.05,
    max_image_dim: int = 0,
    scale_percent: float = 100.0,
    center_x_m: float | None = None,
    center_y_m: float | None = None,
    fit_bounds_m: dict[str, float] | None = None,
    validation_bounds_m: dict[str, float] | None = None,
    curve_tolerance: float = 0.015,
    color_count: int = 2,
    error_threshold: float = 1.0,
    filter_iterations: int = 0,
    autotrace_timeout_sec: float = 45.0,
    preprocessed_bitmap: numpy.ndarray | None = None,
    speckle_strength: int = 1,
) -> DrawingPathPlan:
    """Trace a B&W line-art raster with AutoTrace centerline mode."""

    started = time.perf_counter()
    timing: dict[str, float] = {}

    def mark(stage_started: float, key: str) -> None:
        timing[key] = (time.perf_counter() - stage_started) * 1000.0

    use_antialiased_input = False
    if preprocessed_bitmap is not None:
        if preprocessed_bitmap.ndim != 2:
            raise ValueError('preprocessed_bitmap must be a single-channel bitmap.')
        stage_started = time.perf_counter()
        (
            processed_gray,
            trace_input,
            line_art_like,
            photo_flattened,
            use_antialiased_input,
        ) = _prepare_autotrace_from_preprocessed(
            preprocessed_bitmap,
            speckle_strength=speckle_strength,
        )
        original_size = (int(processed_gray.shape[1]), int(processed_gray.shape[0]))
        source_path = None
        resize_scale = 1.0
        mark(time.perf_counter(), 'decode_time_ms')
        mark(time.perf_counter(), 'resize_time_ms')
        mark(stage_started, 'threshold_time_ms')
    else:
        stage_started = time.perf_counter()
        gray, original_size, source_path = _decode_grayscale(image_bytes_or_path)
        mark(stage_started, 'decode_time_ms')

        stage_started = time.perf_counter()
        processed_gray, resize_scale = _resize_for_processing(gray, max_image_dim=max_image_dim)
        mark(stage_started, 'resize_time_ms')

        line_art_like = looks_like_clean_line_art_any(processed_gray)
        photo_flattened = line_art_like and not looks_like_clean_line_art(processed_gray)
        use_antialiased_input = True

        stage_started = time.perf_counter()
        trace_input = _autotrace_input_from_gray(processed_gray)
        mark(stage_started, 'threshold_time_ms')

    stage_started = time.perf_counter()
    svg_text = _run_autotrace_svg(
        trace_input,
        color_count=color_count,
        error_threshold=error_threshold,
        filter_iterations=filter_iterations,
        timeout_sec=autotrace_timeout_sec,
        background_color='FFFFFF' if use_antialiased_input else None,
    )
    mark(stage_started, 'autotrace_time_ms')

    stage_started = time.perf_counter()
    svg_strokes = _vectorize_autotrace_svg(
        svg_text,
        curve_tolerance=float(curve_tolerance),
    )
    mark(stage_started, 'svg_parse_time_ms')
    if not svg_strokes:
        raise ValueError('AutoTrace produced no drawable SVG paths.')

    points_before_simplification = sum(len(stroke) for stroke in svg_strokes)

    stage_started = time.perf_counter()
    board_strokes, placement_metadata = _scale_strokes_to_board(
        svg_strokes,
        board_width_m=float(board_width_m),
        board_height_m=float(board_height_m),
        margin_m=float(margin_m),
        scale_percent=float(scale_percent),
        center_x_m=center_x_m,
        center_y_m=center_y_m,
        fit_bounds_m=fit_bounds_m,
        validation_bounds_m=validation_bounds_m,
    )
    mark(stage_started, 'scale_time_ms')

    processing_time_ms = (time.perf_counter() - started) * 1000.0
    timing['curve_fit_time_ms'] = 0.0
    timing['preview_total_time_ms'] = processing_time_ms
    processed_height, processed_width = processed_gray.shape[:2]

    metadata: dict[str, Any] = {
        'source_path': source_path,
        'vectorization_method': 'autotrace',
        'pipeline_mode': 'sketch_autotrace',
        'autotrace_available': True,
        'preprocessed_input': preprocessed_bitmap is not None,
        'autotrace_antialiased_input': bool(use_antialiased_input),
        'autotrace_centerline': True,
        'autotrace_color_count': int(color_count),
        'autotrace_error_threshold': float(error_threshold),
        'autotrace_filter_iterations': int(filter_iterations),
        'autotrace_speckle_strength': int(speckle_strength),
        'autotrace_curve_tolerance': float(curve_tolerance),
        'line_art_like': bool(line_art_like),
        'line_art_photo_flattened': bool(photo_flattened),
        'original_image_size': {'width_px': original_size[0], 'height_px': original_size[1]},
        'processed_image_size': {'width_px': int(processed_width), 'height_px': int(processed_height)},
        'resize_scale': float(resize_scale),
        'max_image_dim': int(max_image_dim),
        'raw_stroke_count': len(svg_strokes),
        'final_stroke_count': len(board_strokes),
        'autotrace_svg_characters': len(svg_text),
        'timing': {key: float(value) for key, value in timing.items()},
        **placement_metadata,
    }

    return DrawingPathPlan(
        mode=PipelineMode.SKETCH_CENTERLINE,
        frame='board',
        strokes=board_strokes,
        metrics=_metrics(
            board_strokes,
            points_before_simplification=points_before_simplification,
            processing_time_ms=processing_time_ms,
        ),
        metadata=metadata,
    )


__all__ = ['is_autotrace_available', 'vectorize_autotrace_image_to_plan']
