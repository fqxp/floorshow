import pytest

from mirrorballs.control import tokens, viewmodel as vm
from mirrorballs.control.viewmodel import (
    LENGTHS, banners, beat_in_bar, clock_text, display_name, hotkeys, nearest_length, renderer_chip,
    scene_for_key, transition_payload,
)


def clock(**kw):
    base = dict(source="midi", port="Arturia BeatStep", bpm=124.0, beat=6.5, phase=0.5, bar=1.6, beats_per_bar=4,
                locked=True, status="locked")
    base.update(kw)
    return base


def state(**kw):
    base = dict(rev=vm.REV, clock=clock(), scene="a", scenes=["a"], running=True, calibrating=False, fps=59.6, scene_errors={})
    base.update(kw)
    return base


def test_renderer_chip_texts_match_the_design():
    assert renderer_chip(True, False, state()).text == "Renderer: running · 60 fps"
    assert renderer_chip(False, False, None).text == "Not running" and renderer_chip(False, False, None).kind == "off"
    assert renderer_chip(False, True, None).text == "Connecting…" and renderer_chip(False, True, None).kind == "busy"
    assert renderer_chip(True, True, state()).kind == "ok"  # connected wins over "starting"


def test_clock_text_variants_match_the_design():
    assert clock_text(clock()) == ("MIDI · 124.0 BPM · Arturia BeatStep", False)
    assert clock_text(clock(source="internal", bpm=120.0)) == ("Internal · 120 BPM", False)
    assert clock_text(clock(locked=False, bpm=118.2)) == ("MIDI · waiting for clock (holding 118 BPM)", True)
    assert clock_text(None) == ("Internal · 120 BPM", False)
    assert clock_text(None, "internal", None, 128) == ("Internal · 128 BPM", False)
    assert "no port" in clock_text(None, "midi", None)[0]


def test_beat_in_bar_extrapolates_and_wraps():
    assert beat_in_bar(clock(beat=6.5, bpm=120)) == (2, pytest.approx(0.5))
    b, p = beat_in_bar(clock(beat=6.5, bpm=120), elapsed_s=0.5)  # +1 beat
    assert (b, p) == (3, pytest.approx(0.5))
    assert beat_in_bar(clock(beat=7.9, bpm=120), elapsed_s=0.1)[0] == 0  # wraps into the next bar


def test_banners():
    assert banners(False, None) == []
    assert banners(True, state()) == []
    b = banners(True, state(clock=clock(locked=False, bpm=118)))
    assert [(x.kind, x.action) for x in b] == [("midi_lost", "Use internal clock")]
    assert b[0].text == "MIDI clock lost. Holding the last tempo (118 BPM)."
    b = banners(True, state(scene_errors={"strobe-grid": ["layers[1].widht: unknown key"]}))
    assert b[0].kind == "scene_error" and b[0].scene_id == "strobe-grid" and b[0].text.startswith("strobe-grid.yaml · ")
    both = banners(True, state(calibrating=True, clock=clock(locked=False)))
    assert [x.kind for x in both] == ["calibrating", "midi_lost"]
    b = banners(True, state(config_error="/x/config.yaml: clock.bpm: Input should be greater than or equal to 20"))
    assert b[0].kind == "config_error" and "last good settings" in b[0].text and b[0].action == "Open in editor"
    b = banners(True, state(output=dict(presenting=False, stalled_s=4.0)))
    assert b[0].kind == "output_hidden" and b[0].action is None and "hidden or covered" in b[0].text
    assert banners(True, state(output=dict(presenting=True, stalled_s=0.0))) == []
    # an internal clock is never "lost"
    assert banners(True, state(clock=clock(source="internal", locked=True))) == []


def test_hotkeys_digits_in_order_and_explicit_bindings():
    ids = [f"s{i}" for i in range(10)]
    hk = hotkeys(ids)
    assert hk["s0"] == "1" and hk["s7"] == "8" and "s8" not in hk
    hk = hotkeys(["a", "b", "c"], {"3": "a", "space": "next", "x": "b"})
    assert hk == {"a": "3", "b": "1", "c": "2"}  # explicit digit kept, others fill the free digits
    assert scene_for_key("3", ["a", "b", "c"], {"3": "a"}) == "a"
    assert scene_for_key("9", ["a"]) is None


def test_transition_payload_and_lengths():
    assert transition_payload("crossfade", 1, 4) == {"type": "crossfade", "beats": 4}
    assert transition_payload("iris", 0.25, 4)["beats"] == 1
    assert LENGTHS[nearest_length(2, 4)][0] == "½ bar"
    assert LENGTHS[nearest_length(100, 4)][0] == "2 bars"


