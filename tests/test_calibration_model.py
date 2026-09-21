import pytest

from mirrorballs.common.geometry import Calibration
from mirrorballs.control.calibration_model import Session, fmt_px, key_to_action

W, H = 1920, 1080


def session(**kw):
    return Session(Calibration.default(W / H), W, H, **kw)


def test_default_readouts_match_the_design_board():
    s = session()
    assert s.centre_px() == pytest.approx((960, 540))
    assert s.size_px() == pytest.approx((756, 756))  # 70 % of 1080 high, and round
    assert s.aspect == pytest.approx(1.0) and not s.aspect_warning and not s.dirty
    assert s.line_px("top")[0] == pytest.approx(162)


def test_steps_are_pixels_not_fractions():
    s = session()
    s.nudge("left", -1)  # 1 px left at "normal"
    assert s.cal.left * W == pytest.approx(session().cal.left * W - 1)
    s.step = "coarse"
    s.nudge("top", +1)  # 10 px down on the vertical axis
    assert s.cal.top * H == pytest.approx(session().cal.top * H + 10)
    s.step = "fine"
    s.nudge("right", +1)
    assert s.cal.right * W == pytest.approx(session().cal.right * W + 0.25)
    assert s.active == "right" and s.dirty


def test_move_keeps_size_and_grow_keeps_centre():
    s = session()
    size0, centre0 = s.size_px(), s.centre_px()
    s.step = "coarse"
    s.apply("move", (1, -1))
    assert s.size_px() == pytest.approx(size0)
    assert s.centre_px() == pytest.approx((centre0[0] + 10, centre0[1] - 10))
    c = s.centre_px()
    s.apply("grow", +1)
    assert s.centre_px() == pytest.approx(c)
    assert s.size_px()[0] == pytest.approx(size0[0] + 20) and s.size_px()[1] == pytest.approx(size0[1] + 20)
    s.apply("grow", -1)
    assert s.size_px() == pytest.approx(size0)


def test_aspect_warning_matches_the_design_copy():
    s = session()
    s.step = "coarse"
    for _ in range(20):
        s.nudge("right", +1)  # widen by 200 px
    assert s.aspect_warning
    assert s.aspect_message().startswith("Aspect off by ") and "re-check Left and Right" in s.aspect_message()
    assert "%" in s.aspect_message()


def test_reset_and_dirty():
    s = session()
    s.apply("line", ("left", -1))
    assert s.dirty
    s.reset(Calibration.default(W / H))
    assert not s.dirty and s.active is None


def test_apply_reports_whether_the_calibration_changed():
    s = session()
    assert s.apply("line", ("left", -1)) is True
    assert s.apply("step", "coarse") is False and s.step == "coarse"
    assert s.apply("blink", None) is False and s.blink
    assert s.apply("grid", None) is False and s.grid
    s.cal = Calibration(0.5, 0.52, 0.5, 0.52)  # already at the minimum size
    assert s.apply("line", ("left", +1)) is False  # a line cannot cross its opposite, so nothing changed
    assert s.cal == Calibration(0.5, 0.52, 0.5, 0.52)


def test_preview_payload_is_a_valid_protocol_message():
    from mirrorballs.common.protocol import parse_command

    s = session(blink=True, grid=True)
    s.apply("line", ("bottom", +1))
    cmd = parse_command({"cmd": "calibration.preview", **s.preview()})
    assert cmd.active == "bottom" and cmd.blink and cmd.grid and cmd.bottom == pytest.approx(s.cal.bottom)


@pytest.mark.parametrize("key,expected", [
    ("a", ("line", ("left", -1))), ("D", ("line", ("left", +1))), ("j", ("line", ("right", -1))), ("L", ("line", ("right", +1))),
    ("w", ("line", ("top", -1))), ("s", ("line", ("top", +1))), ("i", ("line", ("bottom", -1))), ("k", ("line", ("bottom", +1))),
    ("Left", ("move", (-1, 0))), ("Down", ("move", (0, 1))), ("plus", ("grow", 1)), ("minus", ("grow", -1)), ("KP_Add", ("grow", 1)),
    ("1", ("step", "fine")), ("3", ("step", "coarse")), ("b", ("blink", None)), ("G", ("grid", None)),
    ("Escape", ("cancel", None)), ("Return", ("next", None)), ("KP_Enter", ("next", None)),
])
def test_key_map_matches_the_shortcut_sheet(key, expected):
    assert key_to_action(key) == expected


def test_no_shortcut_for_reset_and_unknown_keys_are_ignored():
    assert key_to_action("r") is None and key_to_action("R") is None  # deliberately none: too easy to hit
    assert key_to_action("x") is None and key_to_action("space") is None


def test_fmt_px():
    assert fmt_px(960.0) == "960" and fmt_px(425.25) == "425.25" and fmt_px(0.5) == "0.5"


# --- step 2 and 3 -------------------------------------------------------------------------------

import math  # noqa: E402

from mirrorballs.common.geometry import pole_marker  # noqa: E402
from mirrorballs.control.calibration_model import (  # noqa: E402
    AxisSession, RowsSession, axis_key_to_action, done_key_to_action, rows_key_to_action,
)


def axis(**kw):
    return AxisSession(Calibration.default(W / H), W, H, **kw)


