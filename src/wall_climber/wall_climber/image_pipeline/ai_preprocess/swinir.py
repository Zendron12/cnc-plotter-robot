"""SwinIR-L x4 GAN upscaler via spandrel (CUDA-only, no CPU/OpenCV fallback)."""

from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy

from wall_climber.image_pipeline.ai_preprocess.vram_manager import (
    cuda_available,
    shared_gpu_slot,
)

_LOGGER = logging.getLogger(__name__)

SWINIR_RELEASE_BASE = 'https://github.com/JingyunLiang/SwinIR/releases/download/v0.0'
SWINIR_L_X4_GAN_FILE = 'SwinIR-L_x4_GAN.pth'
SWINIR_L_X4_GAN_RELEASE_FILE = '003_realSR_BSRGAN_DFOWMFC_s64w8_SwinIR-L_x4_GAN.pth'
SWINIR_HF_REPO = 'easygoing0114/AI_upscalers'
SWINIR_SCALE = 4
DEFAULT_TILE_SIZE = 192
DEFAULT_TILE_OVERLAP = 24
MIN_WEIGHTS_BYTES = 1_000_000


class SwinirModelError(RuntimeError):
    """SwinIR is unavailable (CUDA/weights/spandrel required)."""


@dataclass(frozen=True)
class SwinirRunResult:
    image_bgr: numpy.ndarray
    scale_applied: float
    model_key: str
    used_cuda: bool
    elapsed_ms: float
    backend: str
    info: str


def model_cache_dir() -> Path:
    override = os.environ.get('WALL_CLIMBER_MODEL_CACHE', '').strip()
    if override:
        return Path(override).expanduser() / 'swinir'
    return Path.home() / '.cache' / 'wall_climber' / 'swinir'


def weights_path() -> Path:
    env_override = os.environ.get('WALL_CLIMBER_SWINIR_WEIGHTS', '').strip()
    if env_override:
        return Path(env_override).expanduser()
    return model_cache_dir() / SWINIR_L_X4_GAN_FILE


def _is_valid_weights_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_WEIGHTS_BYTES


def weights_cached(model_key: str | None = None) -> bool:
    del model_key
    return _is_valid_weights_file(weights_path())


def _require_cuda() -> None:
    if cuda_available():
        return
    raise SwinirModelError(
        'SwinIR requires CUDA. AI preprocessing cannot run without a GPU.'
    )


def _download_weights() -> Path:
    destination = weights_path()
    if _is_valid_weights_file(destination):
        return destination

    cache_dir = model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        downloaded = Path(
            hf_hub_download(
                repo_id=SWINIR_HF_REPO,
                filename=SWINIR_L_X4_GAN_FILE,
                local_dir=str(cache_dir),
            )
        )
        if _is_valid_weights_file(downloaded):
            if downloaded.resolve() != destination.resolve():
                shutil.copy2(downloaded, destination)
            return destination
    except Exception as hf_error:
        _LOGGER.info('HF download failed for SwinIR-L x4 GAN: %s', hf_error)

    release_path = cache_dir / SWINIR_L_X4_GAN_RELEASE_FILE
    if not _is_valid_weights_file(release_path):
        url = f'{SWINIR_RELEASE_BASE}/{SWINIR_L_X4_GAN_RELEASE_FILE}'
        tmp_path = release_path.with_suffix('.partial')
        _LOGGER.info('Downloading SwinIR weights from %s', url)
        try:
            urllib.request.urlretrieve(url, tmp_path)
            tmp_path.replace(release_path)
        except Exception as download_error:
            raise SwinirModelError(
                'Failed to download SwinIR-L x4 GAN weights. '
                f'Set WALL_CLIMBER_SWINIR_WEIGHTS to a local {SWINIR_L_X4_GAN_FILE} file. '
                f'Underlying error: {download_error}'
            ) from download_error

    if not _is_valid_weights_file(destination) and _is_valid_weights_file(release_path):
        try:
            destination.symlink_to(release_path.name)
        except OSError:
            shutil.copy2(release_path, destination)

    if not _is_valid_weights_file(destination):
        raise SwinirModelError(
            f'SwinIR weights not found at {destination}. '
            f'Set WALL_CLIMBER_SWINIR_WEIGHTS or install weights under {cache_dir}.'
        )
    return destination


def _load_spandrel_model():
    import torch
    from spandrel import ModelLoader

    _require_cuda()
    path = _download_weights()
    device = torch.device('cuda')
    try:
        model = ModelLoader().load_from_file(str(path))
    except Exception as error:
        raise SwinirModelError(
            f'Failed to load SwinIR weights with spandrel: {error}'
        ) from error
    model = model.eval().to(device)
    return {'model': model, 'device': device, 'implementation': 'spandrel'}


def _bgr_to_rgb_tensor(image_bgr: numpy.ndarray):
    import torch

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB).astype(numpy.float32) / 255.0
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)
    return tensor.to(torch.device('cuda'))


