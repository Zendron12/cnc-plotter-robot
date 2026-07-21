#!/usr/bin/env python3
"""Generate reference diagrams for the Artie graduation report."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit('Pillow is required: pip install Pillow') from exc

ASSETS = Path(__file__).resolve().parents[1] / 'docs' / 'report_assets'


def _font(size: int = 16):
    for name in ('DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _save(name: str, image: Image.Image) -> Path:
    ASSETS.mkdir(parents=True, exist_ok=True)
    path = ASSETS / name
    image.save(path, format='PNG')
    return path


def render_mermaid(mmd_path: Path, png_path: Path, width: int = 2400, height: int = 3200, scale: int = 2) -> bool:
    mmdc = shutil.which('mmdc')
    if not mmdc:
        return False
    cmd = [
        mmdc,
        '-i',
        str(mmd_path),
        '-o',
        str(png_path),
        '-b',
        'white',
        '-w',
        str(width),
        '-H',
        str(height),
        '-s',
        str(scale),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        return False
    return png_path.is_file()


def draw_tsp_comparison() -> Path:
    """Pen-up travel: naive order vs nearest-neighbour + 2-opt (conceptual)."""
    width, height = 900, 420
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    font = _font(18)
    small = _font(14)

    panels = (
        ('Naive stroke order (long pen-up jumps)', (40, 70, 420, 360), True),
        ('Optimized order (NN seed + 2-opt)', (480, 70, 860, 360), False),
    )
    points = np.array(
        [
            (0.12, 0.75),
            (0.35, 0.20),
            (0.55, 0.65),
            (0.78, 0.25),
            (0.88, 0.70),
            (0.25, 0.55),
        ]
    )
    naive_order = [0, 3, 1, 5, 2, 4]
    opt_order = [0, 5, 2, 4, 3, 1]

    def panel_points(box, order):
        x0, y0, x1, y1 = box
        pw, ph = x1 - x0, y1 - y0
        return [(x0 + float(x) * pw, y0 + float(y) * ph) for x, y in points[order]]

    def travel_length(ordered):
        total = 0.0
        for index in range(len(ordered) - 1):
            dx = ordered[index + 1][0] - ordered[index][0]
            dy = ordered[index + 1][1] - ordered[index][1]
            total += (dx * dx + dy * dy) ** 0.5
        return total

    for title, box, dashed in panels:
        draw.text((box[0], 30 if box[0] < 200 else 30), title, fill='#111827', font=font)
        ordered = panel_points(box, naive_order if dashed else opt_order)
        for index, (x, y) in enumerate(ordered):
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill='#2563eb')
            draw.text((x + 8, y - 8), str(index + 1), fill='#111827', font=small)
        for index in range(len(ordered) - 1):
            x1, y1 = ordered[index]
            x2, y2 = ordered[index + 1]
            if dashed:
                steps = 12
                for step in range(0, steps, 2):
                    t0 = step / steps
                    t1 = min(1.0, (step + 1) / steps)
                    draw.line(
                        (
                            x1 + (x2 - x1) * t0,
                            y1 + (y2 - y1) * t0,
                            x1 + (x2 - x1) * t1,
                            y1 + (y2 - y1) * t1,
                        ),
                        fill='#dc2626',
                        width=2,
                    )
            else:
                draw.line((x1, y1, x2, y2), fill='#16a34a', width=2)

    naive_len = travel_length(panel_points(panels[0][1], naive_order))
    opt_len = travel_length(panel_points(panels[1][1], opt_order))
    reduction = max(0.0, (1.0 - opt_len / naive_len) * 100.0) if naive_len else 0.0
    draw.text(
        (40, height - 36),
        f'Conceptual illustration — measured reduction depends on stroke layout (typical improvement observed in testing: ~{reduction:.0f}%).',
        fill='#4b5563',
        font=small,
    )
    return _save('tsp_comparison.png', image)


def render_graphviz(dot_path: Path, png_path: Path, dpi: int = 200) -> bool:
    dot = shutil.which('dot')
    if not dot or not dot_path.is_file():
        return False
    cmd = [dot, '-Tpng', f'-Gdpi={dpi}', '-o', str(png_path), str(dot_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        return False
    return png_path.is_file()


def generate_diagram(stem: str, dpi: int = 200) -> Path:
    png_path = ASSETS / f'{stem}.png'
    dot_path = ASSETS / f'{stem}.dot'
    mmd_path = ASSETS / f'{stem}.mmd'
    if render_graphviz(dot_path, png_path, dpi=dpi):
        return png_path
    if mmd_path.is_file() and render_mermaid(mmd_path, png_path):
        return png_path
    raise RuntimeError(
        f'Failed to render {stem}. Install Graphviz (dot) or mermaid-cli (mmdc).'
    )


def main() -> None:
    paths = [
        draw_tsp_comparison(),
        generate_diagram('classification_diagram', dpi=220),
        generate_diagram('pipeline_flow_part1', dpi=160),
        generate_diagram('pipeline_flow_part2', dpi=170),
    ]
    print('Generated:')
    for path in paths:
        print(f'  {path}')


if __name__ == '__main__':
    main()
