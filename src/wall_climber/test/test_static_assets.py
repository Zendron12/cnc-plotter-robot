"""Verify web UI static asset routes after frontend extraction."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from wall_climber import web_server


class _FakeRuntime:
    def __init__(self) -> None:
        self.web_dir = Path(__file__).resolve().parents[1] / 'web'


def _local_assets_from_index(web_dir: Path) -> list[str]:
    html = (web_dir / 'index.html').read_text(encoding='utf-8')
    refs = re.findall(
        r'(?:href|src)=["\']((?!https?://|#|data:)[^"\']+)["\']',
        html,
    )
    return sorted(set(refs))


def test_static_asset_routes_serve_extracted_frontend_files() -> None:
    web_dir = Path(__file__).resolve().parents[1] / 'web'
    client = TestClient(web_server.create_app(_FakeRuntime()))

    for asset_ref in _local_assets_from_index(web_dir):
        asset_path = web_dir / asset_ref
        assert asset_path.is_file(), f'missing on disk: {asset_ref}'

        for url in (f'/{asset_ref}', f'/assets/{asset_ref}'):
            response = client.get(url)
            assert response.status_code == 200, f'{url} returned {response.status_code}'

    css = client.get('/styles/main.css')
    assert 'text/css' in css.headers.get('content-type', '')
    assert '--primary:' in css.text

    js = client.get('/js/app.js')
    assert 'javascript' in js.headers.get('content-type', '')
    assert len(js.text) > 100
