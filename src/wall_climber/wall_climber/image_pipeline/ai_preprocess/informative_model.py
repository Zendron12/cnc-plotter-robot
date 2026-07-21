"""Informative Drawings line extractor (anime style) for Photo path preprocessing."""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy
import torch.nn as nn

from wall_climber.image_pipeline.ai_preprocess.vram_manager import (
    cuda_available,
    shared_gpu_slot,
)

_LOGGER = logging.getLogger(__name__)

INFORMATIVE_MODEL_KEY = 'informative_anime'
INFORMATIVE_WEIGHTS_FILENAME = 'netG_A_latest.pth'
INFORMATIVE_HF_REPO = 'ali-vilab/VACE-Annotators'
INFORMATIVE_HF_FILENAME = 'scribble/anime_style/netG_A_latest.pth'
MIN_WEIGHTS_BYTES = 1_000_000
WORKSPACE_FALLBACK = (
    Path(__file__).resolve().parents[5]
    / 'informative-drawings-main'
    / 'checkpoints'
    / 'informative_anime'
    / INFORMATIVE_WEIGHTS_FILENAME
)

norm_layer = nn.InstanceNorm2d


class InformativeModelError(RuntimeError):
    """Informative Drawings is unavailable (CUDA/weights required)."""


class ResidualBlock(nn.Module):
    def __init__(self, in_features: int) -> None:
        super().__init__()
        self.conv_block = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(in_features, in_features, 3),
            norm_layer(in_features),
            nn.ReLU(inplace=True),
            nn.ReflectionPad2d(1),
            nn.Conv2d(in_features, in_features, 3),
            norm_layer(in_features),
        )

    def forward(self, x):
        return x + self.conv_block(x)


class Generator(nn.Module):
    def __init__(
        self,
        input_nc: int,
        output_nc: int,
        n_residual_blocks: int = 9,
        sigmoid: bool = True,
    ) -> None:
        super().__init__()
        model0 = [
            nn.ReflectionPad2d(3),
            nn.Conv2d(input_nc, 64, 7),
            norm_layer(64),
            nn.ReLU(inplace=True),
        ]
        self.model0 = nn.Sequential(*model0)

        model1 = []
        in_features = 64
        out_features = in_features * 2
        for _ in range(2):
            model1 += [
                nn.Conv2d(in_features, out_features, 3, stride=2, padding=1),
                norm_layer(out_features),
                nn.ReLU(inplace=True),
            ]
            in_features = out_features
            out_features = in_features * 2
        self.model1 = nn.Sequential(*model1)

        self.model2 = nn.Sequential(
            *[ResidualBlock(in_features) for _ in range(n_residual_blocks)]
        )

        model3 = []
        out_features = in_features // 2
        for _ in range(2):
            model3 += [
                nn.ConvTranspose2d(
                    in_features,
                    out_features,
                    3,
                    stride=2,
                    padding=1,
                    output_padding=1,
                ),
                norm_layer(out_features),
                nn.ReLU(inplace=True),
            ]
            in_features = out_features
            out_features = in_features // 2
        self.model3 = nn.Sequential(*model3)

        model4 = [nn.ReflectionPad2d(3), nn.Conv2d(64, output_nc, 7)]
        if sigmoid:
            model4 += [nn.Sigmoid()]
        self.model4 = nn.Sequential(*model4)

    def forward(self, x, cond=None):
        out = self.model0(x)
        out = self.model1(out)
        out = self.model2(out)
        out = self.model3(out)
        return self.model4(out)


@dataclass(frozen=True)
class InformativeRunResult:
    lineart_gray: numpy.ndarray
    model_key: str
    used_cuda: bool
    elapsed_ms: float
    backend: str
    info: str


def model_cache_dir() -> Path:
    override = os.environ.get('WALL_CLIMBER_MODEL_CACHE', '').strip()
    if override:
        return Path(override).expanduser() / 'informative' / 'anime'
    return Path.home() / '.cache' / 'wall_climber' / 'informative' / 'anime'


