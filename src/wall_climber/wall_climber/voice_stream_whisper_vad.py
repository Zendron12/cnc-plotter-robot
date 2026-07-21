"""Offline phrase speech-to-text via Silero VAD + faster-whisper over WebSocket PCM."""

from __future__ import annotations

from wall_climber import cuda_env as _cuda_env  # noqa: F401  # must run before ctranslate2

import os
import threading
from typing import Any

import numpy as np

_SAMPLE_RATE_HZ = 16000
_FRAME_SAMPLES = 512
_FRAME_BYTES = _FRAME_SAMPLES * 2
_DEFAULT_MODEL = 'base.en'
_DEFAULT_VAD = 'silero'
_MIN_SPEECH_MS = 250
_MAX_PHRASE_MS = 3000
_GPU_SETUP_HINT = (
    'GPU transcription requires NVIDIA CUDA libraries. Install with: '
    'pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 — then restart the launch.'
)

_whisper_model = None
_whisper_device: str | None = None
_silero_model = None
_model_lock = threading.Lock()


class WhisperUnavailable(Exception):
    """Raised when faster-whisper, Silero VAD, or model load fails."""


def sample_rate_hz() -> int:
    return _SAMPLE_RATE_HZ


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, '')).strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _silence_ms() -> int:
    return _env_int('WALL_CLIMBER_VAD_SILENCE_MS', 500)


def _max_phrase_ms() -> int:
    return _env_int('WALL_CLIMBER_VAD_MAX_PHRASE_MS', _MAX_PHRASE_MS)


def _whisper_model_name() -> str:
    return str(os.environ.get('WALL_CLIMBER_WHISPER_MODEL', _DEFAULT_MODEL)).strip() or _DEFAULT_MODEL


def _resolve_device() -> str:
    requested = str(os.environ.get('WALL_CLIMBER_WHISPER_DEVICE', 'auto')).strip().lower()
    if requested in ('', 'auto'):
        return 'cuda' if _cuda_runtime_ready() else 'cpu'
    if requested != 'cuda':
        return requested
    if not _cuda_runtime_ready():
        raise WhisperUnavailable(
            'CUDA GPU is not available for Whisper. '
            f'{_GPU_SETUP_HINT}'
        )
    return 'cuda'


