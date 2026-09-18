import math

import pytest

from mirrorballs.common.expr import ExprError, compile_expr

ENV = {
    "beat": 37.25, "phase": 0.25, "bar": 9.3125, "scene_beat": 2.0, "time": 12.0,
    "bpm": 120.0, "trans": 1.0, "tile": 4.0, "row_offset": None, "i": 1.0, "n": 2.0,
    "param.width": 8.0,
}


def ev(src, **kw):
    kw.setdefault("variables", [*ENV, ])
    kw.setdefault("params", ["width"])
    return compile_expr(src, **kw)(ENV)


def test_arithmetic_and_variables():
    assert ev("(1 - 2*i) * 90 * ease_out(phase)") == pytest.approx(-90 * (1 - 0.75**2))
    assert ev("param.width * (1 - 0.5*phase)") == pytest.approx(7.0)
    assert ev("26 if phase < 5/17 else 7") == 26.0
    assert ev("phase < 0.1 or beat > 30") in (True, 1.0)  # bool result normalised below


def test_numbers_and_bools_become_floats():
    assert ev(5) == 5.0 and isinstance(ev(5), float)
    assert isinstance(ev("1 < 2"), float)


def test_functions():
    assert ev("fract(beat)") == pytest.approx(0.25)
    assert ev("clamp(5, 0, 1)") == 1.0
    assert ev("smoothstep(0.7, 1.0, 0.85)") == pytest.approx(0.5)
    assert ev("tri(0.5)") == 1.0 and ev("tri(0)") == 0.0
    assert ev("pulse(phase, 0.5)") == 1.0
    assert ev("floor(beat) % 8") == 5.0


def test_colours():
    assert ev("mix(red, green, 0.5)") == (0.5, 0.5, 0.0)
    assert ev('hex("#ff8000")') == pytest.approx((1.0, 128 / 255, 0.0))
    assert ev("hsv(0, 1, 1)") == (1.0, 0.0, 0.0)
    assert ev("rgb(0.1, 0.2, 0.3)") == (0.1, 0.2, 0.3)


def test_rand_deterministic_and_in_range():
    xs = [ev(f"rand({k})") for k in range(200)]
    assert xs == [ev(f"rand({k})") for k in range(200)]
    assert all(0.0 <= x < 1.0 for x in xs)
    assert len(set(xs)) > 190
    assert 0.0 <= ev("noise(3.3)") < 1.0
    assert ev("noise(4)") == ev("rand(4)")  # continuous at integers


def test_row_snapping_identity_when_uncalibrated():
    assert ev("snap_row(13.7)") == 13.7
    env = {**ENV, "row_offset": 1.0}
    e = compile_expr("snap_row(13.7)", variables=list(ENV))
    assert e(env) == pytest.approx(1.0 + (math.floor(12.7 / 4) + 0.5) * 4)  # centre of the row, 13.0
    e2 = compile_expr("snap_edge(13.7)", variables=list(ENV))
    assert e2(env) == pytest.approx(13.0)


@pytest.mark.parametrize("src", [
    "__import__('os').system('true')",
    "().__class__",
    "beat.real",
    "param.width.real",
    "[1, 2][0]",
    "lambda: 1",
    "[x for x in (1,)]",
    "(x := 1)",
    "sin(x=1)",
    "sin(*[1])",
    "beat if 1",
    "open('/etc/passwd')",
    "exec('1')",
    "sin.__call__(1)",
    "'abc'",
    "not_a_name + 1",
    "1 if 2 in 3 else 4",
    "1 << 3",
    "~beat",
    "1 @ 2",
    "f'{beat}'",
    "await x",
    "",
    "1 +",
])
def test_rejected(src):
    with pytest.raises(ExprError):
        compile_expr(src, variables=list(ENV), params=["width"])


def test_error_messages_help():
    with pytest.raises(ExprError, match="did you mean 'phase'"):
        compile_expr("fase * 2", variables=list(ENV))
    with pytest.raises(ExprError, match="did you mean 'smoothstep'"):
        compile_expr("smothstep(0, 1, phase)", variables=list(ENV))
    with pytest.raises(ExprError, match="did you mean 'width'"):
        compile_expr("param.widht", variables=list(ENV), params=["width"])
    with pytest.raises(ExprError, match="is a function"):
        compile_expr("sin", variables=list(ENV))
    with pytest.raises(ExprError, match="hex"):
        compile_expr('hex("nope")', variables=list(ENV))


def test_size_limits():
    with pytest.raises(ExprError, match="too long"):
        compile_expr("1+" * 300 + "1")
    with pytest.raises(ExprError, match="too complex"):
        compile_expr("b+" * 100 + "b", variables=["b"])


@pytest.mark.parametrize("src", ["1/0", "sqrt(-1)", "10**400", "(-8)**0.5", "pow(-8, 0.5)", "mix(red, 1, 0.5)"])
def test_runtime_errors_never_raise(src):
    e = compile_expr(src, variables=list(ENV))
    assert e(ENV) == 0.0
    assert e.last_error


def test_pow_bomb_is_harmless():
    e = compile_expr("9 ** 9 ** 9 ** 9", variables=list(ENV))
    assert e(ENV) == 0.0
