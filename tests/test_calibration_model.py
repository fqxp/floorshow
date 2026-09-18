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
    ("Escape", ("cancel", None)), ("Return", ("finish", None)), ("KP_Enter", ("finish", None)),
])
def test_key_map_matches_the_shortcut_sheet(key, expected):
    assert key_to_action(key) == expected


def test_no_shortcut_for_reset_and_unknown_keys_are_ignored():
    assert key_to_action("r") is None and key_to_action("R") is None  # deliberately none: too easy to hit
    assert key_to_action("x") is None and key_to_action("space") is None


def test_fmt_px():
    assert fmt_px(960.0) == "960" and fmt_px(425.25) == "425.25" and fmt_px(0.5) == "0.5"
