import os
import re
import signal
import socket
import shutil
import subprocess
import time
import contextlib
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler, SetEnvironmentVariable
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution, TextSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from webots_ros2_driver.webots_controller import WebotsController
from webots_ros2_driver.webots_launcher import WebotsLauncher

from wall_climber.port_utils import free_tcp_port, port_is_available
from wall_climber.shared_config import load_shared_config


def _require_supervisor_action(webots_launcher: WebotsLauncher):
    supervisor_action = getattr(webots_launcher, '_supervisor', None)
    if supervisor_action is None:
        raise RuntimeError(
            'WebotsLauncher no longer exposes the internal "_supervisor" action. '
            'Update my_robot.launch.py for the current webots_ros2_driver API.'
        )
    return supervisor_action


def _select_fixed_port(requested_port: int, *, label: str = 'TCP') -> int:
    """Bind one fixed port; free stale listeners first."""
    port = max(1024, int(requested_port))
    if port_is_available(port):
        return port
    free_tcp_port(port)
    time.sleep(0.5)
    if port_is_available(port):
        return port
    from wall_climber.port_utils import find_listener_pids

    holders = find_listener_pids(port)
    hint = f' (pids: {holders})' if holders else ''
    raise RuntimeError(
        f'{label} port {port} is busy{hint}. Stop the previous launch (Ctrl+C), then run '
        f'pkill -9 -f rosbridge; pkill -9 -f wall_climber/web_server — and relaunch.'
    )


def _select_internal_port(requested_port: int, *, attempts: int = 16, label: str = 'TCP') -> int:
    """Prefer ``requested_port``; scan forward for an internal service (e.g. rosbridge)."""
    base = max(1024, int(requested_port))
    free_tcp_port(base)
    time.sleep(0.25)
    for offset in range(attempts):
        candidate = base + offset
        if port_is_available(candidate):
            if offset:
                print(
                    f'[wall_climber.launch] {label} port {base} is busy; '
                    f'using internal port {candidate} (browser stays on :8080).'
                )
            return candidate
    raise RuntimeError(
        f'No free {label} port in range {base}-{base + attempts - 1}.'
    )


