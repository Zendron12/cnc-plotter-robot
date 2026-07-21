"""Tests for preprocessed_bitmap bypass in vectorizers."""

from __future__ import annotations

import cv2  # type: ignore
import numpy as np
import pytest

from wall_climber.image_pipeline import autotrace_vector, potrace_vector
from wall_climber.image_pipeline._vector_common import _resize_for_processing


def _line_bitmap() -> np.ndarray:
    canvas = np.full((120, 160), 255, dtype=np.uint8)
    cv2.line(canvas, (20, 60), (140, 60), 0, 2, lineType=cv2.LINE_AA)
    return canvas


def _color_png() -> bytes:
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 60), (140, 60), (0, 0, 0), 4, lineType=cv2.LINE_AA)
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    return bytes(encoded.tobytes())


def _sample_potrace_svg(width: int, height: int) -> str:
    return (
        f'<?xml version="1.0" standalone="no"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" '
        f'viewBox="0 0 {width} {height}">'
        f'<g transform="translate(0.000000,{height:.6f}) scale(0.100000,-0.100000)" '
        f'fill="#000000" stroke="none">'
        f'<path d="M200 600 L1400 600"/>'
        f'</g></svg>'
    )


def test_soft_gray_preprocessed_bitmap_binarized_for_tracing() -> None:
    """Informative soft gray (no exact 0 ink) must binarize before Potrace/AutoTrace."""
    soft = np.full((100, 100), 240, dtype=np.uint8)
    cv2.line(soft, (10, 50), (90, 50), 30, 2, lineType=cv2.LINE_AA)
    assert not np.any(soft == 0)

    from wall_climber.image_pipeline.ai_preprocess.binary_lineart import light_binarize_for_vectorizer

    binary = light_binarize_for_vectorizer(soft)
    assert int(np.median(np.concatenate((binary[0, :], binary[-1, :])))) >= 127
    assert np.any(binary == 0)


def test_bitmap_for_vectorizer_catches_ultra_faint_informative_gray() -> None:
    """Informative soft output can sit near 245-252 on white without exact 0 ink."""
    from wall_climber.image_pipeline.ai_preprocess.binary_lineart import bitmap_for_vectorizer

    soft = np.full((256, 256), 255, dtype=np.uint8)
    for y in range(64, 192, 6):
        cv2.line(soft, (40, y), (216, y), 245, 1, lineType=cv2.LINE_AA)
    assert np.any(bitmap_for_vectorizer(soft) == 0)

    pale = np.full((256, 256), 255, dtype=np.uint8)
    cv2.line(pale, (40, 128), (216, 128), 252, 1, lineType=cv2.LINE_AA)
    assert np.any(bitmap_for_vectorizer(pale) == 0)


def test_bitmap_for_vectorizer_roundtrip_through_lineart_png() -> None:
    """Cached lineart PNG decode must keep drawable ink for vectorizers."""
    from wall_climber.image_pipeline.ai_preprocess.preview_encode import (
        decode_lineart_png,
        lineart_bitmap_to_png,
    )
    from wall_climber.image_pipeline.ai_preprocess.binary_lineart import bitmap_for_vectorizer

    finalized = np.full((640, 640), 255, dtype=np.uint8)
    for y in range(0, 640, 3):
        cv2.line(finalized, (0, y), (639, y), 0, 1)
    png_bytes = lineart_bitmap_to_png(finalized)
    decoded = decode_lineart_png(png_bytes)
    traced = bitmap_for_vectorizer(decoded)
    assert np.array_equal(traced, finalized)
    assert np.any(traced == 0)


def test_resize_for_processing_zero_means_full_native_size() -> None:
    canvas = np.full((2400, 3200), 255, dtype=np.uint8)
    cv2.line(canvas, (100, 1200), (3100, 1200), 0, 3)
    resized, scale = _resize_for_processing(canvas, max_image_dim=0)
    assert resized.shape == canvas.shape
    assert scale == 1.0


def test_preprocessed_bitmap_light_binarized_before_potrace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: '/usr/bin/potrace')
    captured: dict[str, np.ndarray] = {}

    def _fake_run(command, **_kwargs):
        input_path = __import__('pathlib').Path(command[-1])
        bitmap = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)
        assert bitmap is not None
        captured['bitmap'] = bitmap
        output_path = __import__('pathlib').Path(command[command.index('-o') + 1])
        output_path.write_text(_sample_potrace_svg(160, 120), encoding='utf-8')
        return potrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(potrace_vector.subprocess, 'run', _fake_run)

    soft = np.full((120, 160), 255, dtype=np.uint8)
    cv2.line(soft, (20, 60), (140, 60), 40, 2, lineType=cv2.LINE_AA)

    plan = potrace_vector.vectorize_potrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=soft,
    )

    assert plan.metadata['preprocessed_input'] is True
    assert np.any(captured['bitmap'] == 0)
    assert np.all((captured['bitmap'] == 0) | (captured['bitmap'] == 255))
    assert not np.array_equal(captured['bitmap'], soft)


