"""WebSocket endpoint tests for /api/voice/stream."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from wall_climber import voice_stream_whisper_vad, web_server


class _FakeRuntime:
    def __init__(self) -> None:
        self.node = MagicMock()
        self.node.runtime_snapshot.return_value = {
            'ready': True,
            'active_mode': 'off',
            'manual_pen_mode': 'auto',
            'statuses': {},
            'observed_statuses': {},
        }
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'
        self.text_cursor: tuple[float | None, float | None] = (None, None)

    def set_text_cursor(self, x: float | None, y: float | None) -> None:
        self.text_cursor = (x, y)


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.reset_count = 0

    def feed_pcm(self, pcm_bytes: bytes) -> list[dict[str, str]]:
        if not pcm_bytes:
            return []
        return [{'type': 'result', 'text': 'draw mode'}]

    def flush(self) -> list[dict[str, str]]:
        return []

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.closed = True


def _client() -> TestClient:
    return TestClient(web_server.create_app(_FakeRuntime()))


def test_voice_stream_ready_and_pcm_roundtrip() -> None:
    fake_session = _FakeSession()
    with patch.object(voice_stream_whisper_vad, 'warmup_models'), patch.object(
        voice_stream_whisper_vad, 'create_stream_session', return_value=fake_session
    ):
        client = _client()
        with client.websocket_connect('/api/voice/stream') as ws:
            status = ws.receive_json()
            assert status == {'type': 'status', 'message': 'Loading speech model…'}
            ready = ws.receive_json()
            assert ready == {'type': 'ready'}

            ws.send_bytes(b'\x00' * 1024)
            result = ws.receive_json()
            assert result == {'type': 'result', 'text': 'draw mode'}

            ws.send_text('{"type":"control","action":"reset"}')
            assert fake_session.reset_count == 1

    assert fake_session.closed is True


def test_voice_stream_closes_when_whisper_unavailable() -> None:
    with patch.object(
        voice_stream_whisper_vad,
        'warmup_models',
        side_effect=voice_stream_whisper_vad.WhisperUnavailable('stack missing'),
    ):
        client = _client()
        with client.websocket_connect('/api/voice/stream') as ws:
            status = ws.receive_json()
            assert status['type'] == 'status'
            error = ws.receive_json()
            assert error['type'] == 'error'
            assert 'stack missing' in error['message']


def test_health_includes_voice_stream_status(monkeypatch) -> None:
    monkeypatch.setattr(
        voice_stream_whisper_vad,
        'model_status',
        lambda: {
            'voice_stream_ready': True,
            'voice_model_name': 'base.en',
            'voice_device': 'cuda',
            'voice_vad': 'silero',
        },
    )

    client = _client()
    response = client.get('/api/health')
    assert response.status_code == 200
    payload = response.json()
    assert payload['voice_stream_ready'] is True
    assert payload['voice_model_name'] == 'base.en'
    assert payload['voice_device'] == 'cuda'
    assert payload['voice_vad'] == 'silero'
