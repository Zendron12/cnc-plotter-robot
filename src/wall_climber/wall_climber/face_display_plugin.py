from __future__ import annotations

import math
import os
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from std_msgs.msg import String

try:
    from rclpy.executors import ShutdownException
except ImportError:  # pragma: no cover - older rclpy versions do not expose this symbol
    class ShutdownException(Exception):
        pass


class FaceDisplayPlugin:
    """Two-state face screen.

    * ``idle``  -> a calm smiling face whose eyes blink periodically. Shown when
      the carriage is parked.
    * ``nyan``  -> the Nyan Cat animation (pop-tart cat trailing a scrolling
      rainbow with twinkling stars). Shown while the carriage is moving, i.e.
      while it is executing a draw or write job.

    The expression is published on ``expression_topic`` by the cable supervisor,
    which derives it from real carriage motion. Anything that is not ``nyan``
    renders the idle face, so a manual override stays robust.
    """

    NYAN_FPS = 10.0
    IDLE_FPS = 20.0

    def init(self, webots_node, properties):
        self._robot = webots_node.robot
        self._display_name = str(properties.get('display_name', 'face_display'))
        self._default_text = str(properties.get('default_text', ''))
        self._text_topic = str(properties.get('text_topic', '/wall_climber/face/text'))
        self._expression_topic = str(
            properties.get('expression_topic', '/wall_climber/face/expression')
        )
        self._blink_period_sec = max(0.8, float(properties.get('blink_period_sec', '3.8')))
        self._blink_duration_sec = max(0.05, float(properties.get('blink_duration_sec', '0.14')))
        self._spin_timeout_sec = max(0.001, float(properties.get('spin_timeout_sec', '0.01')))

        self._display = self._robot.getDevice(self._display_name)
        self._width = 0
        self._height = 0
        self._text = self._default_text
        self._expression = 'idle'
        self._last_blink_started = time.monotonic()
        self._last_signature = None

        # Pre-decoded animation frames as Webots image handles. Built once from
        # the bundled GIF assets so the runtime just blits a crisp bitmap per
        # frame instead of drawing primitives.
        #   nyan -> Nyan Cat, shown while drawing/writing.
        #   idle -> cute robot, shown while parked.
        self._nyan_images = []
        self._nyan_blit_xy = (0, 0)
        self._idle_images = []
        self._idle_blit_xy = (0, 0)
        self._idle_bg = 0xFFFFFF

        if self._display is None:
            print(f'[FaceDisplayPlugin] WARNING: display "{self._display_name}" not found')
        else:
            self._width = int(self._display.getWidth())
            self._height = int(self._display.getHeight())
            self._load_nyan_frames()
            self._load_idle_frames()
            self._redraw(force=True)

        if not rclpy.ok():
            rclpy.init(args=None)

        self._node = rclpy.create_node('face_display_plugin')
        self._node.create_subscription(String, self._text_topic, self._text_cb, 1)
        self._node.create_subscription(String, self._expression_topic, self._expression_cb, 1)

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_running = True
        self._spin_thread = threading.Thread(
            target=self._spin_loop,
            name='face_display_plugin_spin',
            daemon=True,
        )
        self._spin_thread.start()
        self._node.get_logger().info(
            f'Face display plugin ready on device "{self._display_name}".'
        )

    # ------------------------------------------------------------------ ROS

    def _text_cb(self, msg: String):
        value = str(msg.data).strip()
        self._text = value[:18] if value else self._default_text

    def _expression_cb(self, msg: String):
        value = str(msg.data).strip().lower()
        if value:
            self._expression = value

    def _spin_loop(self):
        while self._spin_running:
            try:
                if not rclpy.ok():
                    break
                self._executor.spin_once(timeout_sec=self._spin_timeout_sec)
            except (ExternalShutdownException, ShutdownException):
                break
            except Exception:
                if not self._spin_running or not rclpy.ok():
                    break
                try:
                    self._node.get_logger().error('Face display plugin spin loop stopped unexpectedly.')
                except Exception:
                    pass
                break

    # ------------------------------------------------------------ primitives

    def _is_blinking(self) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_blink_started
        if elapsed >= self._blink_period_sec:
            self._last_blink_started = now
            return True
        return elapsed <= self._blink_duration_sec

    def _anim_frame(self, fps: float = None) -> int:
        return int(time.monotonic() * (fps if fps is not None else self.NYAN_FPS))

    def _signature(self):
        if self._expression == 'nyan':
            return ('nyan', self._anim_frame(self.NYAN_FPS))
        # Idle: if we have GIF frames, animate by frame; otherwise fall back to
        # the drawn blinking face.
        if self._idle_images:
            return ('idle', self._anim_frame(self.IDLE_FPS))
        return ('idle', self._is_blinking())

    def _set_color(self, rgb_hex: int):
        self._display.setColor(int(rgb_hex))

    def _fill_rect(self, x: int, y: int, w: int, h: int):
        if w <= 0 or h <= 0:
            return
        self._display.fillRectangle(int(x), int(y), int(w), int(h))

    def _fill_oval(self, x: int, y: int, w: int, h: int):
        # Webots' fillOval takes a CENTRE point and SEMI-axes (radii). Every call
        # site here works in the intuitive "top-left corner + full size"
        # convention, so convert: a box (x, y, w, h) becomes an ellipse that fits
        # exactly inside that box.
        a = max(1, int(w) // 2)
        b = max(1, int(h) // 2)
        self._display.fillOval(int(x) + a, int(y) + b, a, b)

    def _draw_line(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._display.drawLine(int(x1), int(y1), int(x2), int(y2))

    def _fill_arc(self, cx: int, cy: int, width: int, height: int,
                  start_deg: float, sweep_deg: float, thickness: int = 2) -> None:
        """Smooth curved stroke of constant thickness.

        Webots has no native arc primitive, so we walk the centre-line of the
        arc and stamp small filled circles along it.
        """
        if width <= 0 or height <= 0:
            return
        rx = width / 2.0
        ry = height / 2.0
        radius = max(1, int(thickness) // 2)
        arc_len = math.radians(abs(sweep_deg)) * max(rx, ry)
        segments = max(24, int(arc_len / max(1.0, radius * 0.8)))
        step = float(sweep_deg) / float(segments)
        for index in range(segments + 1):
            angle = math.radians(start_deg + step * index)
            x = cx + rx * math.cos(angle)
            y = cy + ry * math.sin(angle)
            self._display.fillOval(int(x), int(y), radius, radius)

    # --------------------------------------------------------------- redraw

    def _redraw(self, force: bool = False):
        if self._display is None:
            return
        signature = self._signature()
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature

        if signature[0] == 'nyan':
            self._draw_nyan(signature[1])
        elif self._idle_images:
            self._draw_idle_gif(signature[1])
        else:
            self._draw_idle(blink=signature[1])

    # ---------------------------------------------------------- idle face

    def _draw_idle(self, blink: bool):
        width = self._width
        height = self._height
        bg = 0x071014
        bezel = 0x10242A
        bezel_glow = 0x1A3942
        fg = 0x63E5F0
        accent = 0xE96A19
        white = 0xE8F6FA

        self._set_color(bg)
        self._fill_rect(0, 0, width, height)

        # Two-tone LCD bezel.
        border = max(3, min(width, height) // 32)
        self._set_color(bezel)
        self._fill_rect(0, 0, width, border)
        self._fill_rect(0, height - border, width, border)
        self._fill_rect(0, 0, border, height)
        self._fill_rect(width - border, 0, border, height)
        self._set_color(bezel_glow)
        inner = max(1, border // 2)
        self._fill_rect(border, border, width - 2 * border, inner)
        self._fill_rect(border, height - border - inner, width - 2 * border, inner)

        # Big forward-looking eyes, set high so there is clear space above the
        # smile.
        eye_w = max(24, int(width * 0.19))
        eye_h = max(26, int(height * 0.40))
        eye_cy = int(height * 0.34)
        left_cx = int(width * 0.31)
        right_cx = int(width * 0.69)
        self._set_color(fg)
        if blink:
            line_h = max(4, eye_h // 7)
            self._fill_rect(left_cx - eye_w // 2, eye_cy - line_h // 2, eye_w, line_h)
            self._fill_rect(right_cx - eye_w // 2, eye_cy - line_h // 2, eye_w, line_h)
        else:
            self._fill_oval(left_cx - eye_w // 2, eye_cy - eye_h // 2, eye_w, eye_h)
            self._fill_oval(right_cx - eye_w // 2, eye_cy - eye_h // 2, eye_w, eye_h)
            # Pupils centred so the robot looks straight ahead.
            pupil_w = max(8, int(eye_w * 0.46))
            pupil_h = max(8, int(eye_h * 0.46))
            self._set_color(bg)
            self._fill_oval(left_cx - pupil_w // 2, eye_cy - pupil_h // 2, pupil_w, pupil_h)
            self._fill_oval(right_cx - pupil_w // 2, eye_cy - pupil_h // 2, pupil_w, pupil_h)
            # Sparkle highlight on the upper inner edge of each pupil.
            sparkle = max(3, min(eye_w, eye_h) // 7)
            self._set_color(white)
            self._fill_oval(left_cx - pupil_w // 4 - sparkle // 2, eye_cy - pupil_h // 3, sparkle, sparkle)
            self._fill_oval(right_cx - pupil_w // 4 - sparkle // 2, eye_cy - pupil_h // 3, sparkle, sparkle)

        # Deep warm smile, well below the eyes.
        self._set_color(accent)
        mouth_cx = width // 2
        mouth_cy = int(height * 0.72)
        mouth_w = int(width * 0.36)
        mouth_h = int(height * 0.32)
        self._fill_arc(
            mouth_cx, mouth_cy - mouth_h // 2,
            mouth_w, mouth_h,
            start_deg=18.0, sweep_deg=144.0,
            thickness=max(5, height // 18),
        )

    # ---------------------------------------------------------- nyan cat

    # Path to the bundled Nyan Cat animation, relative to the package share dir.
    _NYAN_ASSET_REL = os.path.join('assets', 'nyan', 'nyan_cat.gif')

    def _nyan_asset_path(self):
        """Locate the bundled Nyan GIF in the installed share dir or source tree."""
        candidates = []
        try:
            from ament_index_python.packages import get_package_share_directory
            candidates.append(
                os.path.join(get_package_share_directory('wall_climber'), self._NYAN_ASSET_REL)
            )
        except Exception:
            pass
        # Fallback: source tree relative to this file (…/wall_climber/wall_climber/).
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.normpath(os.path.join(here, '..', self._NYAN_ASSET_REL)))
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _load_nyan_frames(self):
        """Decode the GIF once into Webots image handles scaled to the display.

        Uses Pillow (available in the controller's Python) to read the frames,
        fit them to the screen preserving aspect ratio, then hands raw RGBA
        bytes to Webots via ``imageNew``. If anything fails we simply leave the
        frame list empty and fall back to a plain background.
        """
        path = self._nyan_asset_path()
        if path is None:
            print('[FaceDisplayPlugin] WARNING: nyan_cat.gif asset not found')
            return
        try:
            from PIL import Image
        except Exception:
            print('[FaceDisplayPlugin] WARNING: Pillow unavailable; nyan disabled')
            return
        try:
            gif = Image.open(path)
        except Exception as exc:
            print(f'[FaceDisplayPlugin] WARNING: failed to open nyan gif: {exc}')
            return

        # The source GIF is a tall canvas with a lot of empty space above and
        # below the cat. Find the tight bounding box of the bright cat+rainbow
        # content across all frames and crop to it, then crop that band to the
        # display's aspect ratio (anchored on the right so the cat is always in
        # frame) and scale to FILL the screen, so the cat is large instead of
        # floating small in a void.
        crop_box = self._nyan_content_bbox(gif, Image)
        if crop_box is None:
            crop_box = (0, 0, gif.size[0], gif.size[1])

        bx0, by0, bx1, by1 = crop_box
        content_w = bx1 - bx0
        content_h = by1 - by0
        target_aspect = self._width / float(self._height)
        # Content is much wider than the screen, so keep full height and crop the
        # width to the screen aspect, anchored to the right edge (the cat).
        crop_w = int(content_h * target_aspect)
        if crop_w < content_w:
            bx0 = bx1 - crop_w
        else:
            # Content narrower than screen aspect: keep full width, crop height.
            crop_h = int(content_w / target_aspect)
            by0 = by1 - crop_h
        fill_box = (bx0, by0, bx1, by1)

        # Fill the whole display.
        dst_w = self._width
        dst_h = self._height
        self._nyan_blit_xy = (0, 0)

        frames = getattr(gif, 'n_frames', 1)
        for index in range(frames):
            try:
                gif.seek(index)
                frame = gif.convert('RGBA').crop(fill_box)
                frame = frame.resize((dst_w, dst_h), Image.NEAREST)
                data = frame.tobytes()  # RGBA, row-major
                handle = self._display.imageNew(data, self._display.RGBA, dst_w, dst_h)
                self._nyan_images.append(handle)
            except Exception as exc:
                print(f'[FaceDisplayPlugin] WARNING: nyan frame {index} failed: {exc}')

    @staticmethod
    def _nyan_content_bbox(gif, image_module):
        """Tight bounding box of the bright cat+rainbow content across frames.

        Ignores the dim starfield background so the crop hugs the cat. Returns
        an (left, top, right, bottom) box or None if detection fails.
        """
        try:
            width, height = gif.size
            min_x, min_y, max_x, max_y = width, height, 0, 0
            found = False
            step = max(1, min(width, height) // 200)
            frame_count = getattr(gif, 'n_frames', 1)
            for index in range(frame_count):
                gif.seek(index)
                px = gif.convert('RGB').load()
                for y in range(0, height, step):
                    for x in range(0, width, step):
                        r, g, b = px[x, y]
                        sat = max(r, g, b) - min(r, g, b)
                        bright_sat = sat > 70 and max(r, g, b) > 120
                        grey_cat = min(r, g, b) > 110 and sat < 40 and max(r, g, b) < 210
                        if bright_sat or grey_cat:
                            found = True
                            if x < min_x:
                                min_x = x
                            if x > max_x:
                                max_x = x
                            if y < min_y:
                                min_y = y
                            if y > max_y:
                                max_y = y
            gif.seek(0)
            if not found:
                return None
            # Pad a little so we do not clip the cat's outline.
            pad = max(2, step * 2)
            min_x = max(0, min_x - pad)
            min_y = max(0, min_y - pad)
            max_x = min(width, max_x + pad)
            max_y = min(height, max_y + pad)
            return (min_x, min_y, max_x, max_y)
        except Exception:
            try:
                gif.seek(0)
            except Exception:
                pass
            return None

    def _draw_nyan(self, frame: int):
        # Deep-space background, then blit the pre-decoded GIF frame on top.
        self._set_color(0x0A0A2E)
        self._fill_rect(0, 0, self._width, self._height)
        self._draw_stars(frame)
        if not self._nyan_images:
            return
        handle = self._nyan_images[frame % len(self._nyan_images)]
        x, y = self._nyan_blit_xy
        try:
            self._display.imagePaste(handle, int(x), int(y), True)
        except Exception:
            pass

    def _draw_stars(self, frame: int):
        width = self._width
        height = self._height
        self._set_color(0xFFFFFF)
        bases = (
            (0.06, 0.20), (0.20, 0.72), (0.34, 0.40), (0.46, 0.86),
            (0.12, 0.50), (0.40, 0.18), (0.28, 0.62), (0.50, 0.30),
            (0.62, 0.55), (0.74, 0.24),
        )
        scroll = (frame * max(6, int(width * 0.03))) % width
        for index, (fx, fy) in enumerate(bases):
            sx = int(fx * width - scroll) % width
            sy = int(fy * height)
            self._draw_star(sx, sy, (frame + index) % 4)

    def _draw_star(self, x: int, y: int, phase: int):
        # Twinkling plus-shaped star with a 4-frame size cycle.
        size = (1, 2, 3, 2)[phase]
        self._fill_rect(x - size, y, 2 * size + 1, 1)
        self._fill_rect(x, y - size, 1, 2 * size + 1)
        if size >= 2:
            self._fill_rect(x - 1, y - 1, 1, 1)
            self._fill_rect(x + 1, y + 1, 1, 1)
            self._fill_rect(x - 1, y + 1, 1, 1)
            self._fill_rect(x + 1, y - 1, 1, 1)

    # -------------------------------------------------------- idle robot gif

    # Path to the bundled idle "cute robot" animation, relative to share dir.
    _IDLE_ASSET_REL = os.path.join('assets', 'face', 'idle_robot.gif')

    def _idle_asset_path(self):
        candidates = []
        try:
            from ament_index_python.packages import get_package_share_directory
            candidates.append(
                os.path.join(get_package_share_directory('wall_climber'), self._IDLE_ASSET_REL)
            )
        except Exception:
            pass
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.normpath(os.path.join(here, '..', self._IDLE_ASSET_REL)))
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _load_idle_frames(self):
        """Decode the idle robot GIF into Webots image handles.

        Fits the whole frame to the screen preserving aspect ratio (the source
        is square with a white background, so we centre it and match the screen
        background to its corner colour to avoid black bars).
        """
        path = self._idle_asset_path()
        if path is None:
            print('[FaceDisplayPlugin] WARNING: idle_robot.gif asset not found; using drawn face')
            return
        try:
            from PIL import Image
        except Exception:
            print('[FaceDisplayPlugin] WARNING: Pillow unavailable; idle gif disabled')
            return
        try:
            gif = Image.open(path)
        except Exception as exc:
            print(f'[FaceDisplayPlugin] WARNING: failed to open idle gif: {exc}')
            return

        # Match the screen background to the GIF's own background colour so the
        # letterbox margins blend in seamlessly.
        try:
            gif.seek(0)
            corner = gif.convert('RGB').getpixel((1, 1))
            self._idle_bg = (corner[0] << 16) | (corner[1] << 8) | corner[2]
        except Exception:
            self._idle_bg = 0xFFFFFF

        # Fit preserving aspect ratio, filling as much of the screen as possible.
        src_w, src_h = gif.size
        scale = min(self._width / src_w, self._height / src_h)
        dst_w = max(1, int(src_w * scale))
        dst_h = max(1, int(src_h * scale))
        self._idle_blit_xy = ((self._width - dst_w) // 2, (self._height - dst_h) // 2)

        frames = getattr(gif, 'n_frames', 1)
        # Cap the number of frames we cache so we do not hold a huge GIF entirely
        # in display memory; sampling every Nth frame keeps the motion smooth.
        max_frames = 60
        stride = max(1, frames // max_frames)
        for index in range(0, frames, stride):
            try:
                gif.seek(index)
                frame = gif.convert('RGBA').resize((dst_w, dst_h), Image.BILINEAR)
                data = frame.tobytes()
                handle = self._display.imageNew(data, self._display.RGBA, dst_w, dst_h)
                self._idle_images.append(handle)
            except Exception as exc:
                print(f'[FaceDisplayPlugin] WARNING: idle frame {index} failed: {exc}')

    def _draw_idle_gif(self, frame: int):
        # Background matched to the GIF, then blit the pre-decoded frame.
        self._set_color(self._idle_bg)
        self._fill_rect(0, 0, self._width, self._height)
        if not self._idle_images:
            return
        handle = self._idle_images[frame % len(self._idle_images)]
        x, y = self._idle_blit_xy
        try:
            self._display.imagePaste(handle, int(x), int(y), True)
        except Exception:
            pass

    # ----------------------------------------------------------- lifecycle

    def step(self):
        self._redraw(force=False)

    def cleanup(self):
        self._spin_running = False
        # Release pre-decoded image handles for both animations.
        display = getattr(self, '_display', None)
        for attr in ('_nyan_images', '_idle_images'):
            images = getattr(self, attr, None)
            if images and display is not None:
                for handle in images:
                    try:
                        display.imageDelete(handle)
                    except Exception:
                        pass
                setattr(self, attr, [])
        spin_thread = getattr(self, '_spin_thread', None)
        if spin_thread is not None and spin_thread.is_alive():
            spin_thread.join(timeout=0.2)
        executor = getattr(self, '_executor', None)
        node = getattr(self, '_node', None)
        if executor is not None and node is not None:
            try:
                executor.remove_node(node)
            except Exception:
                pass
            try:
                executor.shutdown(timeout_sec=0.1)
            except Exception:
                pass

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
