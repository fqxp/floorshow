"""IPC server + dispatcher + Core over a real unix socket, and MidiManager over a virtual ALSA port."""

import json
import threading
import time
from queue import SimpleQueue

import pytest

from mirrorballs.ctl import __main__ as ctl_main
from mirrorballs.ctl.client import NotRunning, SyncClient
from mirrorballs.render.core import Core
from mirrorballs.render.dispatch import pump
from mirrorballs.render.ipc_server import AlreadyRunning, IpcServer

SCENE = "schema: 1\nname: {n}\nparams:\n  w: {{type: float, default: 5, min: 1, max: 10}}\nlayers:\n  - {{type: fill}}\n"


class Rig:
    """Runs the parts of the renderer's main loop that need no GL, on a thread."""

    def __init__(self, tmp_path):
        scenes = tmp_path / "scenes"
        scenes.mkdir()
        for n in "abc":
            (scenes / f"{n}.yaml").write_text(SCENE.format(n=n))
        self.sock = tmp_path / "run" / "render.sock"
        self.inbox = SimpleQueue()
        self.ipc = IpcServer(self.sock, self.inbox)
        self.ipc.start()
        self.core = Core(tmp_path / "config.yaml", [scenes], now=time.monotonic())
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self._stop.is_set():
            now = time.monotonic()
            pump(self.core, self.inbox, self.ipc, now)
            self.core.frame(now)
            self.ipc.broadcast(self.core.state(now))
            time.sleep(0.005)

    def close(self):
        self._stop.set()
        self.thread.join(2)
        self.ipc.stop()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def test_commands_over_the_socket(rig):
    with SyncClient(rig.sock) as c:
        r = c.request("hello")
        assert r["ok"] and r["state"]["scene"] == "a"
        assert c.request("scene.set", scene="b", transition={"type": "cut", "beats": 0})["ok"]
        assert c.request("get_state")["state"]["scene"] == "b"
        assert c.request("param.set", name="w", value=99)["ok"]
        assert c.request("get_state")["state"]["params"] == {"w": 10.0}
        bad = c.request("scene.set", scene="zzz")
        assert not bad["ok"] and "unknown scene" in bad["error"]
        bad = c.request("scene.set")
        assert not bad["ok"] and "scene" in bad["error"]
        bad = c.request("nope")
        assert not bad["ok"]
        assert c.request("running.set", running=False)["ok"]
        assert c.request("master.set", value=0.5)["ok"]
        st = c.request("get_state")["state"]
        assert st["running"] is False and st["master"] == 0.5


def test_malformed_input_gets_an_error_and_the_connection_survives(rig):
    with SyncClient(rig.sock) as c:
        c.sock.sendall(b"this is not json\n")
        msg = c.read()
        while "event" in msg:  # skip state broadcasts
            msg = c.read()
        assert msg["ok"] is False and "invalid JSON" in msg["error"]
        assert c.request("get_state")["ok"]


def test_state_is_streamed_to_every_client(rig):
    with SyncClient(rig.sock) as a, SyncClient(rig.sock) as b:
        for c in (a, b):
            for _ in range(200):
                m = c.read()
                if m.get("event") == "state":
                    assert "clock" in m and 0 <= m["clock"]["phase"] < 1
                    break
            else:
                pytest.fail("no state event")


def test_calibration_is_released_when_the_client_disconnects(rig):
    c = SyncClient(rig.sock)
    assert c.request("calibration.begin")["ok"]
    assert rig.core.state(time.monotonic())["calibrating"]
    with SyncClient(rig.sock) as other:
        assert "already in progress" in other.request("calibration.begin")["error"]
    c.close()  # a crashed controller
    for _ in range(100):
        if not rig.core.state(time.monotonic())["calibrating"]:
            break
        time.sleep(0.02)
    assert not rig.core.state(time.monotonic())["calibrating"]