def weights_path() -> Path:
    env_override = os.environ.get('WALL_CLIMBER_INFORMATIVE_ANIME_WEIGHTS', '').strip()
    if env_override:
        return Path(env_override).expanduser()
    return model_cache_dir() / INFORMATIVE_WEIGHTS_FILENAME


def _is_valid_weights_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_WEIGHTS_BYTES


def weights_cached() -> bool:
    if _is_valid_weights_file(weights_path()):
        return True
    if _is_valid_weights_file(WORKSPACE_FALLBACK):
        return True
    nested = model_cache_dir() / 'scribble' / 'anime_style' / INFORMATIVE_WEIGHTS_FILENAME
    return _is_valid_weights_file(nested)


def _require_cuda() -> None:
    if cuda_available():
        return
    raise InformativeModelError(
        'Informative Drawings requires CUDA. Photo preprocessing cannot run without a GPU.'
    )


def _download_weights_hf(destination: Path) -> Path:
    from huggingface_hub import hf_hub_download

    destination.parent.mkdir(parents=True, exist_ok=True)
    _LOGGER.info(
        'Downloading Informative anime weights from Hugging Face %s/%s',
        INFORMATIVE_HF_REPO,
        INFORMATIVE_HF_FILENAME,
    )
    downloaded = Path(
        hf_hub_download(
            repo_id=INFORMATIVE_HF_REPO,
            filename=INFORMATIVE_HF_FILENAME,
            local_dir=str(destination.parent),
        )
    )
    if not _is_valid_weights_file(downloaded):
        raise InformativeModelError(
            f'Downloaded Informative weights at {downloaded} are invalid or incomplete.'
        )
    if downloaded.resolve() != destination.resolve():
        shutil.copy2(downloaded, destination)
    return destination


def _ensure_weights() -> Path:
    resolved = weights_path()
    if _is_valid_weights_file(resolved):
        return resolved
    if _is_valid_weights_file(WORKSPACE_FALLBACK):
        return WORKSPACE_FALLBACK

    cache_dir = model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / INFORMATIVE_WEIGHTS_FILENAME
    if _is_valid_weights_file(cached):
        return cached

    nested = cache_dir / 'scribble' / 'anime_style' / INFORMATIVE_WEIGHTS_FILENAME
    if _is_valid_weights_file(nested):
        return nested

    # Remove stale partial/corrupt downloads from earlier Google Drive attempts.
    corrupt_zip = cache_dir / 'model.zip'
    if corrupt_zip.is_file() and not _is_valid_weights_file(corrupt_zip):
        try:
            corrupt_zip.unlink()
        except OSError:
            _LOGGER.warning('Could not remove corrupt Informative cache file %s', corrupt_zip)

    try:
        return _download_weights_hf(cached)
    except Exception as error:
        raise InformativeModelError(
            'Failed to download Informative Drawings anime weights. '
            f'Set WALL_CLIMBER_INFORMATIVE_ANIME_WEIGHTS to a local {INFORMATIVE_WEIGHTS_FILENAME} '
            f'file (~17 MB). Underlying error: {error}'
        ) from error


def _load_model() -> dict:
    import torch

    _require_cuda()
    path = _ensure_weights()
    device = torch.device('cuda')
    net = Generator(input_nc=3, output_nc=1, n_residual_blocks=3)
    state = torch.load(str(path), map_location=device, weights_only=True)
    net.load_state_dict(state)
    net.to(device).eval()
    for parameter in net.parameters():
        parameter.requires_grad = False
    return {'model': net, 'device': device}


INFORMATIVE_TILE_THRESHOLD = 1536
INFORMATIVE_TILE_SIZE = 512
INFORMATIVE_TILE_OVERLAP = 64


def _resize_bgr_to_target_long(image_bgr: numpy.ndarray, target_long: int) -> numpy.ndarray:
    height, width = image_bgr.shape[:2]
    longest = max(width, height)
    if longest <= target_long:
        return image_bgr
    scale = float(target_long) / float(longest)
    new_width = max(4, int(round(width * scale)))
    new_height = max(4, int(round(height * scale)))
    return cv2.resize(image_bgr, (new_width, new_height), interpolation=cv2.INTER_CUBIC)


