"""Bitmap → SVG (Potrace CLI) → board-space DrawingPathPlan for clean line art."""

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
from wall_climber.vector_pipeline import _parse_svg_path_d, vectorize_svg

PixelStroke = tuple[tuple[float, float], ...]
_SVG_NS_PATTERN = re.compile(r'^\{[^}]+\}')

_POTRACE_TRANSFORM_PATTERN = re.compile(
    r'translate\s*\(\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*,\s*'
    r'([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*\)\s*'
    r'scale\s*\(\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*,\s*'
    r'([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*\)',
    re.IGNORECASE,
)


def is_potrace_available() -> bool:
    return shutil.which('potrace') is not None


def _potrace_bitmap_from_gray(gray: numpy.ndarray) -> numpy.ndarray:
    """Build a black-on-white bitmap suitable for the Potrace CLI."""
    working = gray
    if looks_like_clean_line_art_any(gray) and not looks_like_clean_line_art(gray):
        working = flatten_photographed_line_art(gray)
    normalized = _normalize_grayscale(working)
    binary, _metadata = _threshold_foreground(
        normalized,
        line_sensitivity=0.0,
        sketch_extraction_method='otsu',
    )
    ink = binary > 0
    return numpy.where(ink, 0, 255).astype(numpy.uint8)


def _run_potrace_svg(
    bitmap: numpy.ndarray,
    *,
    turdsize: int = 2,
    alphamax: float = 1.0,
    opttolerance: float = 0.2,
    timeout_sec: float = 30.0,
) -> str:
    binary = shutil.which('potrace')
    if binary is None:
        raise ValueError(
            'potrace is not installed or is not on PATH. '
            'Install the potrace package (e.g. apt install potrace) or use Centerline vectorization.'
        )
    if bitmap.size == 0 or not numpy.any(bitmap == 0):
        raise ValueError('Potrace input has no drawable ink pixels.')

    with tempfile.TemporaryDirectory(prefix='wall-climber-potrace-') as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / 'input.bmp'
        output_path = temp_path / 'output.svg'
        if not cv2.imwrite(str(input_path), bitmap):
            raise ValueError('Failed to write Potrace input bitmap.')

        command = [
            binary,
            '-s',
            '-o',
            str(output_path),
            '--turdsize',
            str(max(0, int(turdsize))),
            '--alphamax',
            str(max(0.0, float(alphamax))),
            '--opttolerance',
            str(max(0.0, float(opttolerance))),
            str(input_path),
        ]
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
                f'Potrace timed out after {timeout_sec:.0f}s; try a lower processing resolution.'
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()
            raise ValueError(
                detail or f'Potrace failed with exit code {completed.returncode}.'
            )
        if not output_path.is_file():
            raise ValueError('Potrace did not produce an SVG output file.')
        return output_path.read_text(encoding='utf-8', errors='replace')


def _svg_local_tag(tag: str) -> str:
    return _SVG_NS_PATTERN.sub('', str(tag))


def _parse_potrace_group_transform(transform: str) -> tuple[float, float, float, float]:
    match = _POTRACE_TRANSFORM_PATTERN.search(str(transform or '').strip())
    if match is None:
        raise ValueError(f'Unsupported Potrace SVG transform: {transform!r}')
    return tuple(float(value) for value in match.groups())


def _apply_potrace_transform(
    stroke: PixelStroke,
    *,
    translate_x: float,
    translate_y: float,
    scale_x: float,
    scale_y: float,
) -> PixelStroke:
    return tuple(
        (
            (scale_x * float(x)) + translate_x,
            (scale_y * float(y)) + translate_y,
        )
        for x, y in stroke
    )


