import math
from pathlib import Path

import pytest

from mirrorballs.common.scene import SceneError, load_library, load_scene, load_scene_text, make_env

ROOT = Path(__file__).resolve().parent.parent
SCENES = ROOT / "scenes"


def env(**kw):
    beat = kw.pop("beat", 0.0)
    base = dict(beat=beat, phase=beat % 1, beats_per_bar=4, scene_beat=beat, time=beat / 2, bpm=120.0)
    base.update(kw)
    return make_env(**base)


def scene(body: str, **kw):
    return load_scene_text("schema: 1\nname: t\n" + body, "t", ROOT, **kw)


def test_all_bundled_scenes_load_and_evaluate():
    scenes, errors = load_library(SCENES)
    assert not errors, {k: v.problems for k, v in errors.items()}
    assert set(scenes) == {"bars-to-poles", "kick-spot", "ripples", "sweep-and-sparkle", "row-stepper"}
    for sc in scenes.values():
        for b in (0.0, 0.3, 1.0, 7.77, 100.5):
            shapes = sc.evaluate(env(beat=b, params=sc.default_params()))
            assert shapes, sc.id
            for s in shapes:
                assert 0.0 <= s.inner <= s.outer <= math.pi + 1e-9
                assert math.sqrt(sum(x * x for x in s.center)) == pytest.approx(1)
                assert 0.0 <= s.alpha <= 1.0
        assert not sc.runtime_errors(), (sc.id, sc.runtime_errors())


def test_bars_scene_geometry():
    sc = load_scene(SCENES / "bars-to-poles.yaml")
    assert sc.asset_order and sc.asset_order[0].endswith("weave.jpg")
    shapes = sc.evaluate(env(beat=0.5, params=sc.default_params()))  # phase 0.5
    assert len(shapes) == 2
    north, south = shapes
    lat = 90 * (1 - 0.25)  # ease_out(0.5)
    width = 8 * (1 - 0.25)
    # north band spans lat +-width/2 about 67.5; annulus about the north pole in colatitude
    assert math.degrees(north.inner) == pytest.approx(90 - (lat + width / 2))
    assert math.degrees(north.outer) == pytest.approx(90 - (lat - width / 2))
    assert math.degrees(south.inner) == pytest.approx(90 + (lat - width / 2))
    assert north.texture == 0 and north.tex_scale == (2.0, 1.0)
    assert north.color == pytest.approx((0.5, 0.5, 0.0))  # mix(red, green, 0.5)
    assert north.softness == 0.0  # snap: rows -> hard edges


def test_snap_rows_quantises_band_edges_when_calibrated():
    sc = scene("layers:\n  - {type: band, lat: 30, width: 10, snap: rows}\n")
    free = sc.evaluate(env())[0]
    assert math.degrees(free.inner) == pytest.approx(55.0)  # no calibration: no snapping
    snapped = sc.evaluate(env(tile=4.0, row_offset=0.0))[0]
    assert math.degrees(snapped.inner) == pytest.approx(90 - 36.0)  # 35 -> nearest edge 36
    assert math.degrees(snapped.outer) == pytest.approx(90 - 24.0)  # 25 -> 24


def test_shape_types_reduce_to_annuli():
    sc = scene(
        "layers:\n"
        "  - {type: meridian, lon: 0, width: 10}\n"
        "  - {type: circle, at: {lat: 0, lon: 0}, radius: 20}\n"
        "  - {type: ring, at: {lat: 0, lon: 0}, radius: 20, width: 4}\n"
        "  - {type: annulus, at: {lat: 90, lon: 0}, inner: 10, outer: 30}\n"
        "  - {type: fill}\n"
        "  - {type: band, lat_from: -10, lat_to: 40}\n"
    )
    mer, circ, ring, ann, fill, band = sc.evaluate(env())
    assert mer.center == pytest.approx((1, 0, 0), abs=1e-9)  # equatorial point 90 deg east of lon 0
    assert (math.degrees(mer.inner), math.degrees(mer.outer)) == pytest.approx((85, 95))
    assert circ.center == pytest.approx((0, 0, 1)) and math.degrees(circ.outer) == pytest.approx(20)
    assert (math.degrees(ring.inner), math.degrees(ring.outer)) == pytest.approx((18, 22))
    assert ann.center == pytest.approx((0, 1, 0), abs=1e-9)
    assert (fill.inner, fill.outer) == (0.0, pytest.approx(math.pi))
    assert (math.degrees(band.inner), math.degrees(band.outer)) == pytest.approx((50, 100))


