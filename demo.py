"""Four demo scenes, switchable with keys 1-4. Esc quits.

1  Horizontal band sweeping north pole → south pole (loops every 6s)
2  Vertical meridian band sweeping east → west (loops every 6s)
3  Centered circle pulsating between a dot and covering the full hemisphere
4  Small circle wandering smoothly across the sphere

All scenes use lambdas of global time so they animate continuously and
the current position is always coherent, regardless of when you switch.
"""

import math

import glfw

from render import BallConfig, Engine, ProjectorConfig, Scene
from shapes import Annulus, FRONT, NORTH, latlon_to_dir


def _scene_1() -> Scene:
    """Horizontal band sweeping from north to south pole, looping every 6s."""
    period = 6.0
    w = math.radians(6.0)

    def inner(t: float) -> float:
        u = (t % period) / period
        return max(0.0, u * math.pi - w / 2)

    def outer(t: float) -> float:
        u = (t % period) / period
        return min(math.pi, u * math.pi + w / 2)

    return Scene(shapes=[Annulus(NORTH, inner, outer, color=(0.2, 0.6, 1.0, 1.0))])


def _scene_2() -> Scene:
    """Vertical meridian band sweeping east → west, looping every 6s."""
    period = 6.0
    w = math.radians(6.0)

    # Meridian band = ring of radius 90° centred on an equatorial point.
    # Sweeping the band = rotating that equatorial centre point over time.
    def center(t: float):
        lon = (t % period) / period * 360.0
        return latlon_to_dir(0.0, lon + 90.0)

    return Scene(shapes=[Annulus(center, math.radians(90) - w / 2, math.radians(90) + w / 2,
                                  color=(1.0, 0.3, 0.2, 1.0))])


def _scene_3() -> Scene:
    """Centered circle pulsating in size (dot → hemisphere → dot, 3s period)."""
    def outer(t: float) -> float:
        return abs(math.sin(t * math.pi / 3.0)) * math.radians(80)

    return Scene(shapes=[Annulus(FRONT, 0.0, outer, color=(1.0, 1.0, 1.0, 1.0))])


def _scene_4() -> Scene:
    """Small circle wandering smoothly using incommensurate sinusoids (like a Lissajous path on the sphere)."""
    radius = math.radians(6.0)

    def center(t: float):
        lat = 30.0 * (math.sin(0.71 * t) + 0.45 * math.sin(1.37 * t + 1.1))
        lon = 60.0 * (math.sin(0.53 * t + 0.8) + 0.45 * math.sin(1.13 * t + 2.3))
        return latlon_to_dir(lat, lon)

    return Scene(shapes=[Annulus(center, 0.0, radius, color=(1.0, 0.85, 0.2, 1.0))])


SCENES = [_scene_1(), _scene_2(), _scene_3(), _scene_4()]
NAMES = [
    "1  horizontal sweeping band",
    "2  vertical sweeping band",
    "3  pulsating circle",
    "4  wandering circle",
]


def main():
    projector = ProjectorConfig(position=(0.0, 0.0, 1.2), vfov_deg=30.0)
    ball = BallConfig(center=(0.0, 0.0, 0.0), radius=0.3)
    engine = Engine(projector, ball)

    current = [0]
    print("Keys 1-4 to switch scene, Esc to quit")
    print(f"  → {NAMES[0]}")

    def key_callback(window, key, scancode, action, mods):
        if action != glfw.PRESS:
            return
        idx = key - glfw.KEY_1
        if 0 <= idx < len(SCENES):
            current[0] = idx
            print(f"  → {NAMES[idx]}")

    glfw.set_key_callback(engine.window, key_callback)
    engine.run(lambda: SCENES[current[0]])


if __name__ == "__main__":
    main()
