"""Unit tests for the DrawLibrary named-image service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wall_climber.draw_library import DrawLibrary, DrawLibraryEntry


def _write_manifest(library_dir: Path, entries: list[dict]) -> None:
    (library_dir / 'manifest.json').write_text(
        json.dumps({'version': 1, 'entries': entries}), encoding='utf-8'
    )


def test_resolve_existing_id(tmp_path: Path) -> None:
    (tmp_path / 'examples').mkdir()
    (tmp_path / 'examples' / 'one.png').write_bytes(b'PNGDATA')
    _write_manifest(tmp_path, [
        {'id': 1, 'name': 'One', 'file': 'examples/one.png',
         'default_mode': 'sketch_centerline', 'description': 'x'},
    ])
    lib = DrawLibrary(tmp_path)
    entry = lib.resolve(1)
    assert isinstance(entry, DrawLibraryEntry)
    assert entry.id == 1 and entry.file == 'examples/one.png'
    assert lib.load_image_bytes(entry) == b'PNGDATA'


def test_resolve_missing_id_returns_none(tmp_path: Path) -> None:
    _write_manifest(tmp_path, [
        {'id': 1, 'file': 'examples/one.png'},
    ])
    lib = DrawLibrary(tmp_path)
    assert lib.resolve(99) is None


def test_missing_file_raises(tmp_path: Path) -> None:
    _write_manifest(tmp_path, [
        {'id': 2, 'file': 'examples/missing.png'},
    ])
    lib = DrawLibrary(tmp_path)
    entry = lib.resolve(2)
    assert entry is not None
    with pytest.raises(FileNotFoundError):
        lib.load_image_bytes(entry)


def test_corrupt_manifest_yields_empty(tmp_path: Path) -> None:
    (tmp_path / 'manifest.json').write_text('{ this is not json', encoding='utf-8')
    lib = DrawLibrary(tmp_path)
    assert lib.entries() == []
    assert lib.resolve(1) is None


def test_no_manifest_yields_empty(tmp_path: Path) -> None:
    lib = DrawLibrary(tmp_path)
    assert lib.entries() == []


def test_falls_back_to_example_manifest(tmp_path: Path) -> None:
    (tmp_path / 'manifest.example.json').write_text(
        json.dumps({'version': 1, 'entries': [{'id': 5, 'file': 'a.png'}]}),
        encoding='utf-8',
    )
    lib = DrawLibrary(tmp_path)
    assert lib.resolve(5) is not None


def test_path_traversal_rejected(tmp_path: Path) -> None:
    secret = tmp_path.parent / 'secret.png'
    secret.write_bytes(b'SECRET')
    _write_manifest(tmp_path, [
        {'id': 3, 'file': '../secret.png'},
    ])
    lib = DrawLibrary(tmp_path)
    entry = lib.resolve(3)
    assert entry is not None
    with pytest.raises(ValueError):
        lib.load_image_bytes(entry)