def _run_informative_forward(model, device, tensor):
    import torch

    tensor = tensor.to(device)
    with torch.inference_mode():
        return model(tensor)[0].clamp(0.0, 1.0).cpu().numpy()[0]


def _run_informative_tiled(model, device, tensor):
    import torch

    _, _, height, width = tensor.shape
    tile_size = INFORMATIVE_TILE_SIZE
    overlap = INFORMATIVE_TILE_OVERLAP
    stride = max(1, tile_size - overlap)
    output = numpy.zeros((height, width), dtype=numpy.float32)
    weight = numpy.zeros((height, width), dtype=numpy.float32)
    ramp_size = overlap

    for top in range(0, height, stride):
        for left in range(0, width, stride):
            bottom = min(top + tile_size, height)
            right = min(left + tile_size, width)
            y_start = max(0, bottom - tile_size)
            x_start = max(0, right - tile_size)
            tile = tensor[:, :, y_start:bottom, x_start:right]
            tile_out = _run_informative_forward(model, device, tile)
            oh = bottom - y_start
            ow = right - x_start
            ramp_y = numpy.ones(oh, dtype=numpy.float32)
            ramp_x = numpy.ones(ow, dtype=numpy.float32)
            if ramp_size > 0 and oh > ramp_size:
                ramp_y[:ramp_size] = numpy.linspace(0.0, 1.0, ramp_size, dtype=numpy.float32)
                ramp_y[-ramp_size:] = numpy.linspace(1.0, 0.0, ramp_size, dtype=numpy.float32)
            if ramp_size > 0 and ow > ramp_size:
                ramp_x[:ramp_size] = numpy.linspace(0.0, 1.0, ramp_size, dtype=numpy.float32)
                ramp_x[-ramp_size:] = numpy.linspace(1.0, 0.0, ramp_size, dtype=numpy.float32)
            mask = ramp_y[:, None] * ramp_x[None, :]
            output[y_start:bottom, x_start:right] += tile_out * mask
            weight[y_start:bottom, x_start:right] += mask

    return numpy.clip(output / numpy.maximum(weight, 1.0e-8), 0.0, 1.0)


def _infer_informative_lineart(model, device, image_bgr: numpy.ndarray, *, size_safe: int) -> numpy.ndarray:
    import torch
    import torchvision.transforms as transforms
    from PIL import Image

    working = _resize_bgr_to_target_long(image_bgr, size_safe)
    rgb = cv2.cvtColor(working, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    transform = transforms.Compose([
        transforms.ToTensor(),
    ])
    tensor = transform(pil_image).unsqueeze(0)
    height, width = int(tensor.shape[2]), int(tensor.shape[3])
    if max(height, width) >= INFORMATIVE_TILE_THRESHOLD:
        lineart = _run_informative_tiled(model, device, tensor)
        backend_note = f'anime tiled @ {width}x{height}'
    else:
        lineart = _run_informative_forward(model, device, tensor)
        backend_note = f'anime @ {width}x{height}'
    del tensor
    torch.cuda.empty_cache()
    return (lineart * 255.0).astype(numpy.uint8), backend_note


def run_informative_anime(
    image_bgr: numpy.ndarray,
    *,
    target_size: int,
) -> InformativeRunResult:
    """Map a BGR photo to anime-style informative line art (grayscale)."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        raise ValueError('run_informative_anime expects a BGR color image.')

    _require_cuda()

    started = time.perf_counter()
    size_safe = max(64, int(target_size) // 4 * 4)

    slot = shared_gpu_slot()
    with slot.use(INFORMATIVE_MODEL_KEY, _load_model) as loaded:
        model = loaded['model']
        device = loaded['device']
        lineart_gray, backend_note = _infer_informative_lineart(
            model,
            device,
            image_bgr,
            size_safe=size_safe,
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return InformativeRunResult(
        lineart_gray=lineart_gray,
        model_key=INFORMATIVE_MODEL_KEY,
        used_cuda=True,
        elapsed_ms=elapsed_ms,
        backend='torch_cuda',
        info=backend_note,
    )
