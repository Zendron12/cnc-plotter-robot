"""Unit tests for the voice_transcribe module.

No real model is downloaded: the WhisperModel is mocked, and the
faster-whisper-missing path is simulated to assert graceful unavailability.
"""

from __future__ import annotations

import builtins

import pytest

from wall_climber import voice_transcribe
from wall_climber.voice_transcribe import (
    TranscriptionUnavailable,
    normalize_content_type,
    transcribe_audio,
)


@pytest.fixture(autouse=True)
def _reset_model_state(monkeypatch):
    # Ensure each test starts with a clean singleton/loader state.
    monkeypatch.setattr(voice_transcribe, '_model', None, raising=False)
    monkeypatch.setattr(voice_transcribe, '_model_load_failed', False, raising=False)
    yield


def test_normalize_content_type_strips_params() -> None:
    assert normalize_content_type('audio/webm;codecs=opus') == 'audio/webm'
    assert normalize_content_type(None) == ''


def test_empty_audio_raises_value_error() -> None:
    with pytest.raises(ValueError):
        transcribe_audio(b'', 'audio/webm')


def test_unsupported_content_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        transcribe_audio(b'data', 'application/pdf')


def test_missing_faster_whisper_raises_unavailable(monkeypatch) -> None:
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == 'faster_whisper' or name.startswith('faster_whisper.'):
            raise ImportError('no faster_whisper')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _fake_import)
    with pytest.raises(TranscriptionUnavailable):
        transcribe_audio(b'audio-bytes', 'audio/webm')


def test_transcribe_with_mock_model(monkeypatch) -> None:
    class _Segment:
        def __init__(self, text):
            self.text = text

    class _FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, language='en', beam_size=5):
            assert language == 'en'
            return ([_Segment(' hello '), _Segment('world')], object())

    monkeypatch.setattr(voice_transcribe, '_get_model', lambda: _FakeModel())
    result = transcribe_audio(b'audio-bytes', 'audio/webm;codecs=opus')
    assert result['text'] == 'hello world'
    assert result['engine'] == 'faster_whisper'
    assert result['language'] == 'en'


def test_decode_failure_raises_unavailable(monkeypatch) -> None:
    class _BadModel:
        def transcribe(self, *args, **kwargs):
            raise RuntimeError('ffmpeg missing')

    monkeypatch.setattr(voice_transcribe, '_get_model', lambda: _BadModel())
    with pytest.raises(TranscriptionUnavailable):
        transcribe_audio(b'audio-bytes', 'audio/webm')
