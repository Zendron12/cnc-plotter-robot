"""Lightweight PNG/base64 helpers for pipeline stage thumbnails."""

from __future__ import annotations

import base64

import cv2  # type: ignore
import numpy

PIPELINE_THUMB_SUPERSAMPLE = 2


def _ensure_uint8_gray(image: numpy.ndarray) -> numpy.ndarray:
    if image.ndim == 3:
        if image.shape[2] == 3:
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        raise ValueError('Unsupported channel count for preview encoding.')
    if image.dtype != numpy.uint8:
        scaled = numpy.clip(image, 0, 255)
        return scaled.astype(numpy.uint8)
    return image


def _ensure_uint8_bgr(image: numpy.ndarray) -> numpy.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.dtype != numpy.uint8:
        scaled = numpy.clip(image, 0, 255)
        image = scaled.astype(numpy.uint8)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def resize_for_strip(image: numpy.ndarray, *, max_dim: int = 320) -> numpy.ndarray:
    if max_dim <= 0:
        return image
    height, width = image.shape[:2]
    longest = max(width, height)
    if longest <= max_dim:
        return image
    scale = float(max_dim) / float(longest)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)


def encode_gray_png(image: numpy.ndarray, *, max_dim: int = 320) -> tuple[bytes, int, int]:
    gray = _ensure_uint8_gray(image)
    thumb = resize_for_strip(gray, max_dim=max_dim)
    ok, encoded = cv2.imencode('.png', thumb)
    if not ok:
        raise ValueError('Failed to encode grayscale preview PNG.')
    height, width = thumb.shape[:2]
    return encoded.tobytes(), int(width), int(height)


def encode_bgr_png(image: numpy.ndarray, *, max_dim: int = 320) -> tuple[bytes, int, int]:
    bgr = _ensure_uint8_bgr(image)
    thumb = resize_for_strip(bgr, max_dim=max_dim)
    ok, encoded = cv2.imencode('.png', thumb)
    if not ok:
        raise ValueError('Failed to encode color preview PNG.')
    height, width = thumb.shape[:2]
    return encoded.tobytes(), int(width), int(height)


def letterbox_for_compare(
    image: numpy.ndarray,
    *,
    canvas_size: int = 640,
    color: bool = True,
) -> numpy.ndarray:
    """Fit image into a fixed square canvas so before/after overlays align."""
    if canvas_size <= 0:
        return image
    if color:
        working = _ensure_uint8_bgr(image)
        canvas = numpy.full((canvas_size, canvas_size, 3), 255, dtype=numpy.uint8)
    else:
        working = _ensure_uint8_gray(image)
        canvas = numpy.full((canvas_size, canvas_size), 255, dtype=numpy.uint8)
    height, width = working.shape[:2]
    scale = float(canvas_size) / float(max(width, height))
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    resized = cv2.resize(working, (new_width, new_height), interpolation=cv2.INTER_AREA)
    top = (canvas_size - new_height) // 2
    left = (canvas_size - new_width) // 2
    if color:
        canvas[top : top + new_height, left : left + new_width] = resized
    else:
        canvas[top : top + new_height, left : left + new_width] = resized
    return canvas


def encode_gray_base64(
    image: numpy.ndarray,
    *,
    max_dim: int = 320,
    letterbox: bool = False,
    canvas_size: int = 640,
) -> tuple[str, int, int]:
    gray = _ensure_uint8_gray(image)
    if letterbox:
        thumb = letterbox_for_compare(gray, canvas_size=canvas_size, color=False)
    else:
        thumb = resize_for_strip(gray, max_dim=max_dim)
    ok, encoded = cv2.imencode('.png', thumb)
    if not ok:
        raise ValueError('Failed to encode grayscale preview PNG.')
    height, width = thumb.shape[:2]
    return base64.b64encode(encoded.tobytes()).decode('ascii'), int(width), int(height)


