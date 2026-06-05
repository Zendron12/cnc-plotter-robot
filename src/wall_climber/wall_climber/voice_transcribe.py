"""Self-hosted English speech-to-text using faster-whisper.

The backend exposes ``/api/voice/transcribe`` which calls :func:`transcribe_audio`
here. The dependency (``faster-whisper``) and the model weights are optional: if
either is missing, :func:`transcribe_audio` raises :class:`TranscriptionUnavailable`
so the endpoint returns 503 and the web UI falls back to the browser Web Speech
API. Nothing in this module imports faster-whisper at module load time, so the
package always imports cleanly even without the dependency.

Decoding: ``MediaRecorder`` in the browser produces WebM/Opus. faster-whisper
reads audio via PyAV/ffmpeg when given a file path or file-like object. We write
the uploaded bytes to a temporary file and let faster-whisper decode it. If
decoding support is unavailable, we raise :class:`TranscriptionUnavailable`.
"""

from __future__ import annotations

import os
import tempfile
import threading

# Audio MIME types the endpoint accepts (browser MediaRecorder + common WAV/OGG).
SUPPORTED_CONTENT_TYPES = {
    'audio/webm',
    'audio/ogg',
    'audio/wav',
    'audio/x-wav',
    'audio/wave',
    'audio/mp4',
    'audio/mpeg',
}

_DEFAULT_MODEL = 'base.en'

_model = None
_model_lock = threading.Lock()
_model_load_failed = False


class TranscriptionUnavailable(Exception):
    """Raised when transcription cannot be performed (missing dependency, model,
    or audio decoder). The endpoint maps this to HTTP 503."""


def normalize_content_type(content_type: str | None) -> str:
    return str(content_type or '').split(';', 1)[0].strip().lower()


def _model_name() -> str:
    return str(os.environ.get('WALL_CLIMBER_WHISPER_MODEL', _DEFAULT_MODEL)).strip() or _DEFAULT_MODEL


def _get_model():
    """Lazy-load the WhisperModel singleton. Raises TranscriptionUnavailable
    if faster-whisper or the model weights are unavailable."""
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if _model_load_failed:
        raise TranscriptionUnavailable('transcription model previously failed to load')
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:  # ImportError or transitive failure
            _model_load_failed = True
            raise TranscriptionUnavailable(
                'faster-whisper is not installed; voice transcription is unavailable'
            ) from exc
        try:
            _model = WhisperModel(_model_name(), device='cpu', compute_type='int8')
        except Exception as exc:
            _model_load_failed = True
            raise TranscriptionUnavailable(
                f'failed to load whisper model {_model_name()!r}: {exc}'
            ) from exc
    return _model


def transcribe_audio(audio_bytes: bytes, content_type: str | None) -> dict:
    """Transcribe spoken English audio into text.

    Args:
        audio_bytes: raw audio payload (WebM/Opus, WAV, OGG, ...).
        content_type: MIME type of the payload.

    Returns:
        ``{'text': str, 'engine': 'faster_whisper', 'language': 'en'}``

    Raises:
        ValueError: empty audio or unsupported content type.
        TranscriptionUnavailable: dependency/model/decoder unavailable.
    """
    if not audio_bytes:
        raise ValueError('audio payload is empty')
    normalized = normalize_content_type(content_type)
    if normalized and normalized not in SUPPORTED_CONTENT_TYPES:
        raise ValueError(
            f'unsupported audio content-type {normalized!r}; expected one of '
            + ', '.join(sorted(SUPPORTED_CONTENT_TYPES))
        )

    model = _get_model()
    suffix = _suffix_for(normalized)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(audio_bytes)
            tmp_path = handle.name
        try:
            segments, _info = model.transcribe(tmp_path, language='en', beam_size=5)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise TranscriptionUnavailable(
                f'audio could not be decoded/transcribed: {exc}'
            ) from exc
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return {'text': text, 'engine': 'faster_whisper', 'language': 'en'}


def _suffix_for(normalized_content_type: str) -> str:
    return {
        'audio/webm': '.webm',
        'audio/ogg': '.ogg',
        'audio/wav': '.wav',
        'audio/x-wav': '.wav',
        'audio/wave': '.wav',
        'audio/mp4': '.mp4',
        'audio/mpeg': '.mp3',
    }.get(normalized_content_type, '.webm')


__all__ = ['transcribe_audio', 'TranscriptionUnavailable', 'normalize_content_type',
           'SUPPORTED_CONTENT_TYPES']
