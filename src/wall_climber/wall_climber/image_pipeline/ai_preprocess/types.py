"""Shared types for the AI image preprocessing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from wall_climber.image_pipeline.ai_preprocess.nano_banana_model import (
    DEFAULT_NANO_BANANA_PROMPT,
)


PreprocessMode = Literal['photo', 'coloring_book']
PhotoLineartModel = Literal['informative', 'anilines_detail', 'nano_banana']


@dataclass(frozen=True)
class PreprocessSettings:
    mode: PreprocessMode = 'photo'
    raw_print: bool = False
    target_resolution: int = 1024
    force_solid_black_lines: bool = False
    morph_close_kernel: int = 3
    skeletonize: bool = False
    photo_lineart_model: PhotoLineartModel = 'informative'
    nano_banana_prompt: str = DEFAULT_NANO_BANANA_PROMPT
    google_api_key: str = ''

    def normalized(self) -> PreprocessSettings:
        allowed_resolutions = (512, 768, 1024, 1536, 2048)
        resolution = int(self.target_resolution)
        if resolution not in allowed_resolutions:
            resolution = min(allowed_resolutions, key=lambda value: abs(value - resolution))
        allowed_photo_models = {'informative', 'anilines_detail', 'nano_banana'}
        raw_model = str(self.photo_lineart_model)
        if raw_model == 'anilines_basic':
            raw_model = 'anilines_detail'
        photo_model = raw_model if raw_model in allowed_photo_models else 'informative'
        prompt = str(self.nano_banana_prompt or '').strip() or DEFAULT_NANO_BANANA_PROMPT
        return PreprocessSettings(
            mode=self.mode if self.mode in {'photo', 'coloring_book'} else 'photo',
            raw_print=bool(self.raw_print),
            target_resolution=resolution,
            force_solid_black_lines=(
                bool(self.force_solid_black_lines)
                if self.mode == 'coloring_book'
                else False
            ),
            morph_close_kernel=max(1, int(self.morph_close_kernel)),
            skeletonize=bool(self.skeletonize),
            photo_lineart_model=photo_model,  # type: ignore[arg-type]
            nano_banana_prompt=prompt,
            google_api_key=str(self.google_api_key or '').strip(),
        )


@dataclass(frozen=True)
class PreprocessStagePreview:
    """One step in the pipeline visualizer strip."""

    stage_id: str
    label: str
    image_base64: str
    width_px: int
    height_px: int


@dataclass
class PreprocessResult:
    """Output of preprocess_image_to_lineart()."""

    lineart_png: bytes
    original_preview_png: bytes
    stages: list[PreprocessStagePreview] = field(default_factory=list)
    skipped_preprocess: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_preprocess_preview_payload(self) -> dict[str, Any]:
        import base64

        return {
            'mode': self.metadata.get('mode'),
            'raw_print': bool(self.metadata.get('raw_print')),
            'target_resolution': self.metadata.get('target_resolution'),
            'force_solid_black_lines': bool(self.metadata.get('force_solid_black_lines')),
            'skipped_preprocess': self.skipped_preprocess,
            'original_data_url': (
                'data:image/png;base64,'
                + base64.b64encode(self.original_preview_png).decode('ascii')
            ),
            'lineart_data_url': (
                'data:image/png;base64,'
                + base64.b64encode(self.lineart_png).decode('ascii')
            ),
            'pipeline_stages': [
                {
                    'stage_id': stage.stage_id,
                    'label': stage.label,
                    'data_url': f'data:image/png;base64,{stage.image_base64}',
                    'width_px': stage.width_px,
                    'height_px': stage.height_px,
                }
                for stage in self.stages
            ],
            'timing_ms': dict(self.metadata.get('timing_ms') or {}),
            'swinir_used_cuda': bool(self.metadata.get('swinir_used_cuda')),
            'swinir_backend': self.metadata.get('swinir_backend'),
            'swinir_info': self.metadata.get('swinir_info'),
            'informative_used_cuda': bool(self.metadata.get('informative_used_cuda')),
            'informative_backend': self.metadata.get('informative_backend'),
            'informative_info': self.metadata.get('informative_info'),
            'photo_lineart_model': self.metadata.get('photo_lineart_model'),
            'anilines_used_cuda': bool(self.metadata.get('anilines_used_cuda')),
            'anilines_backend': self.metadata.get('anilines_backend'),
            'anilines_info': self.metadata.get('anilines_info'),
            'nano_banana_used_cuda': bool(self.metadata.get('nano_banana_used_cuda')),
            'nano_banana_backend': self.metadata.get('nano_banana_backend'),
            'nano_banana_info': self.metadata.get('nano_banana_info'),
            'warnings': list(self.metadata.get('warnings') or []),
        }
