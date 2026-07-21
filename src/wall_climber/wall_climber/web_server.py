from __future__ import annotations

import threading
import webbrowser

import uvicorn

from wall_climber.http import create_app, BackendRuntime, WebBackendNode
from wall_climber.http.runtime import (
    _PREVIEW_CACHE_TTL_SECONDS,
    _ROS_IMPORT_ERROR,
    _SKETCH_DRAW_MAX_CANONICAL_COMMANDS,
    _SKETCH_DRAW_MAX_PRIMITIVES,
    _SKETCH_DRAW_MAX_PRIMITIVE_DESCRIPTOR_BYTES,
    _SKETCH_PREVIEW_MAX_POINTS,
    _web_ui_diagnostics,
    rclpy,
)
from wall_climber.http import helpers as _helpers
from wall_climber.image_pipeline.autotrace_vector import (
    is_autotrace_available,
    vectorize_autotrace_image_to_plan,
)
from wall_climber.image_pipeline.potrace_vector import (
    is_potrace_available,
    vectorize_potrace_image_to_plan,
)
from wall_climber.optimizers import vpype_optimizer
from wall_climber.image_pipeline.ai_preprocess import preprocess_image_to_lineart
from wall_climber.port_utils import bind_listening_socket

for _name, _value in vars(_helpers).items():
    if not _name.startswith('__'):
        globals()[_name] = _value


def main(args=None) -> None:
    if rclpy is None:
        raise RuntimeError('ROS 2 Python dependencies are required to run web_server.') from _ROS_IMPORT_ERROR
    rclpy.init(args=args)
    node = WebBackendNode()
    runtime = BackendRuntime(node)
    app = create_app(runtime)
    selected_port, listen_socket = bind_listening_socket(
        node.port,
        scan=8,
        label='web UI',
    )
    if selected_port != node.port:
        node.get_logger().warn(
            f'Requested port {node.port} is busy; using http://localhost:{selected_port} instead.'
        )
    else:
        node.get_logger().info(f'Open http://localhost:{selected_port} in the browser.')
    config = uvicorn.Config(app, host='0.0.0.0', port=selected_port, log_level='info')
    server = uvicorn.Server(config)
    runtime.attach_server(server)

    if node.open_browser:
        threading.Timer(0.75, lambda: webbrowser.open(f'http://localhost:{selected_port}')).start()

    runtime.start()
    ui_info = _web_ui_diagnostics(runtime.web_dir)
    node.get_logger().info(
        'Serving web UI from '
        f'{ui_info["web_dir"]} '
        f'(revision={ui_info["web_ui_revision"]}, '
        f'autotrace_option={ui_info["web_ui_has_autotrace_option"]})'
    )
    try:
        # Hand the pre-bound, SO_REUSEADDR-enabled socket to uvicorn so it
        # does not silently re-bind without that flag and fail on TIME_WAIT.
        server.run(sockets=[listen_socket])
    except KeyboardInterrupt:
        pass
    finally:
        try:
            listen_socket.close()
        except OSError:
            pass
        runtime.shutdown()


if __name__ == '__main__':
    main()