def _tensor_to_bgr(tensor) -> numpy.ndarray:
    array = tensor.detach().float().cpu().clamp(0.0, 1.0).squeeze(0).permute(1, 2, 0).numpy()
    rgb = (array * 255.0).round().astype(numpy.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _upscale_tiled_spandrel(tensor, model, *, tile_size: int, overlap: int):
    import torch

    _, _, height, width = tensor.shape
    scale = SWINIR_SCALE
    out_height = height * scale
    out_width = width * scale
    output = torch.zeros(1, 3, out_height, out_width, device=tensor.device, dtype=tensor.dtype)
    weight = torch.zeros(1, 1, out_height, out_width, device=tensor.device, dtype=tensor.dtype)
    stride = max(1, tile_size - overlap)
    ramp_size = overlap * scale

    for top in range(0, height, stride):
        for left in range(0, width, stride):
            bottom = min(top + tile_size, height)
            right = min(left + tile_size, width)
            y_start = max(0, bottom - tile_size)
            x_start = max(0, right - tile_size)
            tile = tensor[:, :, y_start:bottom, x_start:right]
            with torch.inference_mode():
                tile_out = model(tile)
            oy = y_start * scale
            ox = x_start * scale
            oh = (bottom - y_start) * scale
            ow = (right - x_start) * scale
            ramp_y = torch.ones(oh, device=tensor.device)
            ramp_x = torch.ones(ow, device=tensor.device)
            if ramp_size > 0 and oh > ramp_size:
                ramp_y[:ramp_size] = torch.linspace(0, 1, ramp_size, device=tensor.device)
                ramp_y[-ramp_size:] = torch.linspace(1, 0, ramp_size, device=tensor.device)
            if ramp_size > 0 and ow > ramp_size:
                ramp_x[:ramp_size] = torch.linspace(0, 1, ramp_size, device=tensor.device)
                ramp_x[-ramp_size:] = torch.linspace(1, 0, ramp_size, device=tensor.device)
            mask = ramp_y[:, None] * ramp_x[None, :]
            output[:, :, oy : oy + oh, ox : ox + ow] += tile_out * mask
            weight[:, :, oy : oy + oh, ox : ox + ow] += mask
    return output / weight.clamp(min=1e-8)


def _resize_to_target_long(image_bgr: numpy.ndarray, target_long: int) -> numpy.ndarray:
    height, width = image_bgr.shape[:2]
    longest = max(width, height)
    if longest <= target_long:
        return image_bgr
    scale = float(target_long) / float(longest)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    return cv2.resize(image_bgr, (new_width, new_height), interpolation=cv2.INTER_AREA)


def _spandrel_upscale_bgr(
    image_bgr: numpy.ndarray,
    *,
    target_long: int,
    tile_size: int,
    tile_overlap: int,
) -> tuple[numpy.ndarray, str]:
    import torch

    height, width = image_bgr.shape[:2]
    longest = max(width, height)
    if longest >= target_long:
        return image_bgr, f'skipped ({longest}px ≥ target {target_long}px)'

    scale_needed = float(target_long) / float(longest)
    if scale_needed < 1.5:
        new_width = max(1, int(round(width * scale_needed)))
        new_height = max(1, int(round(height * scale_needed)))
        resized = cv2.resize(image_bgr, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
        return resized, f'bicubic {scale_needed:.2f}x → {new_width}x{new_height}'

    slot = shared_gpu_slot()
    try:
        with slot.use('swinir_l_x4_gan', _load_spandrel_model) as loaded:
            model = loaded['model']
            tensor = _bgr_to_rgb_tensor(image_bgr)
            try:
                upscaled_tensor = _upscale_tiled_spandrel(
                    tensor,
                    model,
                    tile_size=tile_size,
                    overlap=tile_overlap,
                )
            finally:
                del tensor
                torch.cuda.empty_cache()
    except SwinirModelError:
        raise
    except Exception as error:
        raise SwinirModelError(f'SwinIR GPU inference failed: {error}') from error

    upscaled = _tensor_to_bgr(upscaled_tensor)
    upscaled_long = max(upscaled.shape[1], upscaled.shape[0])
    if upscaled_long > int(target_long * 1.1):
        upscaled = _resize_to_target_long(upscaled, target_long)
        return upscaled, f'swinir 4x + downsize → {upscaled.shape[1]}x{upscaled.shape[0]}'
    return upscaled, f'swinir 4x → {upscaled.shape[1]}x{upscaled.shape[0]}'


def run_swinir_tiled(
    image_bgr: numpy.ndarray,
    *,
    target_resolution: int,
    tile_size: int = DEFAULT_TILE_SIZE,
    tile_overlap: int = DEFAULT_TILE_OVERLAP,
) -> SwinirRunResult:
    """Enhance ``image_bgr`` toward ``target_resolution`` using SwinIR-L x4 GAN on CUDA."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        raise ValueError('run_swinir_tiled expects a BGR color image.')

    _require_cuda()

    started = time.perf_counter()
    height, width = image_bgr.shape[:2]
    source_longest = max(width, height)
    scale_applied = max(1.0, float(target_resolution) / float(max(1, source_longest)))

    enhanced, info = _spandrel_upscale_bgr(
        image_bgr,
        target_long=target_resolution,
        tile_size=max(128, min(256, tile_size)),
        tile_overlap=max(16, tile_overlap),
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return SwinirRunResult(
        image_bgr=enhanced,
        scale_applied=scale_applied,
        model_key='swinir_l_x4_gan',
        used_cuda=True,
        elapsed_ms=elapsed_ms,
        backend='spandrel_cuda',
        info=info,
    )
