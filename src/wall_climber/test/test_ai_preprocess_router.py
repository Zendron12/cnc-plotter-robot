"""Tests for AI preprocess router and pipeline stage previews."""

from __future__ import annotations

import io

import cv2  # type: ignore
import numpy
import pytest

from wall_climber.image_pipeline.ai_preprocess import (
    PreprocessSettings,
    preprocess_image_to_lineart,
)
from wall_climber.image_pipeline.ai_preprocess.informative_model import InformativeRunResult
from wall_climber.image_pipeline.ai_preprocess.pyra_canny import pyra_canny
from wall_climber.image_pipeline.ai_preprocess.swinir import SwinirRunResult


@pytest.fixture(autouse=True)
def _stub_swinir(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(image_bgr: numpy.ndarray, *, target_resolution: int, **kwargs) -> SwinirRunResult:
        del target_resolution, kwargs
        return SwinirRunResult(
            image_bgr=image_bgr,
            scale_applied=1.0,
            model_key='swinir_stub',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_swinir_tiled',
        fake_run,
    )


@pytest.fixture(autouse=True)
def _stub_informative(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(image_bgr: numpy.ndarray, *, target_size: int) -> InformativeRunResult:
        del target_size
        gray = numpy.full(image_bgr.shape[:2], 255, dtype=numpy.uint8)
        cv2.rectangle(gray, (10, 10), (gray.shape[1] - 10, gray.shape[0] - 10), 0, 2)
        return InformativeRunResult(
            lineart_gray=gray,
            model_key='informative_anime',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_informative_anime',
        fake_run,
    )


def _gradient_photo_png() -> bytes:
    width, height = 240, 180
    gradient = numpy.zeros((height, width, 3), dtype=numpy.uint8)
    for y in range(height):
        for x in range(width):
            gradient[y, x] = (x % 256, y % 256, (x + y) % 256)
    ok, encoded = cv2.imencode('.png', gradient)
    assert ok
    return encoded.tobytes()


def _line_art_png() -> bytes:
    canvas = numpy.full((200, 200), 255, dtype=numpy.uint8)
    cv2.rectangle(canvas, (40, 40), (160, 160), 0, 2)
    ok, encoded = cv2.imencode('.png', canvas)
    assert ok
    return encoded.tobytes()


def _faint_line_art_png() -> bytes:
    canvas = numpy.full((200, 200), 255, dtype=numpy.uint8)
    cv2.line(canvas, (40, 100), (160, 100), 0, 3, lineType=cv2.LINE_AA)
    cv2.line(canvas, (100, 85), (100, 95), 200, 1, lineType=cv2.LINE_AA)
    ok, encoded = cv2.imencode('.png', canvas)
    assert ok
    return encoded.tobytes()


def test_pyra_canny_returns_edge_map() -> None:
    gray = numpy.full((64, 64), 255, dtype=numpy.uint8)
    cv2.rectangle(gray, (10, 10), (54, 54), 0, 2)
    edges = pyra_canny(gray, low=50, high=150)
    assert edges.shape == gray.shape
    assert int(numpy.count_nonzero(edges)) > 0


def test_photo_path_returns_informative_pipeline_stages() -> None:
    result = preprocess_image_to_lineart(
        _gradient_photo_png(),
        PreprocessSettings(mode='photo', target_resolution=512),
    )
    assert not result.skipped_preprocess
    assert result.lineart_png.startswith(b'\x89PNG')
    stage_ids = [stage.stage_id for stage in result.stages]
    assert stage_ids == [
        'original',
        'swinir',
        'informative',
    ]
    payload = result.to_preprocess_preview_payload()
    assert len(payload['pipeline_stages']) == 3
    assert payload['lineart_data_url'].startswith('data:image/png;base64,')
    assert payload['informative_backend'] == 'stub'


def test_photo_path_ignores_force_solid_black_lines() -> None:
    result = preprocess_image_to_lineart(
        _gradient_photo_png(),
        PreprocessSettings(
            mode='photo',
            target_resolution=512,
            force_solid_black_lines=True,
        ),
    )
    stage_ids = [stage.stage_id for stage in result.stages]
    assert stage_ids == [
        'original',
        'swinir',
        'informative',
    ]
    assert 'binary' not in stage_ids
    assert result.metadata['force_solid_black_lines'] is False


def test_photo_path_anilines_basic_legacy_maps_to_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    from wall_climber.image_pipeline.ai_preprocess.anilines_model import AnilinesRunResult

    seen_mode: dict[str, str] = {}

    def fake_anilines(image_bgr: numpy.ndarray, *, mode: str, target_size: int) -> AnilinesRunResult:
        del image_bgr, target_size
        seen_mode['mode'] = mode
        gray = numpy.full((128, 128), 255, dtype=numpy.uint8)
        cv2.line(gray, (10, 64), (118, 64), 0, 2)
        return AnilinesRunResult(
            lineart_gray=gray,
            model_key=f'anilines_{mode}',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_anilines',
        fake_anilines,
    )
    result = preprocess_image_to_lineart(
        _gradient_photo_png(),
        PreprocessSettings(
            mode='photo',
            target_resolution=512,
            photo_lineart_model='anilines_basic',  # type: ignore[arg-type]
        ),
    )
    assert seen_mode['mode'] == 'detail'
    stage_ids = [stage.stage_id for stage in result.stages]
    assert 'anilines' in stage_ids
    payload = result.to_preprocess_preview_payload()
    assert payload['photo_lineart_model'] == 'anilines_detail'


def test_photo_path_anilines_detail_uses_anilines_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from wall_climber.image_pipeline.ai_preprocess.anilines_model import AnilinesRunResult

    def fake_anilines(image_bgr: numpy.ndarray, *, mode: str, target_size: int) -> AnilinesRunResult:
        del image_bgr, target_size
        gray = numpy.full((128, 128), 255, dtype=numpy.uint8)
        cv2.rectangle(gray, (20, 20), (108, 108), 0, 2)
        return AnilinesRunResult(
            lineart_gray=gray,
            model_key=f'anilines_{mode}',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub detail',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_anilines',
        fake_anilines,
    )
    result = preprocess_image_to_lineart(
        _gradient_photo_png(),
        PreprocessSettings(
            mode='photo',
            target_resolution=512,
            photo_lineart_model='anilines_detail',
        ),
    )
    stage_ids = [stage.stage_id for stage in result.stages]
    assert 'anilines' in stage_ids
    assert result.metadata['photo_lineart_model'] == 'anilines_detail'


def test_photo_path_nano_banana_uses_gemini_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from wall_climber.image_pipeline.ai_preprocess.nano_banana_model import NanoBananaRunResult

    seen: dict[str, str] = {}

    def fake_nano_banana(
        image_bgr: numpy.ndarray,
        *,
        api_key: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> NanoBananaRunResult:
        del model
        seen['api_key'] = api_key
        seen['prompt'] = str(prompt or '')
        gray = numpy.full(image_bgr.shape[:2], 255, dtype=numpy.uint8)
        cv2.rectangle(gray, (12, 12), (gray.shape[1] - 12, gray.shape[0] - 12), 0, 2)
        return NanoBananaRunResult(
            lineart_gray=gray,
            model_key='nano_banana',
            used_cuda=False,
            elapsed_ms=0.0,
            backend='stub',
            info='test stub',
        )

    monkeypatch.setattr(
        'wall_climber.image_pipeline.ai_preprocess.router.run_nano_banana_lineart',
        fake_nano_banana,
    )
    result = preprocess_image_to_lineart(
        _gradient_photo_png(),
        PreprocessSettings(
            mode='photo',
            target_resolution=512,
            photo_lineart_model='nano_banana',
            google_api_key='test-google-key',
            nano_banana_prompt='make clean line art',
        ),
    )
    stage_ids = [stage.stage_id for stage in result.stages]
    assert stage_ids == ['original', 'swinir', 'nano_banana']
    assert 'informative' not in stage_ids
    assert 'anilines' not in stage_ids
    assert seen['api_key'] == 'test-google-key'
    assert seen['prompt'] == 'make clean line art'
    payload = result.to_preprocess_preview_payload()
    assert payload['nano_banana_backend'] == 'stub'


def test_photo_path_nano_banana_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from wall_climber.image_pipeline.ai_preprocess.nano_banana_model import NanoBananaModelError

    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    with pytest.raises(NanoBananaModelError, match='API key'):
        preprocess_image_to_lineart(
            _gradient_photo_png(),
            PreprocessSettings(
                mode='photo',
                target_resolution=512,
                photo_lineart_model='nano_banana',
            ),
        )


def test_coloring_book_ai_path_skips_informative() -> None:
    result = preprocess_image_to_lineart(
        _line_art_png(),
        PreprocessSettings(mode='coloring_book', raw_print=False, target_resolution=512),
    )
    stage_ids = [stage.stage_id for stage in result.stages]
    assert 'informative' not in stage_ids
    assert 'pyra_canny' not in stage_ids
    assert 'swinir' in stage_ids
    assert 'grayscale' not in stage_ids
    assert 'binary' not in stage_ids
    assert result.metadata['path'] == 'coloring_book_ai'


def test_coloring_book_ai_path_with_force_solid_black_lines_adds_binary() -> None:
    result = preprocess_image_to_lineart(
        _line_art_png(),
        PreprocessSettings(
            mode='coloring_book',
            raw_print=False,
            target_resolution=512,
            force_solid_black_lines=True,
        ),
    )
    stage_ids = [stage.stage_id for stage in result.stages]
    assert 'binary' in stage_ids
    assert stage_ids.index('swinir') < stage_ids.index('binary')


def test_coloring_book_force_solid_black_preserves_faint_gray_lines() -> None:
    result = preprocess_image_to_lineart(
        _faint_line_art_png(),
        PreprocessSettings(
            mode='coloring_book',
            raw_print=False,
            target_resolution=512,
            force_solid_black_lines=True,
        ),
    )
    lineart = cv2.imdecode(
        numpy.frombuffer(result.lineart_png, dtype=numpy.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    assert lineart is not None
    assert int(lineart[100, 90]) == 0
    assert int(lineart[100, 100]) == 0
    assert int(lineart[0, 0]) == 255


def test_coloring_book_raw_print_skips_ai() -> None:
    payload = _line_art_png()
    result = preprocess_image_to_lineart(
        payload,
        PreprocessSettings(mode='coloring_book', raw_print=True),
    )
    assert result.skipped_preprocess
    stage_ids = [stage.stage_id for stage in result.stages]
    assert stage_ids == ['original']


def test_coloring_book_raw_print_with_force_solid_black_adds_binary() -> None:
    payload = _line_art_png()
    result = preprocess_image_to_lineart(
        payload,
        PreprocessSettings(
            mode='coloring_book',
            raw_print=True,
            force_solid_black_lines=True,
        ),
    )
    assert result.skipped_preprocess
    stage_ids = [stage.stage_id for stage in result.stages]
    assert stage_ids == ['original', 'binary']
    lineart = cv2.imdecode(
        numpy.frombuffer(result.lineart_png, dtype=numpy.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    assert lineart is not None
    assert set(numpy.unique(lineart).tolist()).issubset({0, 255})
