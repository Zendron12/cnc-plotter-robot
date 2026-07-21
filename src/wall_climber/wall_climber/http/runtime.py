from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import functools
import hashlib
import json
import os

os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Optional

import numpy
import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response

try:
    import websockets
except ImportError:
    websockets = None

try:
    import rclpy
    from ament_index_python.packages import get_package_share_directory
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String
    from wall_climber_interfaces.msg import BoardPoint, PathPrimitive, PrimitivePathPlan
except ImportError as exc:
    rclpy = None
    _ROS_IMPORT_ERROR = exc

    def get_package_share_directory(_package_name: str) -> str:
        raise RuntimeError('ROS 2 Python dependencies are required for package share lookup.') from _ROS_IMPORT_ERROR

    class SingleThreadedExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError('ROS 2 Python dependencies are required for WebBackendNode.') from _ROS_IMPORT_ERROR

    class Node:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError('ROS 2 Python dependencies are required for WebBackendNode.') from _ROS_IMPORT_ERROR

    class ReliabilityPolicy:
        RELIABLE = 'reliable'

    class DurabilityPolicy:
        TRANSIENT_LOCAL = 'transient_local'

    class QoSProfile:
        def __init__(self, *, depth: int, reliability: Any, durability: Any) -> None:
            self.depth = depth
            self.reliability = reliability
            self.durability = durability

    class String:
        data: str

    class BoardPoint:
        x: float
        y: float

    class PathPrimitive:
        pass

    class PrimitivePathPlan:
        pass
else:
    _ROS_IMPORT_ERROR = None

from wall_climber import _http_helpers as _http
from wall_climber._debug_snapshots import DebugSnapshotStore
from wall_climber._ttl_cache import TTLCache
from wall_climber import canonical_adapters as _canonical_adapters
from wall_climber.canonical_adapters import (
    SamplingPolicy,
    canonical_plan_debug_payload,
    canonical_plan_diagnostics,
    canonical_plan_to_draw_strokes,
    canonical_plan_to_legacy_strokes,
    canonical_plan_to_primitive_path_plan,
    canonical_plan_to_sampled_paths,
)
from wall_climber.canonical_builders import (
    draw_strokes_to_canonical_plan,
    text_glyph_outlines_to_canonical_plan,
)
from wall_climber.canonical_optimizer import (
    CanonicalOptimizationPolicy,
    optimize_canonical_plan,
)
from wall_climber.canonical_path import (
    ArcSegment,
    CanonicalCommand,
    CanonicalPathPlan,
    CubicBezier,
    LineSegment,
    PenDown,
    PenUp,
    QuadraticBezier,
    TravelMove,
)
from wall_climber.canonical_tiny_details import expand_tiny_details_in_canonical_plan
from wall_climber.canonical_ops import (
    cleanup_canonical_plan,
    default_image_placement,
    normalize_placement,
    place_canonical_plan_on_board,
    place_grouped_text_on_board,
    stroke_stats,
)
from wall_climber.ingestion.svg import vectorize_svg
from wall_climber.ingestion.text import (
    TextGlyphOutline,
    normalize_text_plan_input,
    vectorize_text_grouped,
)
from wall_climber.ingestion.upload_routing import (
    UploadedVectorFile,
    classify_uploaded_vector_file,
)
from wall_climber.image_pipeline.adapters import drawing_path_plan_to_canonical
from wall_climber.image_pipeline.curve_fit import drawing_path_plan_to_smooth_canonical
from wall_climber.image_pipeline.potrace_vector import (
    is_potrace_available,
    vectorize_potrace_image_to_plan,
)
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.image_pipeline.ai_preprocess import (
    AnilinesModelError,
    InformativeModelError,
    SwinirModelError,
    anilines_weights_cached,
    informative_weights_cached,
    preprocess_image_to_lineart,
    swinir_weights_cached,
)
from wall_climber.image_pipeline.ai_preprocess.preview_encode import (
    decode_lineart_png,
    rasterize_board_strokes_thumbnail,
)
from wall_climber.image_pipeline.ai_preprocess.types import PreprocessSettings
from wall_climber.image_pipeline.ai_preprocess.vram_manager import cuda_available
from wall_climber.image_pipeline.types import DrawingPathPlan
from wall_climber.optimizers import vpype_optimizer
from wall_climber.runtime_topics import (
    ACTIVE_MODE_TOPIC,
    CABLE_EXECUTOR_STATUS_TOPIC,
    CABLE_SUPERVISOR_STATUS_TOPIC,
    EXECUTION_DIAGNOSTICS_TOPIC,
    EXECUTION_CANCEL_TOPIC,
    MANUAL_PEN_MODE_TOPIC,
    MODE_DRAW,
    MODE_OFF,
    MODE_TEXT,
    PEN_MODE_AUTO,
    PEN_MODE_DOWN,
    PEN_MODE_UP,
    PRIMITIVE_PATH_PLAN_TOPIC,
    VALID_MODES,
    VALID_MANUAL_PEN_MODES,
)
from wall_climber.shared_config import load_shared_config
from wall_climber.vector_pipeline import VectorPlacement
from wall_climber import voice_stream_whisper_vad as _voice_stream