def test_display_name():
    assert display_name("bars-to-poles") == "Bars To Poles"


def test_tokens_and_css():
    for dark in (True, False):
        p = tokens.palette(dark)
        css = tokens.css(dark)
        assert f"@define-color acc {p['acc']};" in css
        # every colour the stylesheet uses is defined
        import re
        used = set(re.findall(r"@([a-z_0-9]+)", tokens.STYLE_FILE.read_text())) - {"define"}
        assert used <= set(p), used - set(p)
    assert tokens.palette(True)["beat"] == "#ff3b30"
    assert tokens.mix("#000000", "#ffffff", 0.5) == pytest.approx((0.5, 0.5, 0.5))
    assert tokens.beat_intensity(0.0) == 1.0 and tokens.beat_intensity(1.0) == 0.0
    assert tokens.beat_intensity(0.5) < 0.5 and tokens.beat_intensity(1.7) == 0.0


def test_key_to_action_matches_the_shortcut_sheet():
    from mirrorballs.control.viewmodel import key_to_action as k

    assert k("space") == ("toggle-run", None) and k("t") == ("tap", None) and k("d") == ("downbeat", None) and k("B") == ("blackout", None)
    assert k("m") == ("mode", "manual") and k("r") == ("mode", "random")
    assert k("bracketleft") == ("step", -1) and k("bracketright") == ("step", 1)
    assert k("3") == ("scene-key", "3") and k("9") is None and k("0") is None
    assert k("Up", ctrl=True) == ("brightness", 5) and k("Down", ctrl=True) == ("brightness", -5)
    assert k("comma", ctrl=True) == ("config", None) and k("question", ctrl=True) == ("shortcuts", None)
    assert k("space", ctrl=True) is None and k("x") is None  # no accidental triggers


def test_change_progress_shrinks_from_one_to_zero():
    clock = dict(source="internal", bpm=120.0, beat=0.0, beats_per_bar=4)
    state = dict(clock=clock, next_change_beat=32.0, change_interval_beats=32.0)
    assert vm.change_progress(state) == 1.0
    assert vm.change_progress(state, elapsed_s=8.0) == pytest.approx(0.5)  # 16 of 32 beats
    assert vm.change_progress(state, elapsed_s=16.0) == 0.0
    assert vm.change_progress(state, elapsed_s=99.0) == 0.0  # never negative
    assert vm.change_progress(dict(clock=clock, next_change_beat=None, change_interval_beats=None)) is None
    assert vm.change_progress(dict(clock=clock)) is None and vm.change_progress(None) is None


def test_outdated_renderer_banner():
    clock = dict(source="internal", bpm=120.0, beat=0.0, beats_per_bar=4, locked=True)
    old = dict(clock=clock)  # a renderer from before "rev" existed
    assert [b.kind for b in vm.banners(True, old)] == ["renderer_outdated"]
    assert vm.banners(True, dict(clock=clock, rev=vm.REV)) == []
    assert vm.banners(True, dict(clock=clock, rev=vm.REV + 1)) == []


def test_midi_input_status_says_what_is_wrong():
    def mk(mode="clock", locked=True, wanted="mixxx", open_="Mixxx 20:0", source="midi"):
        return dict(clock=clock(source=source, locked=locked, midi_mode=mode), midi={"clock": {"wanted": wanted, "open": open_}})

    assert vm.midi_input_status(mk(source="internal")) == ("Not used (internal clock)", "")
    assert vm.midi_input_status(mk(wanted=None, open_=None)) == ("No port selected", "warn")
    assert vm.midi_input_status(mk(open_=None)) == ("Port not found", "warn")
    assert vm.midi_input_status(mk(mode="beats", locked=False)) == ("Waiting for beat notes", "warn")
    assert vm.midi_input_status(mk(locked=False)) == ("Waiting for clock", "warn")
    assert vm.midi_input_status(mk()) == ("Receiving · 124.0 BPM", "ok")
    assert vm.midi_input_status(dict(clock=clock(), midi={}))[1] == "ok"  # nothing reported: no port complaint
    text, warn = vm.clock_text(clock(locked=False, midi_mode="beats"), midi={"clock": {"wanted": "x", "open": "x"}})
    assert warn and text.startswith("MIDI · waiting for beat notes")
    assert vm.clock_text(clock(), midi={"clock": {"wanted": None, "open": None}}) == ("MIDI · no port selected", True)
