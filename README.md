# Whiteboard Plotter (Webots Cable-Driven Robot)

This repository contains a ROS 2 + Webots simulation for a cable-driven wall
robot that draws on a large whiteboard. The system uses a four-cable kinematic
supervisor, a Python backend with a browser UI, and a C++ executor to publish
cable setpoints.

This README documents the `sketch-centerline-release` branch.

## Highlights

- Webots-based cable-driven whiteboard robot (not a rail/CNC plotter).
- Four-cable kinematic supervisor plugin with board-safe workspace checks.
- Python backend + browser UI for preview and drawing.
- Canonical path pipeline with ROS transport to the draw executor.
- Sketch Centerline preview pipeline for clean line-art input.

## Repository Layout

- `src/wall_climber`:
  - Python app package (Webots plugins, FastAPI backend, UI assets, ingestion).
  - URDF/Xacro models, Webots worlds, and shared YAML config.
- `src/wall_climber_interfaces`:
  - ROS 2 message definitions (path primitives, cable setpoints).
- `src/wall_climber_draw_body`:
  - C++ executor, geometry sampling, and transport conversions.

## Requirements

- Ubuntu 22.04
- ROS 2 Humble
- Webots (via `webots_ros2_driver`)
- Python dependencies from `package.xml` (FastAPI, OpenCV, scikit-image, etc.)

## Build

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select wall_climber_interfaces wall_climber_draw_body wall_climber --symlink-install
```

## Run (Simulation + Web UI)

```bash
source /opt/ros/humble/setup.bash
ros2 launch wall_climber my_robot.launch.py
```

- Web UI: http://localhost:8080
- Rosbridge: ws://localhost:9090

## Sketch Centerline Preview (Line Art)

Sketch Centerline converts high-contrast line art into centerline strokes and
returns a preview SVG plus cached drawing plan metadata.

Endpoint:

```text
POST /api/sketch-centerline/preview
```

Example:

```bash
curl -s -X POST http://127.0.0.1:8080/api/sketch-centerline/preview \
  -F "file=@sketch.png" \
  -F "margin_m=0.05" | python3 -m json.tool
```

For full parameters and tuning guidance, see:
- `docs/SKETCH_CENTERLINE_PIPELINE.md`

## Configuration

Key configuration is centralized in:

- `src/wall_climber/config/cable_robot.yaml`
- `src/wall_climber/urdf/*.xacro`
- `src/wall_climber/worlds/*.wbt`

These define board dimensions, anchors, carriage geometry, safety bounds, and
Webots plugin wiring. Changes should be made carefully.

## Tests

```bash
PYTHONPATH=src/wall_climber python3 -m pytest -q src/wall_climber/test
```

## License and Usage

See `LICENSE`. This project is provided for personal, non-commercial use only.
Modification, redistribution, or commercial use is not permitted.