_ACTIVE_MODE_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
_STATUS_TOPIC_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)

_MAX_TEXT_CHARS = 400
_MAX_TEXT_BYTES = 4096
_MAX_DRAW_PLAN_BYTES = 256 * 1024
_MAX_DRAW_STROKES = 256
_MAX_POINTS_PER_STROKE = 2048
_MAX_TOTAL_POINTS = 8192
_SEGMENT_EPS_M = 1.0e-4
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_SVG_BYTES = 256 * 1024
_MAX_VECTOR_REQUEST_BYTES = 512 * 1024
_SKETCH_PREVIEW_MAX_POINTS = 2400
_LINEART_CACHE_TTL_SECONDS = 10 * 60
_LINEART_CACHE_MAX_ENTRIES = 8
_PREVIEW_CACHE_TTL_SECONDS = 30 * 60
_PREVIEW_CACHE_MAX_ENTRIES = 48
_SKETCH_DRAW_MAX_CANONICAL_COMMANDS = 200_000
_SKETCH_DRAW_MAX_PRIMITIVES = 200_000
_SKETCH_DRAW_MAX_PRIMITIVE_DESCRIPTOR_BYTES = 16 * 1024 * 1024
_VALID_TEXT_COLUMNS = frozenset({'full', 'left', 'center', 'right'})
_REQUIRED_STATUS_KEYS = (
    'cable_executor_status',
    'cable_supervisor_status',
)

_CPU_EXECUTOR: ThreadPoolExecutor | None = None


def _cpu_executor() -> ThreadPoolExecutor:
    global _CPU_EXECUTOR
    if _CPU_EXECUTOR is None:
        _CPU_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='wall_climber_cpu')
    return _CPU_EXECUTOR


async def _run_cpu_bound(func, /, *args, **kwargs):
    """Run CPU-heavy preview/vectorization work off the asyncio event loop."""
    loop = asyncio.get_running_loop()
    if kwargs:
        bound = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_cpu_executor(), bound)
    return await loop.run_in_executor(_cpu_executor(), func, *args)


@dataclass(frozen=True)
class PreviewCacheEntry:
    preview_id: str
    source_type: str
    canonical_plan: CanonicalPathPlan
    canonical_hash: str
    executable_canonical_plan: CanonicalPathPlan
    executable_canonical_hash: str
    primitive_descriptor: dict[str, Any]
    primitive_plan: PrimitivePathPlan
    primitive_hash: str
    execution_preview_svg: str
    execution_hash: str
    settings_hash: str
    metrics: dict[str, Any]
    preview_payload: dict[str, Any]
    commit_request: dict[str, Any]
    created_at_unix: float
    input_type: str
    pipeline_mode: str
    source_hash: str | None
    settings: dict[str, Any]
    metadata: dict[str, Any]
    warnings: tuple[str, ...]
    source_filename: str
    drawing_plan: DrawingPathPlan | None
    command_metadata: tuple[dict[str, Any] | None, ...] | None
    optimizer_stats: dict[str, Any] | None
    route_metadata: dict[str, Any] | None
    curve_fit_payload: dict[str, Any] | None

