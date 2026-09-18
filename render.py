"""moderngl renderer: ray-sphere intersection and shape evaluation happen
entirely in shaders/sphere.frag on the GPU. Python's per-frame work is
limited to evaluating each shape's current (center, radii, color) --
a handful of floats, independent of output resolution -- and uploading
them as uniforms, so the CPU stays free for audio/beat analysis.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import glfw
import moderngl
import numpy as np

from shapes import Annulus

HERE = Path(__file__).parent
MAX_SHAPES = 32


@dataclass
class ProjectorConfig:
    """The real projector's position/orientation/lens -- calibrate these
    against the physical rig (throw distance ~3-5m per the design plan)."""

    position: tuple[float, float, float] = (0.0, 0.0, 4.0)  # meters
    # Vertical FOV only (from the lens/throw-ratio spec, e.g. a throw
    # calculator's "image height at this distance"). Horizontal FOV is
    # derived each frame from this and the live output aspect ratio --
    # a lens has one focal length, so hfov/vfov are never independent;
    # setting both separately risks a mismatch that stretches every
    # shape into an ellipse.
    vfov_deg: float = 15.0
    up: tuple[float, float, float] = (0.0, 1.0, 0.0)
    # aim_at defaults to the ball's center; override for a deliberately
    # off-axis projector calibration.
    aim_at: Optional[tuple[float, float, float]] = None


@dataclass
class BallConfig:
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.3  # meters


@dataclass
class Scene:
    shapes: list[Annulus] = field(default_factory=list)
    continents_enabled: bool = False
    # t -> 3x3 rotation matrix (numpy, row-major, v' = R @ v) applied to
    # the hit direction before shape evaluation -- e.g. to spin a globe.
    ball_rotation: Optional[Callable[[float], np.ndarray]] = None


def rotation_y(theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype="f4")


def _normalize(v) -> np.ndarray:
    v = np.array(v, dtype="f4")
    return v / np.linalg.norm(v)


def _projector_basis(projector: ProjectorConfig, ball: BallConfig):
    target = np.array(
        projector.aim_at if projector.aim_at is not None else ball.center, dtype="f4"
    )
    forward = _normalize(target - np.array(projector.position, dtype="f4"))
    world_up = np.array(projector.up, dtype="f4")
    if abs(np.dot(forward, world_up)) > 0.999:
        world_up = np.array([1.0, 0.0, 0.0], dtype="f4")
    right = _normalize(np.cross(forward, world_up))
    up = np.cross(right, forward)
    return right, up, forward


def _setup_window(width: int = 1280, height: int = 800):
    """Fullscreen on the second monitor if present (the real projector), else windowed."""
    if not glfw.init():
        raise RuntimeError("failed to initialize GLFW")

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)

    monitors = glfw.get_monitors()
    if len(monitors) >= 2:
        monitor = monitors[1]
        mode = glfw.get_video_mode(monitor)
        window = glfw.create_window(
            mode.size.width, mode.size.height, "mirrorballs3", monitor, None
        )
    else:
        window = glfw.create_window(width, height, "mirrorballs3", None, None)

    if not window:
        glfw.terminate()
        raise RuntimeError("failed to create GLFW window")

    glfw.make_context_current(window)
    # vsync -- comfortably clears the 30fps target on any real display
    glfw.swap_interval(1)
    return window


class Engine:
    def __init__(
        self, projector: ProjectorConfig, ball: BallConfig, windowed: bool = True
    ):
        self.projector = projector
        self.ball = ball

        if windowed:
            self.window = _setup_window()
            self.ctx = moderngl.create_context()
        else:
            self.window = None
            self.ctx = moderngl.create_standalone_context()

        vert = (HERE / "shaders" / "quad.vert").read_text()
        frag = (HERE / "shaders" / "sphere.frag").read_text()
        self.prog = self.ctx.program(vertex_shader=vert, fragment_shader=frag)

        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
        vbo = self.ctx.buffer(quad.tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(vbo, "2f", "in_position")])

        # Dummy 1x1 texture so u_continentsTex is always a valid binding,
        # even when no continents texture has been loaded.
        self._dummy_tex = self.ctx.texture((1, 1), 4, data=bytes(4))
        self._continents_tex = None

        self._set_static_uniforms()

    def _set_static_uniforms(self):
        p = self.projector
        self.prog["u_projPos"].value = tuple(p.position)
        self._tan_half_vfov = math.tan(math.radians(p.vfov_deg) / 2)
        self.prog["u_tanHalfVFov"].value = self._tan_half_vfov
        self.prog["u_ballCenter"].value = tuple(self.ball.center)
        self.prog["u_ballRadius"].value = self.ball.radius

        right, up, forward = _projector_basis(self.projector, self.ball)
        self.prog["u_projRotation"].write(
            np.concatenate([right, up, forward]).astype("f4").tobytes()
        )

    def load_continents_texture(self, path: str):
        from PIL import Image

        img = Image.open(path).convert("RGBA")
        self._continents_tex = self.ctx.texture(img.size, 4, img.tobytes())
        self._continents_tex.build_mipmaps()

    def _upload_scene(self, scene: Scene, t: float):
        n = min(len(scene.shapes), MAX_SHAPES)
        centers = np.zeros((MAX_SHAPES, 3), dtype="f4")
        radii = np.zeros((MAX_SHAPES, 2), dtype="f4")
        colors = np.zeros((MAX_SHAPES, 4), dtype="f4")
        for i, shape in enumerate(scene.shapes[:n]):
            center, shape_radii, color = shape.params_at(t)
            centers[i] = center
            radii[i] = shape_radii
            colors[i] = color

        self.prog["u_numShapes"].value = n
        self.prog["u_shapeCenter"].write(centers.tobytes())
        self.prog["u_shapeRadii"].write(radii.tobytes())
        self.prog["u_shapeColor"].write(colors.tobytes())

        rotation = (
            scene.ball_rotation(t) if scene.ball_rotation else np.eye(3, dtype="f4")
        )
        self.prog["u_ballRotation"].write(rotation.astype("f4").tobytes(order="F"))

        use_continents = scene.continents_enabled and self._continents_tex is not None
        tex = self._continents_tex if use_continents else self._dummy_tex
        tex.use(location=0)
        self.prog["u_continentsTex"].value = 0
        self.prog["u_continentsEnabled"].value = use_continents

    def render_frame(self, scene: Scene, t: float, width: int, height: int):
        self.ctx.viewport = (0, 0, width, height)
        self.prog["u_resolution"].value = (float(width), float(height))
        # hfov is derived from vfov + the live aspect ratio every frame
        # (not stored), so it can never drift out of sync with vfov or
        # with the actual window size (e.g. after a resize).
        self.prog["u_tanHalfHFov"].value = self._tan_half_vfov * (width / height)
        self._upload_scene(scene, t)
        self.ctx.clear(0.0, 0.0, 0.0)
        self.vao.render(moderngl.TRIANGLE_STRIP)

    def run(self, get_scene: Callable[[], Scene]):
        """Run the render loop. get_scene() is called every frame so the
        caller can swap scenes at any time (e.g. from a key callback)."""
        if self.window is None:
            raise RuntimeError("run() needs a window; construct Engine(windowed=True)")

        start = time.perf_counter()
        while not glfw.window_should_close(self.window):
            width, height = glfw.get_framebuffer_size(self.window)
            t = time.perf_counter() - start
            self.render_frame(get_scene(), t, width, height)

            glfw.swap_buffers(self.window)
            glfw.poll_events()
            if glfw.get_key(self.window, glfw.KEY_ESCAPE) == glfw.PRESS:
                glfw.set_window_should_close(self.window, True)

        glfw.terminate()
