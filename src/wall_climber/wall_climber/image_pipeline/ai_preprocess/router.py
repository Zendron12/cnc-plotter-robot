"""Dual-path image preprocessing router with pipeline stage previews."""

from __future__ import annotations

import os
import time
from typing import Any

import cv2  # type: ignore
import numpy

from wall_climber.image_pipeline.ai_preprocess.binary_lineart import binary_lineart_from_gray
from wall_climber.image_pipeline.ai_preprocess.anilines_model import run_anilines
from wall_climber.image_pipeline.ai_preprocess.informative_model import run_informative_anime
from wall_climber.image_pipeline.ai_preprocess.nano_banana_model import run_nano_banana_lineart
from wall_climber.image_pipeline.ai_preprocess.preview_encode import (
    encode_bgr_base64,
    encode_bgr_png,
    encode_gray_base64,
    lineart_bitmap_to_png,
)
from wall_climber.image_pipeline.ai_preprocess.swinir import run_swinir_tiled
from wall_climber.image_pipeline.ai_preprocess.vram_manager import shared_gpu_slot
from wall_climber.image_pipeline.ai_preprocess.types import (
    PreprocessResult,
    PreprocessSettings,
    PreprocessStagePreview,
)


def _decode_bgr(image_bytes: bytes) -> numpy.ndarray:
    if not image_bytes:
        raise ValueError('Image payload is empty.')
    array = numpy.frombuffer(image_bytes, dtype=numpy.uint8)
    color = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if color is None:
        raise ValueError('Failed to decode PNG/JPG image payload.')
    return color


def _append_stage(
    stages: list[PreprocessStagePreview],
    *,
    stage_id: str,
    label: str,
    image: numpy.ndarray,
    color: bool,
) -> None:
    if color:
        encoded, width, height = encode_bgr_base64(image, letterbox=True, canvas_size=640)
    else:
        encoded, width, height = encode_gray_base64(image, letterbox=True, canvas_size=640)
    stages.append(
        PreprocessStagePreview(
            stage_id=stage_id,
            label=label,
            image_base64=encoded,
            width_px=width,
            height_px=height,
        )
    )


def _timing_bucket(timing_ms: dict[str, float], key: str, started: float) -> None:
    timing_ms[key] = timing_ms.get(key, 0.0) + ((time.perf_counter() - started) * 1000.0)


def _finalize_lineart(
    lineart_gray: numpy.ndarray,
    *,
    force_solid_black_lines: bool,
    timing_ms: dict[str, float],
    stages: list[PreprocessStagePreview],
) -> numpy.ndarray:
    """Optional Force Solid Black binarization for coloring-book mode."""
    if not force_solid_black_lines:
        return lineart_gray
    binary_started = time.perf_counter()
    binarized = binary_lineart_from_gray(lineart_gray)
    _timing_bucket(timing_ms, 'binary_ms', binary_started)
    _append_stage(
        stages,
        stage_id='binary',
        label='Binary Threshold',
        image=binarized,
        color=False,
    )
    return binarized


def _settings_from_mapping(settings: dict[str, Any]) -> PreprocessSettings:
    return PreprocessSettings(
        mode=settings.get('mode') or settings.get('image_preprocess_mode') or 'photo',
        raw_print=bool(settings.get('raw_print', settings.get('image_raw_print', False))),
        target_resolution=int(
            settings.get('target_resolution', settings.get('image_target_resolution', 1024))
        ),
        force_solid_black_lines=bool(
            settings.get(
                'force_solid_black_lines',
                settings.get('image_force_solid_black_lines', False),
            )
        ),
        morph_close_kernel=int(settings.get('morph_close_kernel', 3)),
        skeletonize=bool(settings.get('skeletonize', False)),
        photo_lineart_model=str(
            settings.get(
                'photo_lineart_model',
                settings.get('image_photo_lineart_model', 'informative'),
            )
        ),
        nano_banana_prompt=str(
            settings.get(
                'nano_banana_prompt',
                settings.get('image_nano_banana_prompt', ''),
            )
        ),
        google_api_key=str(
            settings.get(
                'google_api_key',
                settings.get('image_google_api_key', ''),
            )
        ),
    ).normalized()


def _resolve_google_api_key(settings: PreprocessSettings) -> str:
    return settings.google_api_key or os.environ.get('GOOGLE_API_KEY', '').strip()


