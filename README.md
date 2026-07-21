<p align="center">
  <img src="docs/images/simulation.png" alt="Artie – Cable-Driven Whiteboard Robot" width="700">
</p>

<h1 align="center">Artie — Whiteboard Plotter</h1>

<p align="center">
  A cable-driven robot that draws on large whiteboards, powered by ROS 2 and Webots.
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#gallery">Gallery</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#license">License</a>
</p>

---

## Features

- **Cable-Driven Kinematics** — Four-cable system with real-time workspace safety checks.
- **Image-to-Drawing Pipeline** — Upload a photo or sketch → AI preprocessing → vectorization → robot draws it.
- **Text Writing** — Type or dictate text; the robot writes it in single-line fonts with column layout support.
- **Voice Dictation** — Whisper-based speech-to-text, speaks and the robot writes.
- **Live Web UI** — Browser-based control panel with real-time board preview, crop, erase, and draw tools.
- **Multiple Vectorizers** — AutoTrace (centerline) and Potrace (outline), selectable per job.
- **AI Line Art** — SwinIR upscaling + cloud/local line-art extraction for photos.
- **Webots Simulation** — Full 3D simulation with physics before deploying to hardware.

## Gallery

<p align="center">
  <img src="docs/images/webots-simulation.png" alt="Webots 3D Simulation" width="700"><br>
  <em>Webots simulation — the robot draws Nyan Cat on the whiteboard</em>
</p>

<p align="center">
  <img src="docs/images/board-workspace.png" alt="Drawing Result" width="700"><br>
  <em>Board workspace — text + diagram drawn by the robot</em>
</p>

<p align="center">
  <img src="docs/images/drawing-result.png" alt="Complex Drawing" width="700"><br>
  <em>Complex illustration — 32k+ points drawn autonomously</em>
</p>

<p align="center">
  <img src="docs/images/web-ui-text.png" alt="Web UI – Text Input" width="700"><br>
  <em>Web UI — text input with voice dictation and column layout</em>
</p>

<p align="center">
  <img src="docs/images/web-ui-settings.png" alt="Web UI – Image Settings" width="700"><br>
  <em>Web UI — image processing settings and vectorization controls</em>
</p>

## Quick Start

### Requirements

- Ubuntu 22.04 / ROS 2 Humble
- Webots R2025a+ (via `webots_ros2_driver`)
- Python 3.10+

### Build

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select wall_climber_interfaces wall_climber_draw_body wall_climber --symlink-install
```

### Run

```bash
ros2 launch wall_climber my_robot.launch.py
```

Open **http://localhost:8080** in your browser.

| Argument | Default | Description |
|---|---|---|
| `world` | `wall_world_basic.wbt` | Webots world file |
| `whisper_device` | `auto` | Whisper device: `auto`, `cuda`, `cpu` |

## Architecture

```
src/
├── wall_climber/              # Main Python package
│   ├── wall_climber/          # Robot logic, image pipeline, web server
│   ├── web/                   # Frontend (HTML/JS/CSS)
│   ├── config/                # YAML configuration
│   ├── urdf/                  # Robot URDF/Xacro models
│   └── worlds/                # Webots world files
├── wall_climber_interfaces/   # ROS 2 message definitions
└── wall_climber_draw_body/    # C++ cable draw executor
```

**Pipeline**: Upload → AI Preprocess → Vectorize → Optimize stroke order → ROS transport → Cable executor → Drawing

## Tests

```bash
PYTHONPATH=src/wall_climber python3 -m pytest -q src/wall_climber/test -p no:anyio
```

## License

MIT — see [LICENSE](LICENSE).
