"""AniLines anime line art extractor for Photo path preprocessing."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy
import torch
import torch.nn.functional as F
from PIL import Image, ImageEnhance

from wall_climber.image_pipeline.ai_preprocess.anilines_network import LineExtractor
from wall_climber.image_pipeline.ai_preprocess.vram_manager import (
    cuda_available,
    shared_gpu_slot,
)

_LOGGER = logging.getLogger(__name__)

ANILINES_BASIC_KEY = 'anilines_basic'
ANILINES_DETAIL_KEY = 'anilines_detail'
MIN_WEIGHTS_BYTES = 500_000
MODEL_URLS = {
    'basic': 'https://drive.google.com/uc?export=download&id=14Bp8mbQAbiR1rQrEsFp-uNdOou8hoCFr',
    'detail': 'https://drive.google.com/uc?export=download&id=12U1Mwlonoipk2Yvr12mNaFB30foy420o',
}
ANILINES_HF_REPO = 'gyrojeff/AniLines'


class AnilinesModelError(RuntimeError):
    """AniLines is unavailable (CUDA/weights required)."""


@dataclass(frozen=True)
class AnilinesRunResult:
    lineart_gray: numpy.ndarray
    model_key: str
    used_cuda: bool
    elapsed_ms: float
    backend: str
    info: str


def _cache_dir() -> Path:
    override = os.environ.get('WALL_CLIMBER_ANILINES_WEIGHTS_DIR', '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.cache' / 'wall_climber' / 'anilines'


def weights_path(mode: str) -> Path:
    normalized = 'detail' if str(mode).strip().lower() == 'detail' else 'basic'
    return _cache_dir() / f'{normalized}.pth'


def _is_valid_weights_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_WEIGHTS_BYTES


def weights_cached(mode: str | None = None) -> bool:
    if mode is None:
        return _is_valid_weights_file(weights_path('basic')) or _is_valid_weights_file(weights_path('detail'))
    return _is_valid_weights_file(weights_path(mode))


def _download_weights_gdown(mode: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        import gdown
    except ImportError as exc:
        raise AnilinesModelError(
            'AniLines weights are missing and gdown is not installed. '
            f'Run `pip install gdown` or place {mode}.pth under {_cache_dir()} '
            f'(for example {_cache_dir() / f"{mode}.pth"}).'
        ) from exc
    url = MODEL_URLS[mode]
    _LOGGER.info('Downloading AniLines %s weights to %s', mode, destination)
    gdown.download(url, str(destination), quiet=False)
    if not _is_valid_weights_file(destination):
        raise AnilinesModelError(f'Downloaded AniLines weights at {destination} are invalid.')
    return destination


def _download_weights_hf(mode: str, destination: Path) -> Path:
    from huggingface_hub import hf_hub_download

    destination.parent.mkdir(parents=True, exist_ok=True)
    filename = f'{mode}.pth'
    _LOGGER.info(
        'Downloading AniLines %s weights from Hugging Face %s/%s',
        mode,
        ANILINES_HF_REPO,
        filename,
    )
    downloaded = Path(
        hf_hub_download(
            repo_id=ANILINES_HF_REPO,
            filename=filename,
            local_dir=str(destination.parent),
        )
    )
    if not _is_valid_weights_file(downloaded):
        raise AnilinesModelError(
            f'Downloaded AniLines weights at {downloaded} are invalid or incomplete.'
        )
    if downloaded.resolve() != destination.resolve():
        import shutil

        shutil.copy2(downloaded, destination)
    return destination


def _ensure_weights(mode: str) -> Path:
    resolved = weights_path(mode)
    if _is_valid_weights_file(resolved):
        return resolved
    try:
        return _download_weights_gdown(mode, resolved)
    except AnilinesModelError:
        pass
    try:
        return _download_weights_hf(mode, resolved)
    except Exception as error:
        raise AnilinesModelError(
            'Failed to download AniLines weights. '
            f'Run `pip install gdown` or place {mode}.pth under {_cache_dir()} '
            f'(for example {_cache_dir() / f"{mode}.pth"}). '
            f'Underlying error: {error}'
        ) from error


def _increase_sharpness(image: numpy.ndarray, factor: float = 6.0) -> numpy.ndarray:
    pil = Image.fromarray(image)
    return numpy.array(ImageEnhance.Sharpness(pil).enhance(factor))


def _load_model(mode: str, device: torch.device) -> LineExtractor:
    path = _ensure_weights(mode)
    if mode == 'basic':
        model = LineExtractor(3, 1, True).to(device)
    else:
        model = LineExtractor(2, 1, True).to(device)
    state = torch.load(str(path), map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.eval()
    for param in model.parameters():
        param.requires_grad = False
    return model


def _infer_lineart(model: LineExtractor, device: torch.device, image_bgr: numpy.ndarray, *, mode: str) -> numpy.ndarray:
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    if mode == 'basic':
        sharp = _increase_sharpness(rgb)
        tensor = torch.from_numpy(sharp).permute(2, 0, 1).unsqueeze(0).float().to(device) / 255.0
        x_in = tensor
    else:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        sobel = cv2.magnitude(sobel_x, sobel_y)
        sobel = 255 - cv2.normalize(sobel, None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8UC1)
        t_gray = torch.from_numpy(gray).unsqueeze(0).unsqueeze(0).float().to(device) / 255.0
        t_sobel = torch.from_numpy(sobel).unsqueeze(0).unsqueeze(0).float().to(device) / 255.0
        x_in = torch.cat([t_gray, t_sobel], dim=1)

    _, _, height, width = x_in.shape
    pad_h = (8 - (height % 8)) % 8
    pad_w = (8 - (width % 8)) % 8
    if pad_h or pad_w:
        x_in = F.pad(x_in, (0, pad_w, 0, pad_h), mode='reflect')

    use_fp16 = device.type == 'cuda'
    with torch.no_grad():
        if use_fp16:
            with torch.autocast(device_type='cuda'):
                pred = model(x_in)
        else:
            pred = model(x_in)
    pred = pred[:, :, :height, :width]
    output = numpy.clip((pred[0, 0].detach().cpu().numpy() * 255.0) + 0.5, 0, 255).astype(numpy.uint8)
    del x_in, pred
    if device.type == 'cuda':
        torch.cuda.empty_cache()
    return output


def run_anilines(
    image_bgr: numpy.ndarray,
    *,
    mode: str = 'detail',
    target_size: int | None = None,
) -> AnilinesRunResult:
    """Map a BGR photo to AniLines sketch output (grayscale)."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError('run_anilines expects a BGR color image.')
    if not cuda_available():
        raise AnilinesModelError('AniLines requires CUDA.')

    normalized_mode = 'detail' if str(mode).strip().lower() == 'detail' else 'basic'
    model_key = ANILINES_DETAIL_KEY if normalized_mode == 'detail' else ANILINES_BASIC_KEY
    started = time.perf_counter()
    slot = shared_gpu_slot()
    device = torch.device('cuda')
    working = image_bgr
    if target_size is not None and target_size > 0:
        height, width = working.shape[:2]
        longest = max(height, width)
        if longest > int(target_size):
            scale = float(target_size) / float(longest)
            new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            working = cv2.resize(working, new_size, interpolation=cv2.INTER_AREA)

    slot = shared_gpu_slot()
    with slot.use(model_key, lambda: _load_model(normalized_mode, device)) as model:
        lineart = _infer_lineart(model, device, working, mode=normalized_mode)
    if lineart.shape[:2] != image_bgr.shape[:2]:
        lineart = cv2.resize(
            lineart,
            (int(image_bgr.shape[1]), int(image_bgr.shape[0])),
            interpolation=cv2.INTER_LINEAR,
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return AnilinesRunResult(
        lineart_gray=lineart,
        model_key=model_key,
        used_cuda=True,
        elapsed_ms=elapsed_ms,
        backend='torch_cuda',
        info=f'{normalized_mode} @ {working.shape[1]}x{working.shape[0]}',
    )
