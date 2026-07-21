"""Static contract: debug APIs are only polled when DEBUG_MODE is enabled."""

from __future__ import annotations

import re
from pathlib import Path


def _read_app_js() -> str:
    return (
        Path(__file__).resolve().parents[1] / 'web' / 'js' / 'app.js'
    ).read_text(encoding='utf-8')


def test_index_html_does_not_call_debug_apis_directly() -> None:
    index_html = (
        Path(__file__).resolve().parents[1] / 'web' / 'index.html'
    ).read_text(encoding='utf-8')
    assert '/api/debug/' not in index_html


def test_app_js_debug_calls_are_gated_by_debug_mode() -> None:
    app_js = _read_app_js()

    debug_calls = list(re.finditer(r"apiRequest\('/api/debug/[^']+'\)", app_js))
    assert debug_calls, 'expected debug polling endpoints in app.js'

    refresh_fn = re.search(
        r'async function refreshDebugPanels\(\) \{(?P<body>.*?)\n      \}',
        app_js,
        re.S,
    )
    assert refresh_fn is not None
    refresh_body = refresh_fn.group('body')
    assert 'if (!DEBUG_MODE)' in refresh_body

    for match in debug_calls:
        assert refresh_fn.start() < match.start() < refresh_fn.end()

    runtime_refresh = re.search(
        r'async function refreshRuntime\(\) \{(?P<body>.*?)\n      \}',
        app_js,
        re.S,
    )
    assert runtime_refresh is not None
    assert 'if (DEBUG_MODE)' in runtime_refresh.group('body')
    assert 'refreshDebugPanels' in runtime_refresh.group('body')


def test_refresh_debug_panels_is_noop_without_debug_mode() -> None:
    app_js = _read_app_js()
    assert re.search(
        r'async function refreshDebugPanels\(\) \{\s*if \(!DEBUG_MODE\) \{\s*return;\s*\}',
        app_js,
        re.S,
    )
