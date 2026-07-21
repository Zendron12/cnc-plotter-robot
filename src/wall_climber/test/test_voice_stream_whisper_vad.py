"""Unit tests for Silero VAD + faster-whisper voice streaming."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from wall_climber import cuda_env
from wall_climber import voice_stream_whisper_vad as voice_stream


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeVADIterator:
    def __init__(self) -> None:
        self.triggered = False
        self.reset_count = 0
        self._step = 0

    def reset_states(self) -> None:
        self.reset_count += 1
        self.triggered = False
        self._step = 0

    def __call__(self, _chunk, return_seconds=False):
        del return_seconds
        self._step += 1
        if self._step == 1:
            self.triggered = True
            return {'start': 0.0}
        if self._step == 20:
            self.triggered = False
            return {'end': 0.64}
        return None


def _frame_bytes() -> bytes:
    samples = np.zeros(voice_stream._FRAME_SAMPLES, dtype=np.int16)
    return samples.tobytes()


def _session_with_fake_vad() -> voice_stream.WhisperVADSession:
    session = voice_stream.WhisperVADSession.__new__(voice_stream.WhisperVADSession)
    session._pending_pcm = bytearray()
    session._speech_buffer = bytearray()
    session._vad_iterator = _FakeVADIterator()
    return session


def test_model_status_reports_whisper_fields(monkeypatch) -> None:
    monkeypatch.setattr(voice_stream, 'is_voice_stack_available', lambda: True)
    monkeypatch.setattr(voice_stream, '_resolve_device', lambda: 'cuda')

    status = voice_stream.model_status()

    assert status['voice_stream_ready'] is True
    assert status['voice_model_name'] == 'base.en'
    assert status['voice_device'] == 'cuda'
    assert status['voice_vad'] == 'silero'


def test_feed_pcm_emits_result_after_vad_end(monkeypatch) -> None:
    session = _session_with_fake_vad()
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([_FakeSegment('draw mode')], None)
    monkeypatch.setattr(voice_stream, '_get_whisper_model', lambda: fake_model)

    events: list[dict[str, str]] = []
    for _ in range(20):
        events.extend(session.feed_pcm(_frame_bytes()))

    assert events == [{'type': 'result', 'text': 'draw mode'}]
    fake_model.transcribe.assert_called_once()


def test_reset_clears_buffers() -> None:
    session = _session_with_fake_vad()
    session._pending_pcm.extend(b'\x01\x02')
    session._speech_buffer.extend(b'\x03\x04')

    session.reset()

    assert session._pending_pcm == bytearray()
    assert session._speech_buffer == bytearray()
    assert session._vad_iterator.reset_count == 1


def test_flush_transcribes_pending_speech(monkeypatch) -> None:
    session = _session_with_fake_vad()
    session._speech_buffer.extend(_frame_bytes() * 10)
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([_FakeSegment('text mode')], None)
    monkeypatch.setattr(voice_stream, '_get_whisper_model', lambda: fake_model)

    events = session.flush()

    assert events == [{'type': 'result', 'text': 'text mode'}]
    assert session._speech_buffer == bytearray()


def test_create_stream_session_raises_when_stack_missing(monkeypatch) -> None:
    monkeypatch.setattr(voice_stream, 'is_voice_stack_available', lambda: False)

    with pytest.raises(voice_stream.WhisperUnavailable):
        voice_stream.create_stream_session()


def test_cuda_env_discovers_nvidia_lib_dirs(monkeypatch, tmp_path) -> None:
    lib_dir = tmp_path / 'nvidia' / 'cublas' / 'lib'
    lib_dir.mkdir(parents=True)
    (lib_dir / 'libcublas.so.12').write_text('')

    monkeypatch.setattr('wall_climber.cuda_env._site_roots', lambda: [str(tmp_path)])

    dirs = cuda_env.discover_nvidia_lib_dirs()

    assert str(lib_dir) in dirs
