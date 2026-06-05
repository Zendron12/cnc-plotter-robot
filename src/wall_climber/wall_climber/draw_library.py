"""Named-image draw library.

A user-managed folder (``assets/draw_library/``) whose images are indexed by a
numeric id via a ``manifest.json`` file. Voice command "draw picture number N"
resolves N to an entry and draws it through the existing image-to-plan pipeline.

Manifest shape (matches ``manifest.example.json``)::

    {
      "version": 1,
      "entries": [
        {"id": 1, "name": "...", "file": "examples/x.png",
         "default_mode": "sketch_centerline", "description": "..."}
      ]
    }

All failures are non-fatal: a missing/corrupt manifest yields an empty library,
``resolve`` returns ``None`` for unknown ids, and ``load_image_bytes`` raises
``FileNotFoundError`` for missing files. File access is confined to the library
directory (path-traversal in the ``file`` field is rejected).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DrawLibraryEntry:
    id: int
    name: str
    file: str
    default_mode: str
    description: str


class DrawLibrary:
    def __init__(self, library_dir: Path | str) -> None:
        self._dir = Path(library_dir)

    @property
    def library_dir(self) -> Path:
        return self._dir

    def _manifest_path(self) -> Path | None:
        primary = self._dir / 'manifest.json'
        if primary.is_file():
            return primary
        example = self._dir / 'manifest.example.json'
        if example.is_file():
            return example
        return None

    def entries(self) -> list[DrawLibraryEntry]:
        path = self._manifest_path()
        if path is None:
            return []
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict):
            return []
        raw_entries = raw.get('entries')
        if not isinstance(raw_entries, list):
            return []
        result: list[DrawLibraryEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            try:
                identifier = int(item['id'])
                file_value = str(item['file'])
            except (KeyError, TypeError, ValueError):
                continue
            if not file_value:
                continue
            result.append(
                DrawLibraryEntry(
                    id=identifier,
                    name=str(item.get('name', '')),
                    file=file_value,
                    default_mode=str(item.get('default_mode', 'sketch_centerline')),
                    description=str(item.get('description', '')),
                )
            )
        return result

    def resolve(self, identifier: int) -> DrawLibraryEntry | None:
        try:
            wanted = int(identifier)
        except (TypeError, ValueError):
            return None
        for entry in self.entries():
            if entry.id == wanted:
                return entry
        return None

    def load_image_bytes(self, entry: DrawLibraryEntry) -> bytes:
        """Read the entry's image bytes, confined to the library directory.

        Raises ``FileNotFoundError`` if the file is missing, and ``ValueError``
        if the ``file`` field escapes the library directory.
        """
        candidate = (self._dir / entry.file)
        base = self._dir.resolve()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise FileNotFoundError(str(candidate)) from exc
        if base != resolved and base not in resolved.parents:
            raise ValueError(
                f'draw library entry {entry.id} references a path outside the library'
            )
        if not resolved.is_file():
            raise FileNotFoundError(str(candidate))
        return resolved.read_bytes()


__all__ = ['DrawLibrary', 'DrawLibraryEntry']
