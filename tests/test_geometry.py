import math

import pytest

from floorshow.common.geometry import (
    Calibration, ball_to_image, image_to_ball, lat_lon, pole_marker, roll_tilt_from_pole,
)


def test_default_is_centred_round_and_70_percent_tall():
    c = Calibration.default(16 / 9)
    assert c.center == pytest.approx((0.5, 0.5))
    assert c.radii[1] * 2 == pytest.approx(0.70)
    W, H = 1920, 1080
    assert c.radii[0] * W == pytest.approx(c.radii[1] * H)
    assert not c.aspect_warning(W, H)


def test_aspect_warning():
    c = Calibration(left=0.3, right=0.7, top=0.2, bottom=0.8)  # 768 x 648 px on 1920x1080
    assert c.aspect_deviation(1920, 1080) == pytest.approx(768 / 648 - 1)
    assert c.aspect_warning(1920, 1080)


def test_mapping_centre_edge_outside():
    c = Calibration.default()
    assert c.pixel_to_image_dir(0.5, 0.5) == pytest.approx((0, 0, 1))
    cx, cy = c.center
    rx, ry = c.radii
    right = c.pixel_to_image_dir(cx + rx * 0.999999, cy)
    assert right[0] == pytest.approx(1, abs=1e-5) and abs(right[2]) < 2e-3
    top = c.pixel_to_image_dir(cx, cy - ry * 0.5)  # v grows downward, so this is above centre
    assert top[1] == pytest.approx(0.5) and top[2] == pytest.approx(math.sqrt(0.75))
    assert c.pixel_to_image_dir(cx + rx * 1.01, cy) is None
    assert c.pixel_to_image_dir(0.0, 0.0) is None


def test_mapping_directions_are_unit_and_front_facing():
    c = Calibration(left=0.2, right=0.6, top=0.1, bottom=0.9)
    for u in (0.25, 0.4, 0.55):
        for v in (0.2, 0.5, 0.8):
            d = c.pixel_to_image_dir(u, v)
            if d:
                assert math.sqrt(sum(x * x for x in d)) == pytest.approx(1)
                assert d[2] >= 0


def test_shift_never_crosses_opposite_line():
    c = Calibration(left=0.4, right=0.6, top=0.4, bottom=0.6)
    assert c.shift("left", 1.0).left == pytest.approx(0.6 - 0.02)
    assert c.shift("right", -1.0).right == pytest.approx(0.4 + 0.02)
    assert c.shift("top", 1.0).top == pytest.approx(0.58)
    assert c.shift("bottom", -1.0).bottom == pytest.approx(0.42)
    assert c.shift("left", 0.05).left == pytest.approx(0.45)
    assert c.shift("left", 1.0).is_valid()


def test_move_keeps_size_and_grow_keeps_centre():
    c = Calibration(left=0.3, right=0.7, top=0.2, bottom=0.8)
    m = c.move(dx=0.05, dy=-0.1)
    assert m.radii == pytest.approx(c.radii)
    assert m.center == pytest.approx((0.55, 0.4))
    g = c.grow(dx=0.02, dy=0.01)
    assert g.center == pytest.approx(c.center)
    assert g.radii == pytest.approx((0.22, 0.31))
    tiny = c.grow(dx=-1.0, dy=-1.0)
    assert tiny.is_valid() and tiny.center == pytest.approx(c.center)


def test_roll_tilt_rotation_roundtrip():
    d = (0.3, 0.5, math.sqrt(1 - 0.09 - 0.25))
    for roll, tilt in [(0, 0), (12, 0), (0, 20), (-8, 15), (30, -25)]:
        back = image_to_ball(ball_to_image(d, roll, tilt), roll, tilt)
        assert back == pytest.approx(d)
    assert image_to_ball(d, 0, 0) == pytest.approx(d)


def test_pole_marker_matches_rotation_of_north_pole():
    for roll, tilt in [(0, 0), (10, 15), (-20, 5), (35, 30)]:
        x, y, z = ball_to_image((0, 1, 0), roll, tilt)
        px, py = pole_marker(roll, tilt)
        assert (x, y) == pytest.approx((px, py))
        assert z == pytest.approx(math.sin(math.radians(tilt)))


@pytest.mark.parametrize("roll,tilt", [(0, 0), (10, 15), (-20, 5), (35, 30), (5, 0.5), (-90, 40)])
def test_pole_marker_roundtrip(roll, tilt):
    r, t = roll_tilt_from_pole(*pole_marker(roll, tilt), projector_above=True)
    assert (r, t) == pytest.approx((roll, tilt), abs=1e-6)


def test_pole_marker_projector_below_flips_tilt_sign():
    r, t = roll_tilt_from_pole(*pole_marker(0, 20), projector_above=False)
    assert t == pytest.approx(-20)


def test_calibration_pixel_to_ball_dir_applies_tilt():
    # Tilting the pole toward the projector (projector above): the ball's north pole sits at the
    # image position (0, cos tilt), so the pixel there must map to lat = 90.
    tilt = 25.0
    c = Calibration(left=0.3, right=0.7, top=0.1, bottom=0.9, tilt_deg=tilt)
    cx, cy = c.center
    rx, ry = c.radii
    u, v = cx, cy - ry * math.cos(math.radians(tilt))
    lat, _ = lat_lon(c.pixel_to_ball_dir(u, v))
    assert lat == pytest.approx(90.0, abs=1e-6)
    # the point facing the projector (image centre) is then at latitude = tilt
    lat0, lon0 = lat_lon(c.pixel_to_ball_dir(cx, cy))
    assert lat0 == pytest.approx(tilt, abs=1e-6) and lon0 == pytest.approx(0, abs=1e-6)