def _active_x_displays() -> list[str]:
    try:
        result = subprocess.run(
            ['ps', '-eo', 'args='],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    displays: list[str] = []
    for line in result.stdout.splitlines():
        if not any(server in line for server in ('Xorg', 'Xwayland', 'Xvfb')):
            continue
        for match in re.finditer(r'(?<!\d)(:\d+)\b', line):
            display = match.group(1)
            if display not in displays:
                displays.append(display)
    return displays


def _candidate_xauthority_paths() -> list[str]:
    candidates: list[str] = []

    def add_candidate(path: str | None) -> None:
        if not path:
            return
        candidate = Path(path)
        if candidate.is_file():
            resolved = str(candidate)
            if resolved not in candidates:
                candidates.append(resolved)

    add_candidate(os.environ.get('XAUTHORITY'))

    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if runtime_dir:
        for path in sorted(Path(runtime_dir).glob('xauth_*')):
            add_candidate(str(path))

    run_user_root = Path('/run/user')
    if run_user_root.is_dir():
        for path in sorted(run_user_root.glob('*/xauth_*')):
            add_candidate(str(path))

    return candidates


def _resolve_webots_display_environment() -> dict[str, str]:
    active_displays = _active_x_displays()
    if not active_displays:
        return {}

    current_display = os.environ.get('DISPLAY')
    if current_display in active_displays:
        selected_display = current_display
    elif ':0' in active_displays:
        selected_display = ':0'
    else:
        selected_display = active_displays[0]

    environment = {'DISPLAY': selected_display}
    for path in _candidate_xauthority_paths():
        environment['XAUTHORITY'] = path
        break

    return environment


def _cleanup_stale_launch_processes() -> None:
    """Kill any leftover ROS / Webots / web_server processes from a previous
    launch that did not exit cleanly.

    A crashed web_server or a tab that kept its WebSocket alive past the
    launcher's grace period can leave the listening socket in TIME_WAIT,
    which forces the next launch to fall back to port 8081 / 8082 and
    breaks the VS Code Ports panel auto-forward. Clearing those stragglers
    up front makes "ros2 launch" feel deterministic again.

    This is a best-effort cleanup; failures are silently ignored so a
    fresh first launch still works. We deliberately do NOT match the
    word "ros2 launch wall_climber" itself because that would kill the
    invocation that just started.
    """
    patterns = (
        # Long-lived sub-processes started by my_robot.launch.py
        'wall_climber/web_server',
        'rosbridge_websocket',
        'rosbridge_server',
        'rosbridge',
        'webots-controller',
        'cable_draw_executor',
        'ros2_supervisor',
        # Webots renderer + binary (only if a previous launch left them)
        '/.ros/webotsR2025a/webots/bin/webots',
    )
    for pattern in patterns:
        try:
            subprocess.run(
                ['pkill', '-15', '-f', pattern],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
            subprocess.run(
                ['pkill', '-9', '-f', pattern],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # pkill missing in some minimal images; nothing to do.
            pass
    for port in (8080, 9090):
        free_tcp_port(port)
    time.sleep(1.0)


def generate_launch_description():
    _cleanup_stale_launch_processes()
    package_name = 'wall_climber'
    pkg_dir = get_package_share_directory(package_name)
    shared = load_shared_config()
    requested_webots_port = os.environ.get('WEBOTS_PORT', '1234')
    requested_rosbridge_port = os.environ.get('ROSBRIDGE_PORT', '9090')
    try:
        selected_webots_port = _select_fixed_port(
            int(requested_webots_port),
            label='Webots',
        )
        selected_rosbridge_port = _select_internal_port(
            int(requested_rosbridge_port),
            label='rosbridge',
        )
    except ValueError as exc:
        raise RuntimeError(
            'WEBOTS_PORT and ROSBRIDGE_PORT must be integers; got '
            f'WEBOTS_PORT={requested_webots_port!r}, ROSBRIDGE_PORT={requested_rosbridge_port!r}.'
        ) from exc
    display_environment = _resolve_webots_display_environment()
    webots_port = str(selected_webots_port)
    rosbridge_port = int(selected_rosbridge_port)
    selected_display = display_environment.get('DISPLAY')
    current_display = os.environ.get('DISPLAY')
    if selected_display and selected_display != current_display:
        print(
            f'[wall_climber.launch] DISPLAY {current_display!r} is not active; '
            f'using {selected_display!r} for Webots.'
        )
    webots_runtime_root = Path('/tmp') / 'webots' / os.environ.get('USER', 'user')
    try:
        webots_runtime_root.mkdir(parents=True, exist_ok=True)
        stale_port_dir = webots_runtime_root / webots_port
        if stale_port_dir.exists():
            shutil.rmtree(stale_port_dir)
    except OSError:
        pass

    webots_prefix = LaunchConfiguration('webots_prefix')
    enable_webots_trail = LaunchConfiguration('enable_webots_trail')
    writer_mode = LaunchConfiguration('writer_mode')
    world = LaunchConfiguration('world')
    whisper_device = LaunchConfiguration('whisper_device')

    world_path = PathJoinSubstitution([
        TextSubstitution(text=os.path.join(pkg_dir, 'worlds')),
        world,
    ])
    climber_xacro_path = os.path.join(pkg_dir, 'urdf', 'my_robot.urdf.xacro')
    supervisor_xacro_path = os.path.join(pkg_dir, 'urdf', 'cable_supervisor.urdf.xacro')

    climber_description = ParameterValue(Command(['xacro ', climber_xacro_path]), value_type=str)

    webots = WebotsLauncher(
        world=world_path,
        mode='realtime',
        ros2_supervisor=True,
        port=webots_port,
        prefix=webots_prefix,
    )
    supervisor_action = _require_supervisor_action(webots)

    climber_spawner = Node(
        package=package_name,
        executable='urdf_spawner',
        name='climber_spawner',
        output='screen',
        parameters=[
            {'robot_description': climber_description},
            {'robot_name': 'wall_climber'},
            {'spawn_translation': shared.initial_spawn_translation_str()},
            {'spawn_rotation': '1 0 0 1.5708'},
        ],
    )
    wall_climber_driver = WebotsController(
        robot_name='wall_climber',
        port=webots_port,
        parameters=[
            {'robot_description': climber_xacro_path},
            {'use_sim_time': True},
        ],
        respawn=True,
    )

    cable_supervisor_driver = WebotsController(
        robot_name='cable_supervisor',
        port=webots_port,
        parameters=[
            {'robot_description': supervisor_xacro_path},
            {'use_sim_time': True},
            {'enable_webots_trail': enable_webots_trail},
        ],
        respawn=True,
    )

    rosbridge = Node(
        package='rosbridge_server',
        executable='rosbridge_websocket',
        name='rosbridge_websocket',
        output='screen',
        # Explicitly set the parameters that rosbridge warns about under
        # Humble: it tells us "the defaults will change in Jazzy". Setting
        # them explicitly here picks the future-default behaviour now and
        # silences the warnings.
        parameters=[{
            'port': rosbridge_port,
            'default_call_service_timeout': 5.0,
            'call_services_in_new_thread': True,
            'send_action_goals_in_new_thread': True,
        }],
    )
    web_server = Node(
        package='wall_climber',
        executable='web_server',
        name='web_ui_server',
        output='screen',
        parameters=[
            {'port': 8080},
            {'rosbridge_port': rosbridge_port},
            {'initial_mode': ParameterValue(writer_mode, value_type=str)},
            {'enable_webots_trail': ParameterValue(enable_webots_trail, value_type=bool)},
            {'open_browser': False},
        ],
    )
    cable_draw_executor = Node(
        package='wall_climber_draw_body',
        executable='cable_draw_executor',
        name='cable_draw_executor',
        output='screen',
        parameters=[shared.cable_executor_params()],
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'webots_prefix',
            default_value='',
            description='Optional command prefix for the Webots process itself.',
        ),
        DeclareLaunchArgument(
            'enable_webots_trail',
            default_value='false',
            description='Enable the optional visual-only Webots trail mesh.',
        ),
        DeclareLaunchArgument(
            'writer_mode',
            default_value='off',
            description='Initial UI mode: off | text | draw',
        ),
        DeclareLaunchArgument(
            'world',
            default_value='wall_world.wbt',
            description='Webots world file name under the package worlds/ directory.',
        ),
        DeclareLaunchArgument(
            'whisper_device',
            default_value='auto',
            description='Whisper inference device: auto | cuda | cpu.',
        ),
        SetEnvironmentVariable('ALSOFT_DRIVERS', 'null'),
        SetEnvironmentVariable('WEBOTS_TMPDIR', '/tmp'),
        SetEnvironmentVariable('TMPDIR', '/tmp'),
        SetEnvironmentVariable('WALL_CLIMBER_WHISPER_DEVICE', whisper_device),
        # Tell FastDDS to skip the shared-memory transport. /dev/shm is
        # restricted in the dev container, so SHM allocation always fails
        # and floods the logs with "Failed to create segment" errors
        # before silently falling back to UDP. Pointing at our XML config
        # makes UDP the only transport and removes the noise.
        SetEnvironmentVariable(
            'FASTRTPS_DEFAULT_PROFILES_FILE',
            os.path.join(pkg_dir, 'config', 'fastdds_no_shm.xml'),
        ),
        *[
            SetEnvironmentVariable(name, value)
            for name, value in display_environment.items()
        ],
        webots,
        supervisor_action,
        climber_spawner,
        RegisterEventHandler(
            OnProcessExit(
                target_action=webots,
                on_exit=[EmitEvent(event=Shutdown())],
            )
        ),
        wall_climber_driver,
        cable_supervisor_driver,
        rosbridge,
        web_server,
        cable_draw_executor,
    ])
