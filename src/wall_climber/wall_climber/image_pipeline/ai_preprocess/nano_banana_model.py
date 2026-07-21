"""Gemini Nano Banana line art extraction for Photo path preprocessing."""

from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass

import cv2  # type: ignore
import numpy

_LOGGER = logging.getLogger(__name__)

NANO_BANANA_MODEL_KEY = 'nano_banana'
DEFAULT_NANO_BANANA_MODEL = 'gemini-3.1-flash-image'
DEFAULT_NANO_BANANA_PROMPT = (
    'convert to clean black and white line art, coloring book style, '
    'minimal details, no shading'
)


class NanoBananaModelError(RuntimeError):
    """Gemini Nano Banana is unavailable or returned no image."""


@dataclass(frozen=True)
class NanoBananaRunResult:
    lineart_gray: numpy.ndarray
    model_key: str
    used_cuda: bool
    elapsed_ms: float
    backend: str
    info: str


def _resolve_model_name() -> str:
    override = os.environ.get('WALL_CLIMBER_NANO_BANANA_MODEL', '').strip()
    return override or DEFAULT_NANO_BANANA_MODEL


def _bgr_to_pil(image_bgr: numpy.ndarray):
    from PIL import Image

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _pil_to_gray(image) -> numpy.ndarray:
    from PIL import Image

    if image.mode != 'L':
        image = image.convert('L')
    return numpy.array(image, dtype=numpy.uint8)


def _extract_response_image(response):
    parts = getattr(response, 'parts', None)
    if parts is None:
        candidates = getattr(response, 'candidates', None) or []
        if candidates:
            content = getattr(candidates[0], 'content', None)
            parts = getattr(content, 'parts', None) if content is not None else None
    if not parts:
        raise NanoBananaModelError('Cloud service returned no content.')

    for part in parts:
        inline_data = getattr(part, 'inline_data', None)
        if inline_data is not None and getattr(inline_data, 'data', None):
            from PIL import Image

            mime = getattr(inline_data, 'mime_type', '') or 'image/png'
            payload = inline_data.data
            if isinstance(payload, str):
                import base64

                payload = base64.b64decode(payload)
            return Image.open(io.BytesIO(payload)).convert('RGB')

        as_image = getattr(part, 'as_image', None)
        if callable(as_image):
            try:
                return as_image().convert('RGB')
            except Exception:
                continue

    raise NanoBananaModelError('Cloud service returned no image output.')


def run_nano_banana_lineart(
    image_bgr: numpy.ndarray,
    *,
    api_key: str,
    prompt: str | None = None,
    model: str | None = None,
) -> NanoBananaRunResult:
    """Map a BGR photo (typically SwinIR output) to line art via Gemini."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        raise ValueError('run_nano_banana_lineart expects a BGR color image.')

    resolved_key = str(api_key or '').strip()
    if not resolved_key:
        raise NanoBananaModelError(
            'Cloud API key is required for AI Line Art (Cloud). '
            'Add one in File settings or set GOOGLE_API_KEY on the server.'
        )

    resolved_prompt = str(prompt or '').strip() or DEFAULT_NANO_BANANA_PROMPT
    resolved_model = str(model or '').strip() or _resolve_model_name()
    started = time.perf_counter()

    try:
        from google import genai
    except ImportError as exc:
        raise NanoBananaModelError(
            'google-genai is not installed. Run `pip install google-genai`.'
        ) from exc

    client = genai.Client(api_key=resolved_key)
    source_image = _bgr_to_pil(image_bgr)
    try:
        response = client.models.generate_content(
            model=resolved_model,
            contents=[resolved_prompt, source_image],
        )
    except Exception as error:
        raise NanoBananaModelError(f'Cloud line art request failed: {error}') from error

    edited_rgb = _extract_response_image(response)
    lineart_gray = _pil_to_gray(edited_rgb)
    if lineart_gray.shape[:2] != image_bgr.shape[:2]:
        lineart_gray = cv2.resize(
            lineart_gray,
            (int(image_bgr.shape[1]), int(image_bgr.shape[0])),
            interpolation=cv2.INTER_LINEAR,
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return NanoBananaRunResult(
        lineart_gray=lineart_gray,
        model_key=NANO_BANANA_MODEL_KEY,
        used_cuda=False,
        elapsed_ms=elapsed_ms,
        backend='gemini_api',
        info=f'{resolved_model} @ {image_bgr.shape[1]}x{image_bgr.shape[0]}',
    )