def test_second_renderer_is_refused_and_stale_socket_is_reclaimed(rig, tmp_path):
    with pytest.raises(AlreadyRunning):
        IpcServer(rig.sock, SimpleQueue()).start()
    stale = tmp_path / "stale.sock"
    import socket

    s = socket.socket(socket.AF_UNIX)
    s.bind(str(stale))
    s.close()  # a socket file nobody listens on
    srv = IpcServer(stale, SimpleQueue())
    srv.start()
    try:
        with SyncClient(stale, timeout=1):
            pass
    finally:
        srv.stop()
    with pytest.raises(NotRunning):
        SyncClient(tmp_path / "nothing.sock")


def test_socket_is_private(rig):
    assert (rig.sock.stat().st_mode & 0o777) == 0o600


def test_ctl_cli(rig, monkeypatch, capsys):
    monkeypatch.setenv("MIRRORBALLS_SOCKET", str(rig.sock))
    assert ctl_main.main(["scene", "c", "--transition", "cut"]) == 0
    assert ctl_main.main(["scenes"]) == 0
    out = capsys.readouterr().out
    assert "* c" in out and "  a" in out
    assert ctl_main.main(["mode", "random", "--bars", "2"]) == 0
    assert ctl_main.main(["param", "w", "7"]) == 0
    assert ctl_main.main(["scene", "nope"]) == 1
    assert ctl_main.main(["state"]) == 0
    assert rig.core.mode == "random" and rig.core.every_beats == 8 and rig.core.params["c"]["w"] == 7.0
    assert ctl_main.main(["quit"]) == 0
    time.sleep(0.05)
    assert rig.core.quit_requested


def test_ctl_reports_a_missing_renderer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MIRRORBALLS_SOCKET", str(tmp_path / "none.sock"))
    assert ctl_main.main(["state"]) == 2
    assert "no renderer" in capsys.readouterr().err


# --- MIDI over a virtual ALSA port -------------------------------------------------------


def test_midi_manager_receives_clock_and_reconnects():
    rtmidi = pytest.importorskip("rtmidi")
    from mirrorballs.render.midi import MidiManager

    try:
        out = rtmidi.MidiOut()
        out.open_virtual_port("mbtest virtual clock")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no ALSA sequencer: {e}")
    inbox = SimpleQueue()
    mgr = MidiManager(inbox)
    try:
        mgr.configure("MBTEST VIRTUAL", None)  # case-insensitive substring
        assert mgr.status()["clock"]["open"] is None
        mgr.poll(0.0)
        assert mgr.status()["clock"]["open"] == next(p for p in mgr.list_ports() if "mbtest virtual" in p)
        t_before = time.monotonic()
        for msg in ([0xFA], [0xF8], [0xF8], [0x90, 60, 100]):
            out.send_message(msg)
        got = []
        deadline = time.monotonic() + 2
        while len(got) < 4 and time.monotonic() < deadline:
            try:
                got.append(inbox.get(timeout=0.1))
            except Exception:
                pass
        assert [g[2][0] for g in got] == [0xFA, 0xF8, 0xF8, 0x90]
        assert all(g[1] == {"clock"} and g[3] >= t_before for g in got)
        mgr.configure(None, None)  # no longer wanted: closed
        assert mgr.status()["clock"] == {"wanted": None, "open": None}
        mgr.configure("no such device", "no such device")
        mgr.poll(10.0)
        assert mgr.status()["clock"]["open"] is None and mgr.status()["control"]["open"] is None
    finally:
        mgr.close()
        out.close_port()


def test_output_window_is_not_minimised_when_it_loses_focus():
    """Regression: GLFW minimises a fullscreen window on focus loss by default, which made the projector image
    vanish whenever another window was clicked."""
    from mirrorballs.render.app import configure_output_window

    class FakeGlfw:
        AUTO_ICONIFY, CURSOR, CURSOR_HIDDEN, FALSE = 0x20006, 0x33001, 0x34002, 0

        def __init__(self):
            self.calls = []

        def set_window_attrib(self, *a):
            self.calls.append(("attrib", *a))

        def set_input_mode(self, *a):
            self.calls.append(("input", *a))

    g = FakeGlfw()
    configure_output_window(g, "win")
    assert ("attrib", "win", g.AUTO_ICONIFY, g.FALSE) in g.calls
    assert ("input", "win", g.CURSOR, g.CURSOR_HIDDEN) in g.calls