def test_axis_starts_at_the_saved_roll_and_tilt():
    cal = Calibration(0.3, 0.7, 0.1, 0.9, roll_deg=10, tilt_deg=25)
    a = AxisSession(cal, W, H)
    assert (a.px, a.py) == pytest.approx(pole_marker(10, 25))
    r, t = a.roll_tilt()
    assert (r, t) == pytest.approx((10, 25), abs=1e-6) and a.above and not a.dirty
    below = AxisSession(Calibration(0.3, 0.7, 0.1, 0.9, tilt_deg=-15), W, H)
    assert not below.above and below.roll_tilt()[1] == pytest.approx(-15, abs=1e-6)


def test_axis_default_is_a_level_projector_with_the_pole_on_the_silhouette_top():
    a = axis()
    assert (a.px, a.py) == pytest.approx((0, 1)) and a.roll_tilt() == pytest.approx((0, 0), abs=1e-6) and not a.dirty


def test_moving_the_crosshair_down_from_the_top_creates_tilt():
    a = axis()
    a.step = "coarse"
    for _ in range(10):
        a.move(0, +1)  # 100 px down on a 756 px ball (radius 378 px)
    r, t = a.roll_tilt()
    assert t == pytest.approx(math.degrees(math.acos(1 - 100 / 378)), abs=0.01) and r == pytest.approx(0, abs=1e-6)
    assert a.dirty and "above" in a.texts()[0]
    a.set_above(False)
    assert a.roll_tilt()[1] == pytest.approx(-t)
    assert "below" in a.texts()[0]


def test_moving_sideways_creates_roll_and_the_marker_never_leaves_the_silhouette():
    a = axis()
    a.step = "coarse"
    a.move(0, +1)
    for _ in range(5):
        a.move(+1, 0)  # marker moves right: the axis leans right (negative roll in this convention)
    r, _t = a.roll_tilt()
    assert r < 0 and "leans right" in a.texts()[1]
    for _ in range(200):
        a.move(+1, -1)
    assert math.hypot(a.px, a.py) <= 1.0 + 1e-9


def test_numeric_fallback_sets_the_tilt_from_height_and_distance():
    a = axis()
    a.set_numbers(height_cm=35, distance_m=2.4)  # the design board's example values
    assert a.roll_tilt()[1] == pytest.approx(math.degrees(math.atan(0.35 / 2.4)), abs=1e-6)
    assert a.roll_tilt()[1] == pytest.approx(8.3, abs=0.05)
    a.set_numbers(distance_m=1.2)  # closer: steeper
    assert a.roll_tilt()[1] > 8.3
    b = axis()
    b.step = "coarse"
    b.move(+1, +1)  # some roll first
    r0 = b.roll_tilt()[0]
    b.set_numbers(height_cm=50, distance_m=3)
    assert b.roll_tilt()[0] == pytest.approx(r0, abs=1e-6)  # the fallback changes the tilt, not the roll


def test_crosshair_position_for_the_renderer_overlay():
    cal = Calibration(0.3, 0.7, 0.1, 0.9, roll_deg=0, tilt_deg=30)
    a = AxisSession(cal, W, H)
    x, y = a.crosshair_norm()
    assert x == pytest.approx(0.5) and y == pytest.approx(0.5 - 0.4 * math.cos(math.radians(30)))
    assert a.calibration().tilt_deg == pytest.approx(30, abs=1e-6)


def test_axis_keys():
    assert axis_key_to_action("Left") == ("cross", (-1, 0)) and axis_key_to_action("2") == ("step", "normal")
    assert axis_key_to_action("Return") == ("next", None) and axis_key_to_action("Escape") == ("cancel", None)
    assert axis_key_to_action("BackSpace") == ("back", None) and axis_key_to_action("a") is None


def test_rows_nudging_and_readout_match_the_design_board():
    r = RowsSession(pitch=180 / 47.3)
    assert r.text() == "rows ≈ 47.3 · tile 3.8°"  # the design's example
    r.apply("pitch", +1)
    assert r.pitch == pytest.approx(180 / 47.3 + 0.02) and r.dirty
    r.apply("step", "coarse")
    r.apply("offset", +1)
    assert r.offset == pytest.approx(1.0)  # 1 degree is inside one row (+-1.9), so no wrap yet
    for _ in range(50):
        r.apply("offset", +1)
    assert -r.pitch / 2 <= r.offset < r.pitch / 2


def test_rows_pitch_is_clamped_and_pattern_toggles():
    r = RowsSession(pitch=1.02, step="coarse")
    for _ in range(5):
        r.apply("pitch", -1)
    assert r.pitch == 1.0
    r.pitch = 11.95
    for _ in range(5):
        r.apply("pitch", +1)
    assert r.pitch == 12.0
    assert r.mode == "zebra"
    r.apply("pattern", None)
    assert r.mode == "single" and r.preview()["mode"] == "single"


def test_rows_offset_only_matters_modulo_a_row():
    r = RowsSession(pitch=4.0, offset=5.0)  # 5 = one row + 1 degree
    assert r.offset == pytest.approx(1.0)
    r = RowsSession(pitch=4.0, offset=-3.0)
    assert r.offset == pytest.approx(1.0)


def test_rows_keys():
    assert rows_key_to_action("Up") == ("offset", 1) and rows_key_to_action("Left") == ("pitch", -1)
    assert rows_key_to_action("z") == ("pattern", None) and rows_key_to_action("Return") == ("next", None)
    assert rows_key_to_action("x") is None


def test_the_confirmation_step_only_navigates():
    assert done_key_to_action("Return") == ("finish", None) and done_key_to_action("BackSpace") == ("back", None)
    assert done_key_to_action("Escape") == ("cancel", None) and done_key_to_action("Up") is None