def _vectorize_potrace_svg(
    svg_text: str,
    *,
    curve_tolerance: float = 0.015,
) -> tuple[PixelStroke, ...]:
    """Parse Potrace SVG output, flattening its standard ``<g transform>`` wrapper."""
    if not svg_text.strip():
        raise ValueError('Potrace SVG payload is empty.')

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError(f'Invalid Potrace SVG XML: {exc}') from exc

    transform_group: ET.Element | None = None
    for element in root.iter():
        if _svg_local_tag(element.tag) != 'g':
            continue
        transform = str(element.attrib.get('transform') or '').strip()
        if transform:
            transform_group = element
            break

    if transform_group is None:
        generic = vectorize_svg(
            svg_text,
            curve_tolerance=float(curve_tolerance),
            simplify_epsilon=0.0,
        )
        return tuple(
            tuple((float(x), float(y)) for x, y in stroke)
            for stroke in generic
            if len(stroke) >= 2
        )

    translate_x, translate_y, scale_x, scale_y = _parse_potrace_group_transform(
        str(transform_group.attrib.get('transform') or '')
    )
    flattened: list[PixelStroke] = []
    for element in transform_group:
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
            transformed = _apply_potrace_transform(
                tuple((float(x), float(y)) for x, y in stroke),
                translate_x=translate_x,
                translate_y=translate_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            if len(transformed) >= 2:
                flattened.append(transformed)

    if not flattened:
        raise ValueError('Potrace SVG produced no drawable paths after flattening transforms.')
    return tuple(flattened)


def vectorize_potrace_image_to_plan(
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
    turdsize: int = 2,
    alphamax: float = 1.0,
    opttolerance: float = 0.2,
    potrace_timeout_sec: float = 30.0,
    preprocessed_bitmap: numpy.ndarray | None = None,
) -> DrawingPathPlan:
    """Trace a B&W line-art raster with Potrace and fit paths to the board."""

    started = time.perf_counter()
    timing: dict[str, float] = {}

    def mark(stage_started: float, key: str) -> None:
        timing[key] = (time.perf_counter() - stage_started) * 1000.0

    if preprocessed_bitmap is not None:
        if preprocessed_bitmap.ndim != 2:
            raise ValueError('preprocessed_bitmap must be a single-channel bitmap.')
        stage_started = time.perf_counter()
        processed_gray = light_binarize_for_vectorizer(preprocessed_bitmap)
        original_size = (int(processed_gray.shape[1]), int(processed_gray.shape[0]))
        source_path = None
        resize_scale = 1.0
        mark(time.perf_counter(), 'decode_time_ms')
        mark(time.perf_counter(), 'resize_time_ms')
        line_art_like = True
        photo_flattened = False
        potrace_bitmap = processed_gray
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

        stage_started = time.perf_counter()
        potrace_bitmap = _potrace_bitmap_from_gray(processed_gray)
        mark(stage_started, 'threshold_time_ms')

    stage_started = time.perf_counter()
    svg_text = _run_potrace_svg(
        potrace_bitmap,
        turdsize=turdsize,
        alphamax=alphamax,
        opttolerance=opttolerance,
        timeout_sec=potrace_timeout_sec,
    )
    mark(stage_started, 'potrace_time_ms')

    stage_started = time.perf_counter()
    svg_strokes = _vectorize_potrace_svg(
        svg_text,
        curve_tolerance=float(curve_tolerance),
    )
    mark(stage_started, 'svg_parse_time_ms')
    if not svg_strokes:
        raise ValueError('Potrace produced no drawable SVG paths.')

    pixel_strokes = svg_strokes
    if not pixel_strokes:
        raise ValueError('Potrace SVG paths did not contain drawable polylines.')

    points_before_simplification = sum(len(stroke) for stroke in pixel_strokes)

    stage_started = time.perf_counter()
    board_strokes, placement_metadata = _scale_strokes_to_board(
        pixel_strokes,
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
        'vectorization_method': 'potrace',
        'pipeline_mode': 'sketch_potrace',
        'potrace_available': True,
        'preprocessed_input': preprocessed_bitmap is not None,
        'potrace_turdsize': int(turdsize),
        'potrace_alphamax': float(alphamax),
        'potrace_opttolerance': float(opttolerance),
        'potrace_curve_tolerance': float(curve_tolerance),
        'line_art_like': bool(line_art_like),
        'line_art_photo_flattened': bool(photo_flattened),
        'original_image_size': {'width_px': original_size[0], 'height_px': original_size[1]},
        'processed_image_size': {'width_px': int(processed_width), 'height_px': int(processed_height)},
        'resize_scale': float(resize_scale),
        'max_image_dim': int(max_image_dim),
        'raw_stroke_count': len(pixel_strokes),
        'final_stroke_count': len(board_strokes),
        'potrace_svg_characters': len(svg_text),
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


__all__ = ['is_potrace_available', 'vectorize_potrace_image_to_plan']
