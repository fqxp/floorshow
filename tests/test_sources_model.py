import pytest

from floorshow.control import sources_model as sm
from floorshow.control import viewmodel as vm


def audio(source="out", locked=True, out_db=-12.0, mic_db=-80.0, error=None, running=True, wanted="out"):
    return {
        "running": running, "error": error, "source": source, "wanted": wanted, "throttled": False, "previewing": True,
        "sources": [{"id": "out", "name": "Monitor of Speakers", "kind": "monitor", "channels": 2,
                     "rms_db": [out_db, out_db - 1], "peak_db": [out_db + 6, out_db + 5]},
                    {"id": "mic", "name": "Built-in mic", "kind": "mic", "channels": 2,
                     "rms_db": [mic_db, mic_db], "peak_db": [mic_db, mic_db]}],
        "tracking": {"bpm": 124.3, "confidence": 0.7, "locked": locked, "anchor_time": 10.0, "anchor_beat": 3.0},
        "health": None,
    }


def test_audio_chips_show_levels_and_the_chosen_ones_tempo():
    items = sm.audio_items(audio())
    assert [i.id for i in items] == ["out", "mic"]
    assert items[0].status == "124 BPM" and items[0].levels == (-12.0, -13.0) and not items[0].dim
    assert items[1].status == "silent" and items[1].dim
    assert sm.audio_items(audio(locked=False))[0].status == "no beat"
    assert sm.audio_items(None) == []


def test_midi_chips_show_the_beat_of_each_port():
    ports = [{"name": "RtMidiOut Client:floorshow test clock 128:0", "status": "clock", "bpm": 128.0, "beat_time": 5.0,
              "note": None},
             {"name": "Mixxx 1", "status": "beats", "bpm": 100.0, "beat_time": 6.0, "note": 50},
             {"name": "Pads", "status": "active", "bpm": None, "beat_time": None, "note": None},
             {"name": "VirMIDI 1-0 (raw hw:1,0)", "status": "busy", "bpm": None, "beat_time": None, "note": None}]
    items = sm.midi_items(ports)
    assert [(i.name, i.status) for i in items] == [("floorshow test clock", "128 BPM"), ("Mixxx 1", "100 BPM"),
                                                    ("Pads", "no beat"), ("VirMIDI 1-0 (raw hw:1,0)", "busy")]
    assert items[0].id == ports[0]["name"] and items[3].dim
    assert sm.selected_midi(ports, "mixxx") == "Mixxx 1" and sm.selected_midi(ports, "gone") == "gone"


def test_the_hover_says_whether_the_chosen_source_has_a_beat():
    assert sm.hover_text(audio(), "out") == ("Monitor of Speakers · 124.3 BPM", audio()["tracking"])
    assert sm.hover_text(audio(locked=False), "out") == ("Monitor of Speakers · no beat yet", None)
    assert sm.hover_text(audio(out_db=-90), "out")[0] == "Monitor of Speakers · silent"
    assert sm.hover_text(audio(source=None, wanted="hdmi"), "hdmi")[0] == "Audio · hdmi is not there"
    assert sm.hover_text(audio(error="no GStreamer", running=False), "out")[0].startswith("Audio is not available")
    assert sm.hover_text(audio(running=False), "out", cfg_name="Monitor of Speakers")[0] == \
        "Audio · starting to listen to Monitor of Speakers…"
    assert "not running" in sm.hover_text(None, "out")[0]


def test_under_another_clock_the_hover_only_names_the_source():
    idle = {"running": False, "error": None, "source": None, "sources": [], "tracking": None}
    assert sm.hover_text(idle, "out", active=False, cfg_name="Monitor of Speakers") == \
        ("Audio · Monitor of Speakers · listened to while the audio clock runs", None)
    assert sm.hover_text(idle, "out", active=False)[0] == "Audio · out · listened to while the audio clock runs"
    assert "no source chosen" in sm.hover_text(idle, None, active=False)[0]


def test_switching_to_audio_says_what_it_listens_to():
    assert sm.audio_switch_toast("out", "Monitor of Speakers", 120) == \
        "Audio clock · listening to Monitor of Speakers (holding 120 BPM until the beat is found)"
    assert sm.audio_switch_toast(None, None, 120) == "Audio clock · choose a source beside Downbeat"
    # a few seconds later, whatever source it listens to
    assert sm.no_beat_toast(audio(locked=False), None) == "No beat found on Monitor of Speakers so far"
    assert sm.no_beat_toast(audio(out_db=-90, locked=False), None) == "Monitor of Speakers is silent"
    assert sm.no_beat_toast(audio(), None) is None


def test_picking_toasts():
    items = sm.audio_items(audio())
    assert sm.audio_pick_toast(items[1], "Built-in mic") == "Audio · Built-in mic is silent"
    assert sm.audio_pick_toast(items[0], "Monitor of Speakers") == "Audio · Monitor of Speakers"
    assert sm.no_beat_toast(audio(locked=False), "out") == "No beat found on Monitor of Speakers so far"
    assert sm.no_beat_toast(audio(), "out") is None
    assert sm.no_beat_toast(audio(), "mic") is None  # another source was picked meanwhile
    m = sm.midi_items([{"name": "Mixxx 1", "status": "beats", "bpm": 100.0, "beat_time": 1.0, "note": 50}])[0]
    assert sm.midi_pick_toast(m) == "MIDI · Mixxx 1 · 100 BPM"


def test_preview_beat_phases():
    assert sm.beat_phase(120, 10.0, 10.25) == pytest.approx(0.5)
    assert sm.beat_phase(None, 10.0, 10.25) is None
    assert sm.tracking_phase(audio()["tracking"], 10.0 + 60 / 124.3 * 0.25) == pytest.approx(0.25)
    assert sm.tracking_phase(audio(locked=False)["tracking"], 10.0) is None


def test_levels_and_tempo_do_not_change_the_stable_view():
    assert sm.stable_audio(audio(out_db=-12)) == sm.stable_audio(audio(out_db=-30))
    assert sm.stable_audio(audio(locked=True)) != sm.stable_audio(audio(locked=False))


def test_the_audio_clock_chip_and_banner():
    clock = {"source": "audio", "bpm": 124.3, "locked": True, "beat": 1.0, "beats_per_bar": 4, "status": "locked"}
    assert vm.clock_text(clock, audio=audio()) == ("Audio · 124.3 BPM · Monitor of Speakers", False)
    assert vm.clock_text(dict(clock, locked=False), audio=audio()) == ("Audio · listening for a beat (holding 124 BPM)", True)
    assert vm.clock_text(clock, audio=audio(source=None, wanted="hdmi")) == ("Audio · hdmi not found", True)
    assert vm.clock_text(None, "audio", cfg_audio="out") == ("Audio · out · not running", False)
    state = {"clock": clock, "audio": audio(error="no GStreamer", running=False), "rev": vm.REV}
    assert [b.kind for b in vm.banners(True, state)] == ["audio_unavailable"]
    assert vm.banners(True, {"clock": dict(clock, locked=False), "audio": audio(), "rev": vm.REV}) == []