def _cuda_runtime_ready() -> bool:
    """True only when ctranslate2 can actually run on GPU."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def _reset_whisper_model() -> None:
    global _whisper_model, _whisper_device
    with _model_lock:
        _whisper_model = None
        _whisper_device = None


def _load_whisper_model(device: str):
    global _whisper_model, _whisper_device
    from faster_whisper import WhisperModel

    model_name = _whisper_model_name()
    compute_type = 'float16' if device == 'cuda' else 'int8'
    _whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    _whisper_device = device
    return _whisper_model


def is_voice_stack_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import silero_vad  # noqa: F401
        import torch  # noqa: F401
    except Exception:
        return False
    return True


def model_status() -> dict[str, Any]:
    ready = is_voice_stack_available()
    device: str | None = None
    if ready:
        try:
            device = _resolve_device()
        except WhisperUnavailable:
            device = 'cuda'
    if _whisper_device is not None:
        device = _whisper_device
    return {
        'voice_stream_ready': ready,
        'voice_model_name': _whisper_model_name(),
        'voice_device': device,
        'voice_vad': _DEFAULT_VAD,
        **_cuda_env.cuda_library_status(),
    }


def _get_whisper_model():
    global _whisper_model, _whisper_device
    if _whisper_model is not None:
        return _whisper_model
    with _model_lock:
        if _whisper_model is not None:
            return _whisper_model
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:
            raise WhisperUnavailable(
                'faster-whisper is not installed; run '
                '"pip install faster-whisper silero-vad torch" and restart the launch.'
            ) from exc

        device = _resolve_device()
        try:
            return _load_whisper_model(device)
        except Exception as exc:
            _reset_whisper_model()
            raise WhisperUnavailable(
                f'could not load Whisper model {_whisper_model_name()!r} on {device}: {exc}. '
                f'{_GPU_SETUP_HINT}'
            ) from exc


def _get_silero_model():
    global _silero_model
    if _silero_model is not None:
        return _silero_model
    with _model_lock:
        if _silero_model is not None:
            return _silero_model
        try:
            from silero_vad import load_silero_vad
        except Exception as exc:
            raise WhisperUnavailable(
                'silero-vad is not installed; run '
                '"pip install silero-vad" and restart the launch.'
            ) from exc
        try:
            _silero_model = load_silero_vad()
        except Exception as exc:
            raise WhisperUnavailable(f'could not load Silero VAD model: {exc}') from exc
    return _silero_model


def warmup_models() -> None:
    """Load Silero + Whisper on GPU and verify one transcribe call succeeds."""
    _get_silero_model()
    model = _get_whisper_model()
    probe = np.zeros(_SAMPLE_RATE_HZ // 10, dtype=np.float32)
    try:
        _transcribe_with_model(model, probe)
    except Exception as exc:
        raise WhisperUnavailable(
            f'Whisper GPU warmup failed: {exc}. {_GPU_SETUP_HINT}'
        ) from exc


def _create_vad_iterator():
    from silero_vad import VADIterator

    return VADIterator(
        _get_silero_model(),
        sampling_rate=_SAMPLE_RATE_HZ,
        min_silence_duration_ms=_silence_ms(),
    )


def _transcribe_with_model(model, audio: np.ndarray) -> str:
    segments, _info = model.transcribe(
        audio,
        language='en',
        condition_on_previous_text=False,
        vad_filter=False,
    )
    return ' '.join(segment.text.strip() for segment in segments).strip()


def _transcribe_buffer(speech_buffer: bytearray) -> str:
    if not speech_buffer:
        return ''
    audio = (
        np.frombuffer(bytes(speech_buffer), dtype=np.int16).astype(np.float32) / 32768.0
    )
    model = _get_whisper_model()
    return _transcribe_with_model(model, audio)


class WhisperVADSession:
    """Buffer PCM until Silero detects end-of-phrase, then transcribe with Whisper."""

    def __init__(self) -> None:
        self._pending_pcm = bytearray()
        self._speech_buffer = bytearray()
        self._vad_iterator = _create_vad_iterator()

    def reset(self) -> None:
        self._pending_pcm.clear()
        self._speech_buffer.clear()
        self._vad_iterator.reset_states()

    def close(self) -> None:
        return

    def _buffer_duration_ms(self) -> int:
        sample_count = len(self._speech_buffer) // 2
        return int(sample_count * 1000 / _SAMPLE_RATE_HZ)

    def _emit_transcription(self, events: list[dict[str, str]]) -> None:
        if self._buffer_duration_ms() < _MIN_SPEECH_MS:
            self._speech_buffer.clear()
            return
        try:
            text = _transcribe_buffer(self._speech_buffer)
        except Exception as exc:
            events.append({'type': 'error', 'message': f'transcription failed: {exc}'})
            self._speech_buffer.clear()
            return
        if text:
            events.append({'type': 'result', 'text': text})
        self._speech_buffer.clear()

    def _process_frame(self, frame_bytes: bytes) -> list[dict[str, str]]:
        import torch

        events: list[dict[str, str]] = []
        chunk = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        speech_dict = self._vad_iterator(
            torch.from_numpy(chunk),
            return_seconds=True,
        )

        if speech_dict and 'start' in speech_dict:
            self._speech_buffer = bytearray()

        if self._vad_iterator.triggered:
            self._speech_buffer.extend(frame_bytes)
            if self._buffer_duration_ms() >= _max_phrase_ms():
                self._emit_transcription(events)

        if speech_dict and 'end' in speech_dict:
            self._emit_transcription(events)

        return events

    def feed_pcm(self, pcm_bytes: bytes) -> list[dict[str, str]]:
        if not pcm_bytes:
            return []
        try:
            self._pending_pcm.extend(pcm_bytes)
            events: list[dict[str, str]] = []
            while len(self._pending_pcm) >= _FRAME_BYTES:
                frame_bytes = bytes(self._pending_pcm[:_FRAME_BYTES])
                del self._pending_pcm[:_FRAME_BYTES]
                events.extend(self._process_frame(frame_bytes))
            return events
        except Exception as exc:
            return [{'type': 'error', 'message': str(exc)}]

    def flush(self) -> list[dict[str, str]]:
        events: list[dict[str, str]] = []
        try:
            if self._pending_pcm:
                padded = bytes(self._pending_pcm) + b'\x00' * (
                    _FRAME_BYTES - len(self._pending_pcm)
                )
                self._pending_pcm.clear()
                events.extend(self._process_frame(padded[:_FRAME_BYTES]))
            if self._speech_buffer:
                self._emit_transcription(events)
                self._vad_iterator.reset_states()
        except Exception as exc:
            events.append({'type': 'error', 'message': str(exc)})
        return events


def create_stream_session() -> WhisperVADSession:
    if not is_voice_stack_available():
        raise WhisperUnavailable(
            'voice stack unavailable; run '
            '"pip install faster-whisper silero-vad torch" and restart the launch.'
        )
    return WhisperVADSession()


__all__ = [
    'WhisperUnavailable',
    'WhisperVADSession',
    'create_stream_session',
    'is_voice_stack_available',
    'model_status',
    'sample_rate_hz',
    'warmup_models',
]
