"""Tests for emergency execution cancel behavior."""

from __future__ import annotations

from pathlib import Path


def _supervisor_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / 'wall_climber'
        / 'cable_supervisor_plugin.py'
    ).read_text(encoding='utf-8')


def _app_js_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / 'web'
        / 'js'
        / 'app.js'
    ).read_text(encoding='utf-8')


def _executor_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / 'wall_climber_draw_body'
        / 'src'
        / 'cable_draw_executor.cpp'
    ).read_text(encoding='utf-8')


def test_supervisor_clears_pending_setpoints_on_execution_cancel() -> None:
    text = _supervisor_source()
    assert 'EXECUTION_CANCEL_TOPIC' in text
    assert '_execution_cancel_cb' in text
    cancel_block = text.split('def _execution_cancel_cb', 1)[1].split('def _executor_status_cb', 1)[0]
    assert 'self._pending_setpoints.clear()' in cancel_block
    assert "command not in {'stop', 'cancel'}" in cancel_block
    assert 'self._pen_down_requested = False' in cancel_block
    assert 'self._latest_setpoint = None' in cancel_block
    assert 'self._cancel_motion_hold = True' in cancel_block
    assert 'self._post_cancel_park_active = True' in cancel_block


def test_supervisor_skips_setpoint_queue_trim_during_post_cancel_park() -> None:
    text = _supervisor_source()
    setpoint_block = text.split('def _setpoint_cb', 1)[1].split('def _manual_pen_mode_cb', 1)[0]
    assert '_post_cancel_park_active' in setpoint_block
    assert 'if not self._post_cancel_park_active and len(self._pending_setpoints) > 64:' in setpoint_block


def test_supervisor_clears_post_cancel_park_on_executor_done() -> None:
    text = _supervisor_source()
    status_block = text.split('def _executor_status_cb', 1)[1].split('def _set_status', 1)[0]
    assert "status in {'done', 'idle', 'error'}" in status_block
    assert 'self._post_cancel_park_active = False' in status_block


def test_supervisor_holds_motion_after_cancel_until_fresh_setpoint() -> None:
    text = _supervisor_source()
    step_block = text.split('def step(self):', 1)[1].split('def cleanup(self):', 1)[0]
    assert '_cancel_motion_hold' in step_block
    assert 'elif not self._cancel_motion_hold and self._latest_setpoint is not None:' in step_block
    assert 'if not reached_target and not self._cancel_motion_hold:' in step_block
    assert 'if self._cancel_motion_hold:' in step_block
    assert 'self._cancel_motion_hold = False' in step_block


def test_emergency_stop_ui_preserves_drawn_trail() -> None:
    text = _app_js_source()
    stop_block = text.split('async emergencyStop()', 1)[1].split('async onToolChanged', 1)[0]
    assert 'endTrailSegment()' in stop_block
    assert 'state.penContact = false' in stop_block
    assert 'clearTrail()' not in stop_block
    assert 'clearAllColumnDrafts()' not in stop_block
    assert 'resetBackendTextCursor' not in stop_block


def test_board_edit_confirm_vectorizes_from_session_canvas() -> None:
    text = _app_js_source()
    block = text.split('async function vectorizeBoardRasterSession', 1)[1].split(
        'async function revectorizeBoardRasterSession',
        1,
    )[0]
    assert "return apiRequest('/api/preview/edited-lineart'," in block
    assert "return apiRequest('/api/preview'," not in block
    assert 'exportRasterSessionCanvas(session)' in block
    assert 'edited-lineart.png' in block


def test_emergency_cancel_publishes_immediate_pen_up_without_burst_sample() -> None:
    text = _executor_source()
    cancel_block = text.split('void cancel_execution_callback', 1)[1].split(
        'void primitive_path_plan_callback',
        1,
    )[0]
    assert 'publish_immediate_pen_up' in cancel_block
    assert 'publish_front_schedule_sample' not in cancel_block
    assert cancel_block.index('publish_immediate_pen_up') < cancel_block.index(
        'build_next_schedule_chunk'
    )


def test_emergency_cancel_uses_shared_completion_park_helper() -> None:
    text = _executor_source()
    cancel_block = text.split('void cancel_execution_callback', 1)[1].split(
        'void primitive_path_plan_callback',
        1,
    )[0]
    assert 'append_optional_completion_park' in cancel_block
    assert 'park_mode' in cancel_block
    assert 'build_next_schedule_chunk' in cancel_block
    assert 'execution_sampling_policy_for_mode(park_mode' in cancel_block
    assert 'append_chunk_sample(point, false, -1' not in cancel_block
