"""Put NVIDIA pip CUDA runtime libraries on LD_LIBRARY_PATH before ctranslate2 loads."""

from __future__ import annotations

import glob
import os
import site


def _site_roots() -> list[str]:
    roots: list[str] = []
    for base in site.getsitepackages():
        if base:
            roots.append(base)
    user = site.getusersitepackages()
    if user:
        roots.append(user)
    return roots


def discover_nvidia_lib_dirs() -> list[str]:
    """Return existing nvidia/cublas/cudnn lib directories from pip packages."""
    dirs: list[str] = []
    seen: set[str] = set()
    patterns = (
        'nvidia/cublas/lib',
        'nvidia/cudnn/lib',
        'nvidia/cu12/lib',
        'nvidia/cu13/lib',
        'nvidia/*/lib',
    )
    for root in _site_roots():
        for pattern in patterns:
            for path in glob.glob(os.path.join(root, pattern)):
                if os.path.isdir(path) and path not in seen:
                    seen.add(path)
                    dirs.append(path)
    return dirs


def ensure_cuda_library_path() -> list[str]:
    """Prepend discovered NVIDIA lib dirs to LD_LIBRARY_PATH; return dirs added."""
    dirs = discover_nvidia_lib_dirs()
    if not dirs:
        return []
    current = os.environ.get('LD_LIBRARY_PATH', '')
    parts = [part for part in current.split(':') if part]
    prepend = [path for path in dirs if path not in parts]
    if prepend:
        os.environ['LD_LIBRARY_PATH'] = ':'.join(prepend + parts)
    return prepend


def cuda_library_status() -> dict[str, object]:
    dirs = discover_nvidia_lib_dirs()
    return {
        'cuda_lib_dirs': dirs,
        'ld_library_path': os.environ.get('LD_LIBRARY_PATH', ''),
    }


# Configure once on import so faster-whisper / ctranslate2 can dlopen libcublas.
ensure_cuda_library_path()
