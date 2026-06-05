"""Endpoint tests for /api/voice/transcribe.

Self-contained fake runtime; the transcription engine is monkeypatched so no
model is downloaded. Requires httpx/TestClient (httpx-dependent set).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wall_climber import web_server
from wall_climber import voice_transcribe
from wall_climber.runtime_topics import MODE_OFF, PEN_MODE_AUTO


class _FakeNode:
    def runtime_snapshot(self) -> dict:
        return {
            'ready': True,
            'active_mode': MODE_OFF,
            'manual_pen_mode': PEN_MODE_AUTO,
            'statuses': {'cable_executor_status': 'idle'},
            'observed_statuses': {
                'cable_executor_status': True,
                'cable_supervisor_status': True,
            },
        }

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.2, 'y_min': 0.12, 'y_max': 2.9}

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        return {'x_min': 0.348, 'x_max': 6.14, 'y_min': 0.22, 'y_max': 2.82}


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = _FakeNode()
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'

    def set_text_cursor(self, *_args, **_kwargs) -> None:
        pass


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def test_transcribe_success(monkeypatch) -> None:
    monkeypatch.setattr(
        voice_transcribe, 'transcribe_audio',
        lambda content, content_type: {'text': 'hello world', 'engine': 'faster_whisper', 'language': 'en'},
    )
    client = _client()
    response = client.post(
        '/api/voice/transcribe',
        files={'file': ('clip.webm', b'audio-bytes', 'audio/webm')},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['text'] == 'hello world'
    assert payload['engine'] == 'faster_whisper'


def test_transcribe_unavailable_returns_503(monkeypatch) -> None:
    def _raise(content, content_type):
        raise voice_transcribe.TranscriptionUnavailable('model missing')

    monkeypatch.setattr(voice_transcribe, 'transcribe_audio', _raise)
    client = _client()
    response = client.post(
        '/api/voice/transcribe',
        files={'file': ('clip.webm', b'audio-bytes', 'audio/webm')},
    )
    assert response.status_code == 503


def test_transcribe_unsupported_content_type_returns_422() -> None:
    client = _client()
    response = client.post(
        '/api/voice/transcribe',
        files={'file': ('clip.pdf', b'not-audio', 'application/pdf')},
    )
    assert response.status_code == 422
