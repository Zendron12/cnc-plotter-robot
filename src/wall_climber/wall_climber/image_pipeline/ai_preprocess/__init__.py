"""Dual-path AI raster preprocessing before Potrace/AutoTrace vectorization."""

from wall_climber.image_pipeline.ai_preprocess.anilines_model import (
    AnilinesModelError,
    run_anilines,
    weights_cached as anilines_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.informative_model import (
    InformativeModelError,
    InformativeRunResult,
    run_informative_anime,
    weights_cached as informative_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.router import preprocess_image_to_lineart
from wall_climber.image_pipeline.ai_preprocess.swinir import (
    SwinirModelError,
    weights_cached as swinir_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.types import (
    PreprocessResult,
    PreprocessSettings,
    PreprocessStagePreview,
)

__all__ = [
    'AnilinesModelError',
    'InformativeModelError',
    'InformativeRunResult',
    'PreprocessResult',
    'PreprocessSettings',
    'PreprocessStagePreview',
    'SwinirModelError',
    'anilines_weights_cached',
    'informative_weights_cached',
    'preprocess_image_to_lineart',
    'run_anilines',
    'run_informative_anime',
    'swinir_weights_cached',
]