def test_potrace_preprocessed_bitmap_skips_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(potrace_vector.shutil, 'which', lambda _name: '/usr/bin/potrace')

    def _fake_run(command, **_kwargs):
        output_path = __import__('pathlib').Path(command[command.index('-o') + 1])
        output_path.write_text(_sample_potrace_svg(160, 120), encoding='utf-8')
        return potrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(potrace_vector.subprocess, 'run', _fake_run)

    plan = potrace_vector.vectorize_potrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=_line_bitmap(),
    )

    assert plan.metadata['preprocessed_input'] is True
    assert plan.metadata['timing']['threshold_time_ms'] < 5.0
    assert plan.metrics.stroke_count >= 1


def test_autotrace_thickens_preprocessed_skeleton_before_trace() -> None:
    thin = np.full((40, 40), 255, dtype=np.uint8)
    thin[20, 10:30] = 0
    thickened = autotrace_vector._thicken_preprocessed_lineart(thin)
    assert int(np.count_nonzero(thickened == 0)) > int(np.count_nonzero(thin == 0))


def test_autotrace_preprocessed_bitmap_skips_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, **_kwargs):
        output_path = None
        for index, token in enumerate(command):
            if token == '-output-file' and index + 1 < len(command):
                output_path = __import__('pathlib').Path(command[index + 1])
                break
        assert output_path is not None
        output_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120">'
            '<path d="M10 60 L150 60"/></svg>',
            encoding='utf-8',
        )
        return autotrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/bin/autotrace')
    monkeypatch.setattr(autotrace_vector.subprocess, 'run', _fake_run)

    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=_line_bitmap(),
    )

    assert plan.metadata['preprocessed_input'] is True
    assert plan.metadata['timing']['decode_time_ms'] < 0.01


def test_autotrace_clean_lineart_skips_light_binarize(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, **_kwargs):
        output_path = None
        for index, token in enumerate(command):
            if token == '-output-file' and index + 1 < len(command):
                output_path = __import__('pathlib').Path(command[index + 1])
                break
        assert output_path is not None
        output_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120">'
            '<path d="M10 60 L150 60"/></svg>',
            encoding='utf-8',
        )
        return autotrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    def _reject_light_binarize(_gray):
        raise AssertionError('light_binarize_for_vectorizer must not run on clean line art')

    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/bin/autotrace')
    monkeypatch.setattr(autotrace_vector.subprocess, 'run', _fake_run)
    monkeypatch.setattr(autotrace_vector, 'light_binarize_for_vectorizer', _reject_light_binarize)

    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=_line_bitmap(),
    )

    assert plan.metadata['preprocessed_input'] is True
    assert plan.metadata['autotrace_antialiased_input'] is True


def test_autotrace_soft_ai_lineart_uses_light_binarize(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {'count': 0}

    def _fake_run(command, **_kwargs):
        output_path = None
        for index, token in enumerate(command):
            if token == '-output-file' and index + 1 < len(command):
                output_path = __import__('pathlib').Path(command[index + 1])
                break
        assert output_path is not None
        output_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120">'
            '<path d="M10 60 L150 60"/></svg>',
            encoding='utf-8',
        )
        return autotrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    def _count_light_binarize(gray):
        calls['count'] += 1
        return original_light_binarize(gray)

    original_light_binarize = autotrace_vector.light_binarize_for_vectorizer
    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/bin/autotrace')
    monkeypatch.setattr(autotrace_vector.subprocess, 'run', _fake_run)
    monkeypatch.setattr(autotrace_vector, 'light_binarize_for_vectorizer', _count_light_binarize)

    soft = np.full((120, 160), 255, dtype=np.uint8)
    for x in range(20, 140):
        soft[60, x] = 245 if x % 2 == 0 else 252

    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=soft,
    )

    assert calls['count'] == 1
    assert plan.metadata['preprocessed_input'] is True
    assert plan.metadata['autotrace_antialiased_input'] is False


def test_autotrace_direct_upload_skips_light_binarize(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(command, **_kwargs):
        output_path = None
        for index, token in enumerate(command):
            if token == '-output-file' and index + 1 < len(command):
                output_path = __import__('pathlib').Path(command[index + 1])
                break
        assert output_path is not None
        output_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120">'
            '<path d="M10 60 L150 60"/></svg>',
            encoding='utf-8',
        )
        return autotrace_vector.subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    def _reject_light_binarize(_gray):
        raise AssertionError('light_binarize_for_vectorizer must not run on direct upload')

    monkeypatch.setattr(autotrace_vector.shutil, 'which', lambda _name: '/usr/bin/autotrace')
    monkeypatch.setattr(autotrace_vector.subprocess, 'run', _fake_run)
    monkeypatch.setattr(autotrace_vector, 'light_binarize_for_vectorizer', _reject_light_binarize)

    plan = autotrace_vector.vectorize_autotrace_image_to_plan(
        _color_png(),
        board_width_m=2.0,
        board_height_m=1.0,
        preprocessed_bitmap=None,
    )

    assert plan.metadata['preprocessed_input'] is False
    assert plan.metadata['autotrace_antialiased_input'] is True
