"""Tests for GPU VRAM slot unloading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wall_climber.image_pipeline.ai_preprocess.vram_manager import (
    GpuModelSlot,
    _release_torch_model,
)


def test_release_torch_model_moves_module_to_cpu() -> None:
    torch = pytest.importorskip('torch')

    class TinyModule(torch.nn.Module):
        def forward(self, x):
            return x

    module = TinyModule()
    if torch.cuda.is_available():
        module.cuda()

    _release_torch_model(module)
    for parameter in module.parameters():
        assert parameter.device.type == 'cpu'


def test_unload_clears_active_slot() -> None:
    slot = GpuModelSlot()
    active = {'model': MagicMock()}
    slot._active_model = active
    slot._active_key = 'test_model'

    with patch(
        'wall_climber.image_pipeline.ai_preprocess.vram_manager._release_torch_model'
    ) as release_mock:
        with patch.object(GpuModelSlot, 'empty_cache') as empty_cache_mock:
            slot.unload()

    release_mock.assert_called_once_with(active)
    assert slot.active_key() is None
    empty_cache_mock.assert_called_once()


def test_use_unloads_after_context() -> None:
    slot = GpuModelSlot()
    loader = MagicMock(return_value={'model': MagicMock()})

    with patch.object(slot, 'unload') as unload_mock:
        with slot.use('model_a', loader):
            loader.assert_called_once()
        unload_mock.assert_called_once()


def test_use_switches_models_and_unloads_each_time() -> None:
    slot = GpuModelSlot()
    first = {'model': MagicMock(name='first')}
    second = {'model': MagicMock(name='second')}
    loader = MagicMock(side_effect=[first, second])

    with patch.object(slot, 'unload', wraps=slot.unload) as unload_mock:
        with slot.use('first_key', loader):
            assert slot.active_key() == 'first_key'
        assert unload_mock.call_count == 1
        with slot.use('second_key', loader):
            assert slot.active_key() == 'second_key'
        assert unload_mock.call_count == 2

    assert slot.active_key() is None