def test_enabled_and_repeat_and_spin():
    sc = scene(
        "spin: {axis: y, deg: 'fract(bar) * 360'}\n"
        "layers:\n"
        "  - {type: circle, at: {lat: 0, lon: 'i * 10'}, radius: 5, repeat: 3, enabled: 'i != 1'}\n"
    )
    shapes = sc.evaluate(env())
    assert len(shapes) == 2
    axis, rad = sc.spin(env(beat=2.0))  # bar = 0.5
    assert axis == "y" and rad == pytest.approx(math.pi)


def test_default_softness_and_colours():
    sc = scene(
        "layers:\n"
        "  - {type: fill, color: '#ff8000'}\n"
        "  - {type: fill, color: [0.1, 0.2, 0.3]}\n"
        "  - {type: fill, color: cyan}\n"
        "  - {type: fill, color: 'hsv(0, 1, 1)', softness: 0}\n"
    )
    a, b, c, d = sc.evaluate(env())
    assert a.color == pytest.approx((1.0, 128 / 255, 0.0))
    assert b.color == (0.1, 0.2, 0.3)
    assert c.color == (0.0, 1.0, 1.0)
    assert d.color == (1.0, 0.0, 0.0)
    assert math.degrees(a.softness) == pytest.approx(1.0) and d.softness == 0.0


def test_params_are_available_to_expressions():
    sc = scene(
        "params: {w: {type: float, default: 6}}\n"
        "layers:\n  - {type: band, lat: 0, width: 'param.w * 2'}\n"
    )
    s = sc.evaluate(env(params={"w": 10}))[0]
    assert math.degrees(s.outer - s.inner) == pytest.approx(20)
    assert sc.default_params() == {"w": 6}


def test_error_reporting_collects_all_problems_with_paths():
    with pytest.raises(SceneError) as ei:
        scene(
            "layers:\n"
            "  - {type: band, lat: 'fase', width: 3}\n"
            "  - {type: meridian, lon: 0}\n"
            "  - {type: circle, at: {lat: 0, lon: 0}, radius: 'sin(('}\n"
            "  - {type: fill, texture: {asset: nope}}\n"
        )
    text = "\n".join(ei.value.problems)
    assert "layers[0].lat: unknown name 'fase' (did you mean 'phase'?)" in text
    assert "layers[1]: 'meridian' needs 'width'" in text
    assert "layers[2].radius: syntax error" in text
    assert "layers[3].texture.asset: 'nope' is not declared" in text
    assert len(ei.value.problems) == 4  # every expression/semantic problem is reported at once


def test_structural_errors_are_reported_before_expressions_are_compiled():
    with pytest.raises(SceneError) as ei:
        scene("layers:\n  - {type: fill, colour: red}\n  - {type: nonsense}\n")
    text = "\n".join(ei.value.problems)
    assert "layers[0].colour: unknown key" in text
    assert "layers[1].type" in text


def test_structural_errors():
    with pytest.raises(SceneError, match="schema"):
        load_scene_text("schema: 2\nname: t\nlayers: []\n", "t", ROOT)
    with pytest.raises(SceneError, match="mapping"):
        load_scene_text("- 1\n- 2\n", "t", ROOT)
    with pytest.raises(SceneError, match="YAML syntax"):
        load_scene_text("schema: 1\n  bad: [\n", "t", ROOT)
    with pytest.raises(SceneError, match="maximum of 64"):
        scene("layers:\n  - {type: fill, repeat: 40}\n  - {type: fill, repeat: 30}\n")
    with pytest.raises(SceneError, match="only applies to bands"):
        scene("layers:\n  - {type: fill, snap: rows}\n")
    with pytest.raises(SceneError, match="asset"):
        scene("assets: {a: missing.jpg}\nlayers:\n  - {type: fill}\n")