@dataclass(frozen=True)
class LineartCacheEntry:
    lineart_png: bytes
    preprocess_preview: dict[str, Any]
    created_at_unix: float



class WebBackendNode(Node):
    def __init__(self) -> None:
        super().__init__('web_ui_server')
        self._shared = load_shared_config()
        self.declare_parameter('port', 8080)
        self.declare_parameter('rosbridge_port', 9090)
        self.declare_parameter('initial_mode', MODE_OFF)
        self.declare_parameter('enable_webots_trail', False)
        self.declare_parameter('open_browser', False)

        self.port = int(self.get_parameter('port').value)
        self.rosbridge_port = int(self.get_parameter('rosbridge_port').value)
        self.enable_webots_trail = bool(self.get_parameter('enable_webots_trail').value)
        self.open_browser = bool(self.get_parameter('open_browser').value)
        requested_mode = str(self.get_parameter('initial_mode').value).strip().lower()
        if requested_mode not in VALID_MODES:
            self.get_logger().warn(
                f'Invalid initial_mode {requested_mode!r}; defaulting to {MODE_OFF!r}.'
            )
            requested_mode = MODE_OFF
        self._active_mode = requested_mode
        self._manual_pen_mode = PEN_MODE_AUTO

        self._lock = threading.Lock()
        self._observed_statuses = {key: False for key in _REQUIRED_STATUS_KEYS}
        self._statuses = {key: None for key in _REQUIRED_STATUS_KEYS}
        self._board_info: dict[str, Any] | None = None
        self._board_bounds: dict[str, float] | None = None
        self._executor_diagnostics: dict[str, Any] | None = None

        self._active_mode_pub = self.create_publisher(
            String,
            ACTIVE_MODE_TOPIC,
            _ACTIVE_MODE_QOS,
        )
        self._primitive_path_plan_pub = self.create_publisher(
            PrimitivePathPlan,
            PRIMITIVE_PATH_PLAN_TOPIC,
            10,
        )
        self._manual_pen_mode_pub = self.create_publisher(
            String,
            MANUAL_PEN_MODE_TOPIC,
            _ACTIVE_MODE_QOS,
        )
        self._execution_cancel_pub = self.create_publisher(
            String,
            EXECUTION_CANCEL_TOPIC,
            _ACTIVE_MODE_QOS,
        )

        self.create_subscription(
            String,
            CABLE_EXECUTOR_STATUS_TOPIC,
            self._status_cb('cable_executor_status'),
            _STATUS_TOPIC_QOS,
        )
        self.create_subscription(
            String,
            CABLE_SUPERVISOR_STATUS_TOPIC,
            self._status_cb('cable_supervisor_status'),
            _STATUS_TOPIC_QOS,
        )
        self.create_subscription(String, '/wall_climber/board_info', self._board_info_cb, 10)
        self.create_subscription(
            String,
            MANUAL_PEN_MODE_TOPIC,
            self._manual_pen_mode_cb,
            _ACTIVE_MODE_QOS,
        )
        self.create_subscription(
            String,
            EXECUTION_DIAGNOSTICS_TOPIC,
            self._executor_diagnostics_cb,
            _STATUS_TOPIC_QOS,
        )

        self._publish_active_mode(self._active_mode)
        self._publish_manual_pen_mode(self._manual_pen_mode)
        self.get_logger().info(
            f'Web backend ready on port {self.port} with initial mode {self._active_mode!r}.'
        )

    def _status_cb(self, key: str):
        def _callback(msg: String) -> None:
            value = str(msg.data).strip().lower()
            with self._lock:
                self._statuses[key] = value
                self._observed_statuses[key] = True

        return _callback

    def _board_info_cb(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        needed = (
            'width',
            'height',
            'writable_x_min',
            'writable_x_max',
            'writable_y_min',
            'writable_y_max',
        )
        if not all(key in data for key in needed):
            return
        try:
            board_info = dict(data)
            for key, value in tuple(board_info.items()):
                if isinstance(value, (int, float)):
                    board_info[key] = float(value)
            for key in needed:
                board_info[key] = float(data[key])
        except (TypeError, ValueError):
            return
        with self._lock:
            self._board_info = board_info
            self._board_bounds = {
                'x_min': board_info['writable_x_min'],
                'x_max': board_info['writable_x_max'],
                'y_min': board_info['writable_y_min'],
                'y_max': board_info['writable_y_max'],
            }

    def _manual_pen_mode_cb(self, msg: String) -> None:
        mode = str(msg.data).strip().lower()
        if mode not in VALID_MANUAL_PEN_MODES:
            return
        with self._lock:
            self._manual_pen_mode = mode

    def _executor_diagnostics_cb(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            self._executor_diagnostics = payload

    def _publish_active_mode(self, mode: str) -> None:
        msg = String()
        msg.data = mode
        self._active_mode_pub.publish(msg)

    def _publish_manual_pen_mode(self, mode: str) -> None:
        msg = String()
        msg.data = mode
        self._manual_pen_mode_pub.publish(msg)

    def runtime_snapshot(self) -> dict[str, Any]:
        with self._lock:
            statuses = dict(self._statuses)
            observed = dict(self._observed_statuses)
            board_info = dict(self._board_info) if self._board_info is not None else None
            active_mode = self._active_mode
            manual_pen_mode = self._manual_pen_mode
            executor_diagnostics = (
                dict(self._executor_diagnostics)
                if self._executor_diagnostics is not None else None
            )
        ready = all(observed.values())
        return {
            'active_mode': active_mode,
            'manual_pen_mode': manual_pen_mode,
            'ready': ready,
            'not_ready_reason': None if ready else 'waiting_for_status_topics',
            'observed_statuses': observed,
            'statuses': statuses,
            'board_info': board_info,
            'executor_diagnostics': executor_diagnostics,
            'enable_webots_trail': self.enable_webots_trail,
        }

    def ensure_ready(self) -> dict[str, Any]:
        snapshot = self.runtime_snapshot()
        if not snapshot['ready']:
            raise HTTPException(status_code=503, detail='runtime status topics are not ready yet')
        return snapshot

    def switch_mode(self, mode: str) -> dict[str, Any]:
        snapshot = self.ensure_ready()
        statuses = snapshot['statuses']
        if statuses['cable_executor_status'] == 'running':
            raise HTTPException(status_code=409, detail='runtime is busy; mode switch rejected')
        with self._lock:
            self._active_mode = mode
        self._publish_active_mode(mode)
        return self.runtime_snapshot()

    def emergency_stop(self) -> dict[str, Any]:
        cancel_msg = String()
        cancel_msg.data = 'stop'
        self._execution_cancel_pub.publish(cancel_msg)
        with self._lock:
            self._active_mode = MODE_OFF
        self._publish_active_mode(MODE_OFF)
        return self.runtime_snapshot()

    def set_manual_pen_mode(self, mode: str) -> dict[str, Any]:
        if mode not in VALID_MANUAL_PEN_MODES:
            raise HTTPException(status_code=400, detail='invalid manual pen mode')
        snapshot = self.ensure_ready()
        statuses = snapshot['statuses']
        if statuses['cable_executor_status'] == 'running':
            raise HTTPException(status_code=409, detail='runtime is busy; manual pen control rejected')
        with self._lock:
            self._manual_pen_mode = mode
        self._publish_manual_pen_mode(mode)
        return self.runtime_snapshot()

    def publish_execution_plan(
        self,
        primitive_plan: PrimitivePathPlan,
        *,
        allowed_modes: tuple[str, ...],
    ) -> dict[str, Any]:
        snapshot = self.ensure_ready()
        if snapshot['active_mode'] not in allowed_modes:
            allowed = ', '.join(allowed_modes)
            raise HTTPException(status_code=409, detail=f'active mode must be one of: {allowed}')
        if snapshot['manual_pen_mode'] != PEN_MODE_AUTO:
            raise HTTPException(status_code=409, detail='manual arm test must be set to auto before drawing')
        if snapshot['statuses']['cable_executor_status'] == 'running':
            raise HTTPException(status_code=409, detail='cable executor is busy')
        self._primitive_path_plan_pub.publish(primitive_plan)
        return {
            'published': 'primitive_path_plan',
            'preferred_transport': 'primitive_path_plan',
            'primitive_transport_published': True,
            'topics': {
                'primitive_path_plan': PRIMITIVE_PATH_PLAN_TOPIC,
            },
        }

    def writable_bounds(self) -> dict[str, float]:
        with self._lock:
            if self._board_bounds is None:
                raise HTTPException(status_code=503, detail='board metadata is not ready yet')
            return dict(self._board_bounds)

    def carriage_safe_writable_bounds(self) -> dict[str, float]:
        try:
            return self._shared.carriage_safe_writable_bounds()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    def carriage_safe_safe_bounds(self) -> dict[str, float]:
        try:
            return self._shared.carriage_safe_workspace_bounds()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    def executor_diagnostics_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._executor_diagnostics) if self._executor_diagnostics is not None else None


class BackendRuntime:
    def __init__(self, node: WebBackendNode) -> None:
        self._node = node
        self._executor = SingleThreadedExecutor()
        self._executor_thread: threading.Thread | None = None
        self._shutdown_lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._server: uvicorn.Server | None = None
        self._share_dir = Path(get_package_share_directory('wall_climber'))
        self._web_dir = _resolve_web_dir(self._share_dir)
        self._debug = DebugSnapshotStore()
        # Per-column text cursors: tracks (X, Y) where the next text draw
        # should continue for each board column (full/left/center/right).
        self._text_cursors: dict[str, tuple[float | None, float | None]] = {}
        self._text_cursor_lock = threading.Lock()
        # Lowest board-space Y from full-width (column=full) text draws only.
        self._text_full_width_bottom_y: float | None = None
        # Lowest board-space Y from text draws in any column (full/left/center/right).
        self._text_global_bottom_y: float | None = None
        # Lowest board-space Y from partial-column (left/center/right) text draws.
        self._text_column_bottom_y: dict[str, float] = {}
        # Last column that successfully completed a text draw (full/left/center/right).
        self._last_text_draw_column: str | None = None
        # Per-column stacks of ink state captured before each text draw (for undo).
        self._text_ink_undo_stacks: dict[str, list[dict[str, Any]]] = {
            key: [] for key in _VALID_TEXT_COLUMNS
        }

    def _capture_text_ink_snapshot_unlocked(self) -> dict[str, Any]:
        return {
            'text_cursors': dict(self._text_cursors),
            'text_full_width_bottom_y': self._text_full_width_bottom_y,
            'text_global_bottom_y': self._text_global_bottom_y,
            'text_column_bottom_y': dict(self._text_column_bottom_y),
            'last_text_draw_column': self._last_text_draw_column,
        }

    def _restore_text_ink_snapshot_unlocked(self, snapshot: dict[str, Any]) -> None:
        self._text_cursors = dict(snapshot.get('text_cursors') or {})
        self._text_full_width_bottom_y = snapshot.get('text_full_width_bottom_y')
        self._text_global_bottom_y = snapshot.get('text_global_bottom_y')
        self._text_column_bottom_y = dict(snapshot.get('text_column_bottom_y') or {})
        self._last_text_draw_column = snapshot.get('last_text_draw_column')

    def push_text_ink_snapshot(self, column: str | None) -> None:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            self._text_ink_undo_stacks.setdefault(key, []).append(
                self._capture_text_ink_snapshot_unlocked()
            )

    def undo_last_text_write(self, column: str | None) -> bool:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            stack = self._text_ink_undo_stacks.get(key) or []
            if not stack:
                return False
            snapshot = stack.pop()
            self._restore_text_ink_snapshot_unlocked(snapshot)
            return True

    def has_text_write_undo(self, column: str | None = None) -> bool:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            return bool(self._text_ink_undo_stacks.get(key))

    def clear_text_write_undo(self, column: str | None = None) -> None:
        if column is None:
            for key in self._text_ink_undo_stacks:
                self._text_ink_undo_stacks[key] = []
            return
        key = self._normalize_cursor_column(column)
        self._text_ink_undo_stacks[key] = []

    @staticmethod
    def _normalize_cursor_column(column: str | None) -> str:
        if column is None:
            return 'full'
        normalized = str(column).strip().lower()
        if normalized in _VALID_TEXT_COLUMNS:
            return normalized
        return 'full'

    def get_text_cursor(self, column: str | None = None) -> tuple[float | None, float | None]:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            return self._text_cursors.get(key, (None, None))

    def set_text_cursor(
        self,
        x: float | None,
        y: float | None,
        column: str | None = None,
    ) -> None:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            if x is None or y is None:
                self._text_cursors.pop(key, None)
            else:
                self._text_cursors[key] = (float(x), float(y))

    def clear_text_cursor_position(self, column: str | None = None) -> None:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            self._text_cursors.pop(key, None)

    def reset_text_cursors(
        self,
        column: str | None = None,
        *,
        clear_ink: bool = True,
    ) -> None:
        with self._text_cursor_lock:
            if column is None:
                self._text_cursors.clear()
                if clear_ink:
                    self._text_full_width_bottom_y = None
                    self._text_global_bottom_y = None
                    self._text_column_bottom_y.clear()
                    self._last_text_draw_column = None
                    self.clear_text_write_undo()
                return
            key = self._normalize_cursor_column(column)
            self._text_cursors.pop(key, None)
            if clear_ink:
                if key in {'left', 'center', 'right'}:
                    self._text_column_bottom_y.pop(key, None)
                elif key == 'full':
                    self._text_full_width_bottom_y = None
                self.clear_text_write_undo(key)

    def get_text_full_width_bottom_y(self) -> float | None:
        with self._text_cursor_lock:
            return self._text_full_width_bottom_y

    def note_text_full_width_bottom_y(self, bottom_y: float) -> None:
        with self._text_cursor_lock:
            value = float(bottom_y)
            if (
                self._text_full_width_bottom_y is None
                or value > self._text_full_width_bottom_y
            ):
                self._text_full_width_bottom_y = value

    def get_text_global_bottom_y(self) -> float | None:
        with self._text_cursor_lock:
            return self._text_global_bottom_y

    def note_text_global_bottom_y(self, bottom_y: float) -> None:
        with self._text_cursor_lock:
            value = float(bottom_y)
            if (
                self._text_global_bottom_y is None
                or value > self._text_global_bottom_y
            ):
                self._text_global_bottom_y = value

    def get_text_column_bottom_y(self, column: str | None) -> float | None:
        key = self._normalize_cursor_column(column)
        if key not in {'left', 'center', 'right'}:
            return None
        with self._text_cursor_lock:
            return self._text_column_bottom_y.get(key)

    def note_text_column_bottom_y(self, column: str | None, bottom_y: float) -> None:
        key = self._normalize_cursor_column(column)
        if key not in {'left', 'center', 'right'}:
            return
        with self._text_cursor_lock:
            value = float(bottom_y)
            existing = self._text_column_bottom_y.get(key)
            if existing is None or value > existing:
                self._text_column_bottom_y[key] = value

    def get_last_text_draw_column(self) -> str | None:
        with self._text_cursor_lock:
            return self._last_text_draw_column

    def set_last_text_draw_column(self, column: str | None) -> None:
        key = self._normalize_cursor_column(column)
        with self._text_cursor_lock:
            self._last_text_draw_column = key

    def get_text_cursor_y(self, column: str | None = None) -> float | None:
        cursor_x, cursor_y = self.get_text_cursor(column)
        del cursor_x
        return cursor_y

    def set_text_cursor_y(self, value: float | None, column: str | None = None) -> None:
        if value is None:
            self.set_text_cursor(None, None, column)
            return
        cursor_x, _cursor_y = self.get_text_cursor(column)
        self.set_text_cursor(cursor_x, float(value), column)

    @property
    def node(self) -> WebBackendNode:
        return self._node

    @property
    def web_dir(self) -> Path:
        return self._web_dir

    def attach_server(self, server: uvicorn.Server) -> None:
        self._server = server

    def start(self) -> None:
        with self._shutdown_lock:
            if self._started:
                return
            self._executor.add_node(self._node)
            self._executor_thread = threading.Thread(
                target=self._executor.spin,
                name='web_ui_server_ros_executor',
                daemon=True,
            )
            self._executor_thread.start()
            self._started = True

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
            server = self._server
            if server is not None:
                server.should_exit = True
            try:
                self._executor.shutdown(timeout_sec=2.0)
            except TypeError:
                self._executor.shutdown()
            if self._executor_thread is not None:
                self._executor_thread.join(timeout=5.0)
                if self._executor_thread.is_alive():
                    self._node.get_logger().warn('ROS executor thread did not stop cleanly before timeout.')
            try:
                self._executor.remove_node(self._node)
            except Exception:
                pass
            try:
                self._node.destroy_node()
            except Exception:
                pass
            if rclpy.ok():
                rclpy.shutdown()

    def record_last_plan_debug(self, payload: dict[str, Any]) -> None:
        self._debug.record_plan(payload)

    def record_last_execution_debug(self, payload: dict[str, Any]) -> None:
        self._debug.record_execution(payload)

    def record_last_curve_fit_debug(self, payload: dict[str, Any]) -> None:
        self._debug.record_curve_fit(payload)

    def last_plan_debug_snapshot(self) -> dict[str, Any] | None:
        return self._debug.plan_snapshot()

    def last_execution_debug_snapshot(self) -> dict[str, Any] | None:
        return self._debug.execution_snapshot()

    def last_curve_fit_debug_snapshot(self) -> dict[str, Any] | None:
        return self._debug.curve_fit_snapshot()

def _resolve_web_dir(share_dir: Path) -> Path:
    """Prefer explicit override, then newer source-tree web assets, then install."""
    installed = share_dir / 'web'
    override = os.environ.get('WALL_CLIMBER_WEB_DIR', '').strip()
    if override:
        path = Path(override)
        if path.is_dir():
            return path
    try:
        workspace_root = share_dir.parents[3]
        source_web = workspace_root / 'src' / 'wall_climber' / 'web'
        source_index = source_web / 'index.html'
        installed_index = installed / 'index.html'
        if source_index.is_file():
            installed_mtime = installed_index.stat().st_mtime if installed_index.is_file() else 0.0
            if source_index.stat().st_mtime >= installed_mtime:
                return source_web
    except (IndexError, OSError):
        pass
    return installed


def _web_ui_diagnostics(web_dir: Path) -> dict[str, Any]:
    index_path = web_dir / 'index.html'
    text = ''
    if index_path.is_file():
        try:
            text = index_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            text = ''
    revision = 'unknown'
    for line in text.splitlines()[:40]:
        marker = 'wall-climber-ui:'
        if marker in line:
            revision = line.split(marker, 1)[1].strip(' ->')
            break
    return {
        'web_dir': str(web_dir),
        'web_ui_revision': revision,
        'web_ui_has_autotrace_option': 'value="autotrace"' in text,
        'web_ui_vectorization_options': text.count('<option value="') if text else 0,
    }


def _resolve_web_asset_path(web_dir: Path, asset_path: str) -> Path:
    normalized = asset_path.strip('/')
    rel_path = Path(normalized)
    if not normalized or rel_path.is_absolute():
        raise HTTPException(status_code=404, detail='asset not found')
    if any(part in ('', '.', '..') for part in rel_path.parts):
        raise HTTPException(status_code=404, detail='asset not found')
    candidate = web_dir / rel_path
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail='asset not found')
    # Defence in depth: ensure the candidate path itself never contains traversal
    # parts after lexical normalisation. We intentionally do NOT call
    # ``candidate.resolve()`` because colcon installs Webots/web assets as
    # symlinks pointing back into ``src/``; resolving and checking against
    # web_dir would reject those legitimate symlinks. The ``..``/``.`` parts
    # check above is what stops path traversal regardless of symlinks.
    return candidate