def encode_bgr_base64(
    image: numpy.ndarray,
    *,
    max_dim: int = 320,
    letterbox: bool = False,
    canvas_size: int = 640,
) -> tuple[str, int, int]:
    bgr = _ensure_uint8_bgr(image)
    if letterbox:
        thumb = letterbox_for_compare(bgr, canvas_size=canvas_size, color=True)
    else:
        thumb = resize_for_strip(bgr, max_dim=max_dim)
    ok, encoded = cv2.imencode('.png', thumb)
    if not ok:
        raise ValueError('Failed to encode color preview PNG.')
    height, width = thumb.shape[:2]
    return base64.b64encode(encoded.tobytes()).decode('ascii'), int(width), int(height)


def decode_lineart_png(png_bytes: bytes) -> numpy.ndarray:
    gray = cv2.imdecode(numpy.frombuffer(png_bytes, dtype=numpy.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError('Failed to decode lineart PNG bytes.')
    return gray


def lineart_bitmap_to_png(bitmap: numpy.ndarray) -> bytes:
    """Black ink on white background (0=ink, 255=paper) for vectorizers."""
    if bitmap.ndim != 2:
        raise ValueError('lineart bitmap must be single-channel.')
    gray = _ensure_uint8_gray(bitmap)
    ok, encoded = cv2.imencode('.png', gray)
    if not ok:
        raise ValueError('Failed to encode lineart PNG.')
    return encoded.tobytes()


def _letterbox_layout(
    width: int,
    height: int,
    *,
    canvas_size: int = 640,
) -> tuple[int, int, float, int, int]:
    scale = float(canvas_size) / float(max(width, height))
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    left = (canvas_size - new_width) // 2
    top = (canvas_size - new_height) // 2
    return left, top, scale, new_width, new_height


def _downsample_pipeline_canvas(
    canvas: numpy.ndarray,
    *,
    canvas_size: int,
) -> numpy.ndarray:
    if canvas.shape[0] == canvas_size and canvas.shape[1] == canvas_size:
        return canvas
    return cv2.resize(canvas, (canvas_size, canvas_size), interpolation=cv2.INTER_AREA)


def _draw_strokes_on_canvas(
    canvas: numpy.ndarray,
    strokes: list[list[list[float]]],
    *,
    board_to_canvas,
    stroke_width: int,
) -> None:
    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        points: list[tuple[int, int]] = []
        for point in stroke:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x_px, y_px = board_to_canvas(float(point[0]), float(point[1]))
            points.append((int(round(x_px)), int(round(y_px))))
        if len(points) >= 2:
            cv2.polylines(
                canvas,
                [numpy.array(points, dtype=numpy.int32)],
                isClosed=False,
                color=0,
                thickness=max(1, int(stroke_width)),
                lineType=cv2.LINE_AA,
            )


def rasterize_board_strokes_board_frame(
    strokes: list[list[list[float]]],
    *,
    board_width_m: float,
    board_height_m: float,
    canvas_size: int = 640,
    supersample: int = PIPELINE_THUMB_SUPERSAMPLE,
) -> tuple[str, int, int]:
    """Map full board coordinates into a letterboxed square canvas."""
    ss = max(1, int(supersample))
    render_size = canvas_size * ss
    canvas = numpy.full((render_size, render_size), 255, dtype=numpy.uint8)
    scale = float(render_size) / float(max(board_width_m, board_height_m, 1e-6))
    offset_x = (render_size - (board_width_m * scale)) * 0.5
    offset_y = (render_size - (board_height_m * scale)) * 0.5
    stroke_width = max(1, int(round(scale * 0.003)))

    def board_to_canvas(board_x: float, board_y: float) -> tuple[float, float]:
        return (offset_x + (board_x * scale), offset_y + (board_y * scale))

    _draw_strokes_on_canvas(
        canvas,
        strokes,
        board_to_canvas=board_to_canvas,
        stroke_width=stroke_width,
    )
    canvas = _downsample_pipeline_canvas(canvas, canvas_size=canvas_size)
    encoded, width, height = encode_gray_base64(canvas, letterbox=False, max_dim=canvas_size)
    return encoded, width, height


def rasterize_strokes_on_lineart_frame(
    strokes: list[list[list[float]]],
    lineart_gray: numpy.ndarray,
    *,
    board_width_m: float,
    board_height_m: float,
    placement_metadata: dict | None = None,
    canvas_size: int = 640,
    supersample: int = PIPELINE_THUMB_SUPERSAMPLE,
) -> tuple[str, int, int]:
    """Render strokes on the same letterboxed frame used for Final Lineart thumbnails."""
    ss = max(1, int(supersample))
    render_size = canvas_size * ss
    lineart = _ensure_uint8_gray(lineart_gray)
    canvas = numpy.full((render_size, render_size), 255, dtype=numpy.uint8)
    img_height, img_width = lineart.shape[:2]
    left, top, lb_scale, _, _ = _letterbox_layout(img_width, img_height, canvas_size=render_size)

    metadata = dict(placement_metadata or {})
    scale_m_per_px = metadata.get('scale_m_per_px')
    offset_x_m = metadata.get('offset_x_m')
    offset_y_m = metadata.get('offset_y_m')
    offset = metadata.get('offset_m') or {}
    if offset_x_m is None:
        offset_x_m = offset.get('x', 0.0)
    if offset_y_m is None:
        offset_y_m = offset.get('y', 0.0)

    if scale_m_per_px is None or float(scale_m_per_px) <= 1.0e-9:
        return rasterize_board_strokes_board_frame(
            strokes,
            board_width_m=board_width_m,
            board_height_m=board_height_m,
            canvas_size=canvas_size,
            supersample=ss,
        )

    m_per_px = float(scale_m_per_px)
    stroke_width = max(1, int(round(m_per_px * lb_scale * 2.0)))

    def board_to_canvas(board_x: float, board_y: float) -> tuple[float, float]:
        px = (board_x - float(offset_x_m)) / m_per_px
        py = (board_y - float(offset_y_m)) / m_per_px
        return (left + (px * lb_scale), top + (py * lb_scale))

    _draw_strokes_on_canvas(
        canvas,
        strokes,
        board_to_canvas=board_to_canvas,
        stroke_width=stroke_width,
    )
    canvas = _downsample_pipeline_canvas(canvas, canvas_size=canvas_size)
    encoded, width, height = encode_gray_base64(canvas, letterbox=False, max_dim=canvas_size)
    return encoded, width, height


def rasterize_board_strokes_thumbnail(
    strokes: list[list[list[float]]],
    *,
    board_width_m: float,
    board_height_m: float,
    canvas_size: int = 640,
) -> tuple[str, int, int]:
    """Render board-space stroke polylines to a letterboxed PNG thumbnail."""
    canvas = numpy.full((canvas_size, canvas_size), 255, dtype=numpy.uint8)
    if not strokes:
        encoded, width, height = encode_gray_base64(canvas, letterbox=False)
        return encoded, width, height

    xs: list[float] = []
    ys: list[float] = []
    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        for point in stroke:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            xs.append(float(point[0]))
            ys.append(float(point[1]))

    if not xs or not ys:
        encoded, width, height = encode_gray_base64(canvas, letterbox=False)
        return encoded, width, height

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    content_w = max(x_max - x_min, 1e-6)
    content_h = max(y_max - y_min, 1e-6)
    span = max(content_w, content_h)
    pad_m = max(0.05, span * 0.08)
    x_min -= pad_m
    x_max += pad_m
    y_min -= pad_m
    y_max += pad_m
    content_w = max(x_max - x_min, 1e-6)
    content_h = max(y_max - y_min, 1e-6)

    scale = float(canvas_size) / max(content_w, content_h)
    offset_x = int(round((canvas_size - content_w * scale) * 0.5))
    offset_y = int(round((canvas_size - content_h * scale) * 0.5))
    stroke_width = max(1, int(round(scale * 0.003)))

    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        points: list[tuple[int, int]] = []
        for point in stroke:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x_px = int(round((float(point[0]) - x_min) * scale)) + offset_x
            y_px = int(round((float(point[1]) - y_min) * scale)) + offset_y
            points.append((x_px, y_px))
        if len(points) >= 2:
            cv2.polylines(
                canvas,
                [numpy.array(points, dtype=numpy.int32)],
                isClosed=False,
                color=0,
                thickness=stroke_width,
            )

    encoded, width, height = encode_gray_base64(canvas, letterbox=False, max_dim=canvas_size)
    return encoded, width, height