def preprocess_image_to_lineart(
    image_bytes: bytes,
    settings: PreprocessSettings | dict[str, Any] | None = None,
) -> PreprocessResult:
    """Run Path A (photo) or Path B (coloring book) preprocessing.

    Photo path: SwinIR → Informative/AniLines/Nano Banana → optional Otsu → lineart.
    Coloring book AI: SwinIR → optional Otsu → lineart.
    """
    if isinstance(settings, dict):
        normalized = _settings_from_mapping(settings)
    elif settings is None:
        normalized = PreprocessSettings().normalized()
    else:
        normalized = settings.normalized()

    timing_ms: dict[str, float] = {}
    warnings: list[str] = []
    stages: list[PreprocessStagePreview] = []
    metadata: dict[str, Any] = {
        'mode': normalized.mode,
        'raw_print': normalized.raw_print,
        'target_resolution': normalized.target_resolution,
        'force_solid_black_lines': normalized.force_solid_black_lines,
        'photo_lineart_model': normalized.photo_lineart_model,
        'timing_ms': timing_ms,
        'warnings': warnings,
    }

    try:
        decode_started = time.perf_counter()
        original_bgr = _decode_bgr(image_bytes)
        _timing_bucket(timing_ms, 'decode_ms', decode_started)

        original_png, _, _ = encode_bgr_png(original_bgr)
        _append_stage(stages, stage_id='original', label='Original', image=original_bgr, color=True)

        if normalized.mode == 'coloring_book' and normalized.raw_print:
            metadata['path'] = 'coloring_book_raw_print'
            gray_started = time.perf_counter()
            gray = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2GRAY)
            _timing_bucket(timing_ms, 'grayscale_ms', gray_started)
            lineart_gray = _finalize_lineart(
                gray,
                force_solid_black_lines=normalized.force_solid_black_lines,
                timing_ms=timing_ms,
                stages=stages,
            )
            lineart_png = lineart_bitmap_to_png(lineart_gray)
            timing_ms['total_ms'] = sum(timing_ms.values())
            return PreprocessResult(
                lineart_png=lineart_png,
                original_preview_png=original_png,
                stages=stages,
                skipped_preprocess=True,
                metadata=metadata,
            )

        swinir_started = time.perf_counter()
        swinir_result = run_swinir_tiled(
            original_bgr,
            target_resolution=normalized.target_resolution,
        )
        shared_gpu_slot().unload()
        _timing_bucket(timing_ms, 'swinir_ms', swinir_started)
        metadata['swinir_model_key'] = swinir_result.model_key
        metadata['swinir_scale_applied'] = swinir_result.scale_applied
        metadata['swinir_used_cuda'] = swinir_result.used_cuda
        metadata['swinir_backend'] = swinir_result.backend
        metadata['swinir_info'] = swinir_result.info
        _append_stage(
            stages,
            stage_id='swinir',
            label='SwinIR',
            image=swinir_result.image_bgr,
            color=True,
        )

        if normalized.mode == 'photo':
            metadata['path'] = 'photo_artwork'
            metadata['photo_lineart_model'] = normalized.photo_lineart_model
            if normalized.photo_lineart_model == 'anilines_detail':
                anilines_mode = 'detail'
                anilines_started = time.perf_counter()
                anilines_result = run_anilines(
                    swinir_result.image_bgr,
                    mode=anilines_mode,
                    target_size=normalized.target_resolution,
                )
                shared_gpu_slot().unload()
                _timing_bucket(timing_ms, 'anilines_ms', anilines_started)
                metadata['anilines_model_key'] = anilines_result.model_key
                metadata['anilines_used_cuda'] = anilines_result.used_cuda
                metadata['anilines_backend'] = anilines_result.backend
                metadata['anilines_info'] = anilines_result.info
                _append_stage(
                    stages,
                    stage_id='anilines',
                    label='AniLines (detail)',
                    image=anilines_result.lineart_gray,
                    color=False,
                )
                lineart_gray = _finalize_lineart(
                    anilines_result.lineart_gray,
                    force_solid_black_lines=normalized.force_solid_black_lines,
                    timing_ms=timing_ms,
                    stages=stages,
                )
            elif normalized.photo_lineart_model == 'nano_banana':
                nano_started = time.perf_counter()
                nano_result = run_nano_banana_lineart(
                    swinir_result.image_bgr,
                    api_key=_resolve_google_api_key(normalized),
                    prompt=normalized.nano_banana_prompt,
                )
                _timing_bucket(timing_ms, 'nano_banana_ms', nano_started)
                metadata['nano_banana_model_key'] = nano_result.model_key
                metadata['nano_banana_used_cuda'] = nano_result.used_cuda
                metadata['nano_banana_backend'] = nano_result.backend
                metadata['nano_banana_info'] = nano_result.info
                _append_stage(
                    stages,
                    stage_id='nano_banana',
                    label='AI Line Art (Cloud)',
                    image=nano_result.lineart_gray,
                    color=False,
                )
                lineart_gray = _finalize_lineart(
                    nano_result.lineart_gray,
                    force_solid_black_lines=normalized.force_solid_black_lines,
                    timing_ms=timing_ms,
                    stages=stages,
                )
            else:
                informative_started = time.perf_counter()
                informative_result = run_informative_anime(
                    swinir_result.image_bgr,
                    target_size=normalized.target_resolution,
                )
                shared_gpu_slot().unload()
                _timing_bucket(timing_ms, 'informative_ms', informative_started)
                metadata['informative_model_key'] = informative_result.model_key
                metadata['informative_used_cuda'] = informative_result.used_cuda
                metadata['informative_backend'] = informative_result.backend
                metadata['informative_info'] = informative_result.info
                _append_stage(
                    stages,
                    stage_id='informative',
                    label='Informative (anime)',
                    image=informative_result.lineart_gray,
                    color=False,
                )
                lineart_gray = _finalize_lineart(
                    informative_result.lineart_gray,
                    force_solid_black_lines=normalized.force_solid_black_lines,
                    timing_ms=timing_ms,
                    stages=stages,
                )
        else:
            metadata['path'] = 'coloring_book_ai'
            gray_started = time.perf_counter()
            gray = cv2.cvtColor(swinir_result.image_bgr, cv2.COLOR_BGR2GRAY)
            _timing_bucket(timing_ms, 'grayscale_ms', gray_started)
            lineart_gray = _finalize_lineart(
                gray,
                force_solid_black_lines=normalized.force_solid_black_lines,
                timing_ms=timing_ms,
                stages=stages,
            )

        lineart_png = lineart_bitmap_to_png(lineart_gray)
        timing_ms['total_ms'] = sum(timing_ms.values())
        return PreprocessResult(
            lineart_png=lineart_png,
            original_preview_png=original_png,
            stages=stages,
            skipped_preprocess=False,
            metadata=metadata,
        )
    finally:
        shared_gpu_slot().unload()
