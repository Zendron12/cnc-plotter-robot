"""Single-model GPU slot manager for 8GB VRAM safety."""

from __future__ import annotations

import gc
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator


def _release_torch_model(model: Any) -> None:
    """Move a loaded model off CUDA and drop references."""
    if model is None:
        return
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return

    modules: list[Any] = []
    if isinstance(model, nn.Module):
        modules.append(model)
    elif isinstance(model, dict):
        nested = model.get('model')
        if nested is not None:
            modules.append(nested)

    for module in modules:
        try:
            module.cpu()
        except Exception:
            pass
        try:
            del module
        except Exception:
            pass


@dataclass
class GpuModelSlot:
    """Loads at most one CUDA model at a time."""

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _active_key: str | None = field(default=None, repr=False)
    _active_model: Any = field(default=None, repr=False)

    def active_key(self) -> str | None:
        with self._lock:
            return self._active_key

    def unload(self) -> None:
        with self._lock:
            if self._active_model is not None:
                _release_torch_model(self._active_model)
            self._active_model = None
            self._active_key = None
        self.empty_cache()

    @staticmethod
    def empty_cache() -> None:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except ImportError:
            return
        gc.collect()

    @contextmanager
    def use(self, model_key: str, loader) -> Generator[Any, None, None]:
        """Load ``loader()`` when key changes; unload after inference."""
        with self._lock:
            if self._active_key != model_key:
                if self._active_model is not None:
                    _release_torch_model(self._active_model)
                self._active_model = None
                self._active_key = None
                self.empty_cache()
                self._active_model = loader()
                self._active_key = model_key
            model = self._active_model
        try:
            yield model
        finally:
            self.unload()


_gpu_slot = GpuModelSlot()


def shared_gpu_slot() -> GpuModelSlot:
    return _gpu_slot


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False