def test_library_keeps_good_scenes_when_one_is_broken(tmp_path):
    (tmp_path / "good.yaml").write_text("schema: 1\nname: g\nlayers:\n  - {type: fill}\n")
    (tmp_path / "bad.yaml").write_text("schema: 1\nname: b\nlayers:\n  - {type: nonsense}\n")
    scenes, errors = load_library(tmp_path, tmp_path / "does-not-exist")
    assert set(scenes) == {"good"} and set(errors) == {"bad"}
    # later directories override earlier ones by id
    (tmp_path / "over").mkdir()
    (tmp_path / "over" / "good.yaml").write_text("schema: 1\nname: override\nlayers:\n  - {type: fill}\n")
    scenes, _ = load_library(tmp_path, tmp_path / "over")
    assert scenes["good"].name == "override"


def test_yaml_is_not_yaml_1_1_gotchas():
    # 'on'/'no' must stay strings, not booleans (why we use ruamel's YAML 1.2 loader)
    sc = scene("description: no\nlayers:\n  - {type: fill}\n")
    assert sc.description == "no"


def test_colour_and_bool_params_reach_expressions():
    sc = scene(
        "params:\n  tint: {type: color, default: '#ff8000'}\n  on: {type: bool, default: true}\n"
        "layers:\n  - {type: fill, color: 'mix(param.tint, white, 0.5)', alpha: 'param.on'}\n"
    )
    s = sc.evaluate(env(params=sc.default_params()))[0]
    assert s.color == pytest.approx((1.0, 0.5 + 128 / 255 / 2, 0.5))
    assert s.alpha == 1.0
    s2 = sc.evaluate(env(params={"tint": [0.0, 0.0, 1.0], "on": False}))[0]
    assert s2.color == pytest.approx((0.5, 0.5, 1.0)) and s2.alpha == 0.0


def test_the_scene_spin_is_outside_any_repeat(tmp_path):
    """i and n belong to a layer's repeat; the spin is evaluated once per frame, so it cannot use them.
    Rejecting it at load time is what keeps that from being a NameError while the show runs."""
    with pytest.raises(SceneError) as e:
        load_scene_text("schema: 1\nname: x\nspin: {axis: y, deg: 'i * 10'}\nlayers:\n  - {type: fill}\n", "x", tmp_path)
    assert "spin.deg: unknown name 'i'" in str(e.value)
    sc = load_scene_text("schema: 1\nname: x\nspin: {axis: y, deg: 'fract(bar) * 360'}\nlayers:\n  - {type: fill}\n", "x", tmp_path)
    assert sc.spin(env(beat=2.0))[0] == "y"


def test_scenes_read_the_accent_colour_and_say_so():
    sc = scene("layers:\n  - {type: fill, color: accent}\n  - {type: band, lat: 0, width: 10, color: 'mix(accent, white, phase)'}\n")
    assert sc.uses_accent
    shapes = sc.evaluate(env(accent=(1.0, 0.5, 0.0)))
    assert shapes[0].color == pytest.approx((1.0, 0.5, 0.0)) and shapes[1].color == pytest.approx((1.0, 0.5, 0.0))
    assert sc.evaluate(env())[0].color == (1.0, 1.0, 1.0)  # white when nobody set one
    assert not scene("layers:\n  - {type: fill, color: orange}\n").uses_accent
    plain = scene("params:\n  accent: {type: color, default: '#ff0000'}\nlayers:\n  - {type: fill, color: param.accent}\n")
    assert not plain.uses_accent  # a parameter that happens to be called accent is not the accent
