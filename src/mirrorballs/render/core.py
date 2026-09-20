"""Renderer brain: clock, director (scene selection and transitions), scene library, config
hot-reload, command handling and calibration mode. No GL, no GLFW, no sockets, no MIDI devices,
so all of it is unit-testable with fake time. ``app.py`` wires it to those.

Time is always passed in (``now``, seconds on a monotonic clock).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common import protocol as P
from ..common.clock import Clock
from ..common.config import TRANSITION_TYPES, CalibrationConfig, Config, ConfigError, load_config
from ..common.geometry import Calibration
from ..common.scene import Scene, SceneError, load_library, make_env
from .frame import FrameSpec, SlotSpec
from .gl import Overlay

log = logging.getLogger("mirrorballs.core")

FILE_POLL_S = 0.5
FADE_S = 0.4  # blackout fade
OUTPUT_STALL_S = 1.5  # no frame presented for this long: the window is hidden or covered
DEFAULT_TILE_DEG = 4.0
EASE = {
    "linear": lambda u: u,
    "in": lambda u: u * u,
    "out": lambda u: 1 - (1 - u) * (1 - u),
    "in_out": lambda u: u * u * (3 - 2 * u),
}


@dataclass
class _Transition:
    type: str
    duration_s: float
    ease: str
    start: float


class Core:
    def __init__(
        self,
        config_path: Path,
        scene_dirs: list[Path],
        *,
        now: float,
        rng: random.Random | None = None,
    ):
        self.config_path = Path(config_path)
        self.scene_dirs = [Path(d) for d in scene_dirs]
        self.t0 = now
        self.rng = rng or random.Random()
        self.fps = 0.0
        self.quit_requested = False
        self.midi_status: dict[str, Any] = {}  # filled by the app: which ports are open
        self.window: dict[str, Any] | None = None  # filled by the app: size / fullscreen / monitor of the output window
        self.frame_heartbeat: float | None = None  # set by the render thread after every presented frame
        self.test_pattern = False
        self.learn = False  # MIDI learn: report the next control message instead of acting on it
        self.outbox: list[dict[str, Any]] = []  # events for all clients, drained by the control loop

        self.config = Config()
        self.config_error: str | None = None
        self.scenes: dict[str, Scene] = {}
        self.scene_errors: dict[str, SceneError] = {}
        self.clock = Clock(now=now)
        self._applied_clock: Any = None
        self._file_sig: tuple = ()
        self._next_poll = now

        # runtime state (lives here, so a restarted controller can just ask for it)
        self.current: str | None = None
        self.previous: str | None = None
        self._scene_start: dict[str, float] = {}  # scene id -> beat at which it became active
        self._transition: _Transition | None = None
        self.params: dict[str, dict[str, Any]] = {}
        self.running = True
        self.master = 1.0
        self._level = 1.0  # blackout fade level, animated toward 1 (running) or 0 (stopped)
        self._level_time = now
        self.mode = "manual"
        self.every_beats = 32
        self._last_slot: int | None = None
        self._seq_index = 0
        self._seq_next: float | None = None  # beat at which the sequence advances; None = start on the next tick
        self._seq_start = 0.0

        # calibration mode
        self.cal_owner: Any = None
        self._cal_preview: dict[str, Any] | None = None
        self._cal_before: Calibration | None = None
        self.midi_dirty = True

        self.reload(now, first=True)

    # --- files ---------------------------------------------------------------------------

    def _signature(self) -> tuple:
        sig = []
        paths = [self.config_path]
        for d in self.scene_dirs:
            if d.is_dir():
                paths += sorted(d.glob("*.yaml"))
        for p in paths:
            try:
                sig.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                sig.append((str(p), None))
        return tuple(sig)

    def poll_files(self, now: float) -> bool:
        """Reload if any config or scene file changed. Returns True if a reload happened."""
        if now < self._next_poll:
            return False
        self._next_poll = now + FILE_POLL_S
        if self._signature() == self._file_sig:
            return False
        self.reload(now)
        return True

    def reload(self, now: float, first: bool = False) -> None:
        self._file_sig = self._signature()
        try:
            self.config = load_config(self.config_path)
            self.config_error = None
        except ConfigError as e:  # keep running with the previous config
            self.config_error = str(e)
            log.warning("config: %s", e)

        old = self.scenes
        scenes, errors = load_library(*self.scene_dirs)
        for sid, err in errors.items():
            log.warning("scene %s: %s", sid, err)
            if sid in old:  # a scene that breaks while running keeps its last good version
                scenes[sid] = old[sid]
        self.scenes, self.scene_errors = scenes, errors
        for sid, sc in scenes.items():
            self.params.setdefault(sid, sc.default_params())
        for sid in [k for k in self.params if k not in scenes]:
            del self.params[sid]

        if self.current not in self.scenes:
            self.previous, self._transition = None, None
            self.current = self._first_scene()
            if self.current:
                self._scene_start[self.current] = self.clock.snapshot(now).beat
        if first:
            self.mode = self.config.director.mode
            self.every_beats = self.config.director.every.in_beats(self.config.clock.beats_per_bar)
            self._last_slot = None
        self._apply_clock_config(now)
        self.midi_dirty = True

    def _first_scene(self) -> str | None:
        return sorted(self.scenes)[0] if self.scenes else None

    def _apply_clock_config(self, now: float) -> None:
        c = self.config.clock
        prev = self._applied_clock
        self.clock.set_beats_per_bar(c.beats_per_bar)
        self.clock.set_offset_ms(c.offset_ms)
        if (prev is None or prev.source != c.source or (c.source == "internal" and prev.bpm != c.bpm)
                or (c.source == "midi" and prev.midi_mode != c.midi_mode)):
            if c.source == "midi":
                self.clock.use_midi(now, beats=c.midi_mode == "beats")
            else:
                self.clock.use_internal(c.bpm, now)
        self._applied_clock = c

    def output_state(self, now: float) -> dict[str, Any]:
        """Is the output window actually being presented? The render loop blocks in swap_buffers while GNOME
        hides or covers the window, so a stale heartbeat means the projector is not showing our frames."""
        if self.frame_heartbeat is None:
            return {"presenting": True, "stalled_s": 0.0}
        stalled = max(0.0, now - self.frame_heartbeat)
        return {"presenting": stalled < OUTPUT_STALL_S, "stalled_s": round(stalled, 1)}

    def take_outbox(self) -> list[dict[str, Any]]:
        out, self.outbox = self.outbox, []
        return out

    def midi_wants(self) -> tuple[str | None, str | None]:
        """(clock port substring or None, control port substring or None) the app should have open."""
        clock = self.config.clock.midi_port if self.config.clock.source == "midi" else None
        return clock, self.config.midi.control_port

    # --- scene switching -----------------------------------------------------------------

    def scene_ids(self) -> list[str]:
        return sorted(self.scenes)

    def set_scene(self, sid: str, now: float, transition: tuple[str, float, str] | None = None) -> None:
        if sid not in self.scenes:
            raise KeyError(f"unknown scene '{sid}'")
        if sid == self.current:
            return
        snap = self.clock.snapshot(now)
        incoming = self.scenes[sid]
        if transition is None:
            if incoming.transition_in is not None:
                t = incoming.transition_in
                transition = (t.type, t.beats, t.ease)
            else:
                t = self.config.director.transition
                transition = (t.type, t.beats, "in_out")
        ttype, beats, ease = transition
        if ttype == "random":  # a fresh pick on every scene change
            ttype = self.rng.choice(TRANSITION_TYPES)
        self.previous = self.current
        self.current = sid
        self._scene_start[sid] = snap.beat
        duration = beats * 60.0 / max(snap.bpm, 1.0)
        self._transition = (
            None if ttype == "cut" or duration <= 0 or self.previous is None
            else _Transition(ttype, duration, ease, now)
        )
        if self._transition is None:
            self.previous = None

    def _sequence(self) -> list:
        return [i for i in self.config.director.sequence if i.scene in self.scenes]

    def _sequence_step(self, snap, now: float) -> None:
        """Play the configured sequence: each item for its own number of bars, optionally looping."""
        items = self._sequence()
        bpb = snap.beats_per_bar
        if self._seq_next is None or self._seq_index >= len(items):
            self._seq_index = 0
            self.set_scene(items[0].scene, now)
            self._seq_start, self._seq_next = snap.beat, snap.beat + items[0].bars * bpb
            return
        if snap.beat < self._seq_start:  # the beat counter jumped back (MIDI Start): restart the current item
            self._seq_start, self._seq_next = snap.beat, snap.beat + items[self._seq_index].bars * bpb
            return
        if snap.beat < self._seq_next:
            return
        last = self._seq_index == len(items) - 1
        if last and not self.config.director.loop:
            self._seq_next = float("inf")  # stay on the last scene
            return
        self._seq_index = (self._seq_index + 1) % len(items)
        item = items[self._seq_index]
        self.set_scene(item.scene, now)
        self._seq_start, self._seq_next = snap.beat, snap.beat + item.bars * bpb

    def _step(self, delta: int, now: float) -> None:
        ids = self.scene_ids()
        if ids:
            i = ids.index(self.current) if self.current in ids else -1
            self.set_scene(ids[(i + delta) % len(ids)], now)

    def _pool(self) -> list[str]:
        pool = self.config.director.pool
        ids = self.scene_ids()
        if pool.ids or pool.tags:
            ids = [i for i in ids if i in pool.ids or set(self.scenes[i].tags) & set(pool.tags)]
        return ids

    def _director_step(self, snap, now: float) -> None:
        if self.mode == "manual" or self.cal_owner is not None:
            return
        if self.mode == "sequence" and self._sequence():
            self._sequence_step(snap, now)
            return
        slot = int(snap.beat // self.every_beats)
        if self._last_slot is None:
            self._last_slot = slot
            return
        if slot == self._last_slot:
            return
        self._last_slot = slot
        pool = self._pool()
        if not pool:
            return
        if self.mode == "sequence":
            nxt = next((p for p in pool if p > (self.current or "")), pool[0])
        else:
            choices = [p for p in pool if p != self.current] if self.config.director.no_repeat and len(pool) > 1 else pool
            nxt = self.rng.choice(choices)
        self.set_scene(nxt, now)

    # --- commands ------------------------------------------------------------------------

    def client_disconnected(self, client: Any, now: float) -> None:
        if client == self.cal_owner:
            self._end_calibration(commit=False)

    def handle(self, cmd: P._Cmd, client: Any, now: float) -> dict[str, Any]:
        """Apply a validated command; returns the reply for the sender."""
        rid = cmd.id
        try:
            return self._handle(cmd, client, now, rid)
        except KeyError as e:
            return P.error(rid, str(e.args[0]))
        except ValueError as e:
            return P.error(rid, str(e))

    def _handle(self, cmd: P._Cmd, client: Any, now: float, rid: Any) -> dict[str, Any]:
        if isinstance(cmd, P.Hello):
            if cmd.proto != P.PROTO:
                return P.error(rid, f"protocol {cmd.proto} not supported (renderer speaks {P.PROTO})")
            return P.ok(rid, proto=P.PROTO, state=self.state(now))
        if isinstance(cmd, P.GetState):
            return P.ok(rid, state=self.state(now))
        if isinstance(cmd, P.SceneSet):
            t = (cmd.transition.type, cmd.transition.beats, "in_out") if cmd.transition else None
            self.set_scene(cmd.scene, now, t)
        elif isinstance(cmd, P.RunningSet):
            self.running = cmd.running
            self._level_time = now
        elif isinstance(cmd, P.MasterSet):
            self.master = cmd.value
        elif isinstance(cmd, P.ParamSet):
            self._set_param(cmd.name, cmd.value)
        elif isinstance(cmd, P.ModeSet):
            self.mode = cmd.mode
            if cmd.every:
                self.every_beats = cmd.every.in_beats(self.clock.beats_per_bar)
            self._last_slot = None
            self._seq_next = None
        elif isinstance(cmd, P.BpmSet):
            if self.clock.source == "internal":
                self.clock.use_internal(cmd.bpm, now)  # keeps the beat continuous
        elif isinstance(cmd, P.TestPatternSet):
            self.test_pattern = cmd.on
        elif isinstance(cmd, P.MidiLearn):
            self.learn = cmd.on
        elif isinstance(cmd, P.CalibrationBegin):
            self._begin_calibration(client)
        elif isinstance(cmd, P.CalibrationPreview):
            self._require_owner(client)
            self._cal_preview = cmd.model_dump(exclude={"id", "cmd"})
        elif isinstance(cmd, P.CalibrationEnd):
            self._require_owner(client)
            self._end_calibration(commit=cmd.commit)
        elif isinstance(cmd, P.Simple):
            self._simple(cmd.cmd, now)
        return P.ok(rid)

    def _simple(self, name: str, now: float) -> None:
        if name == "reload":
            self.reload(now)
        elif name == "quit":
            self.quit_requested = True
        elif name == "tap":
            self.clock.tap(now)  # no effect on MIDI-driven clocks (ticks override)
        elif name == "sync_downbeat":
            self.clock.sync_downbeat(now)
        elif name == "scene.next":
            self._step(+1, now)
        elif name == "scene.prev":
            self._step(-1, now)

    def _set_param(self, name: str, value: Any) -> None:
        sc = self.scenes.get(self.current or "")
        if sc is None or name not in sc.params:
            raise KeyError(f"scene '{self.current}' has no parameter '{name}'")
        p = sc.params[name]
        if p.type in ("float", "int"):
            v = float(value)
            if p.min is not None:
                v = max(p.min, v)
            if p.max is not None:
                v = min(p.max, v)
            value = int(round(v)) if p.type == "int" else v
        elif p.type == "choice" and value not in (p.choices or []):
            raise ValueError(f"'{value}' is not one of {p.choices}")
        self.params[sc.id][name] = value

    # --- calibration mode ----------------------------------------------------------------

    def _begin_calibration(self, client: Any) -> None:
        if self.cal_owner is not None and self.cal_owner != client:
            raise ValueError("calibration is already in progress from another client")
        self.cal_owner = client
        self._cal_before = self.calibration(None)
        self._cal_preview = None

    def _require_owner(self, client: Any) -> None:
        if self.cal_owner is None or self.cal_owner != client:
            raise ValueError("not calibrating (send calibration.begin first)")

    def _end_calibration(self, commit: bool) -> None:
        if commit and self._cal_preview is not None:
            pv = self._cal_preview
            committed = Calibration(pv["left"], pv["right"], pv["top"], pv["bottom"], pv["roll_deg"], pv["tilt_deg"])
            if committed.is_valid():  # the controller persists it; until the file reloads we use it directly
                self.config = self.config.model_copy(update={"calibration": CalibrationConfig.from_calibration(committed)})
        self.cal_owner, self._cal_preview, self._cal_before = None, None, None

    def calibration(self, aspect: float | None) -> Calibration:
        """The committed calibration; the default (for ``aspect``, else 16:9) if never calibrated."""
        if self.config.calibration is not None:
            return self.config.calibration.to_calibration()
        return Calibration.default(aspect or 16 / 9)

    # --- MIDI ----------------------------------------------------------------------------

    def on_midi(self, roles: set[str], data: bytes, t: float) -> None:
        if not data:
            return
        status = data[0]
        c = self.config.clock
        if "clock" in roles and c.source == "midi" and c.midi_mode == "beats":
            if self._midi_beat_note(status, data, t):
                return
        elif "clock" in roles and c.source == "midi":
            if status == 0xF8:
                self.clock.on_midi_tick(t)
                return
            if status == 0xFA:
                self.clock.on_midi_start(t)
                return
            if status == 0xFB:
                self.clock.on_midi_continue(t)
                return
            if status == 0xFC:
                self.clock.on_midi_stop(t)
                return
            if status == 0xF2 and len(data) >= 3:
                self.clock.on_midi_spp(data[1] | (data[2] << 7), t)
                return
        if "control" in roles and 0x80 <= status < 0xF0:
            self._midi_control(status, data, t)

    def _midi_beat_note(self, status: int, data: bytes, t: float) -> bool:
        """Beat-notes mode: is this message the beat (or the announced tempo)? Returns True if it was used."""
        c = self.config.clock
        if status & 0xF0 != 0x90 or len(data) < 3 or data[2] == 0:  # note-on with a velocity only
            return False
        if c.midi_channel and (status & 0x0F) + 1 != c.midi_channel:
            return False
        if data[1] == c.beat_note and (not c.beat_velocity or data[2] == c.beat_velocity):
            self.clock.on_midi_beat(t)
            return True
        if c.tempo_note >= 0 and data[1] == c.tempo_note:
            self.clock.set_reported_bpm(data[2] + c.tempo_offset)
            return True
        return False

    def _midi_control(self, status: int, data: bytes, now: float) -> None:
        kind, ch = status & 0xF0, (status & 0x0F) + 1
        d1 = data[1] if len(data) > 1 else 0
        d2 = data[2] if len(data) > 2 else 0
        if kind == 0x90 and d2 == 0:  # note-on with velocity 0 is a note-off
            return
        if self.learn and kind in (0x90, 0xB0, 0xC0):
            key = {0x90: "note", 0xB0: "cc", 0xC0: "program"}[kind]
            self.outbox.append({"event": "midi_learned", "on": {key: d1, "ch": ch}})
            self.learn = False
            return
        for m in self.config.midi.map:
            trig = m.on
            if trig.ch is not None and trig.ch != ch:
                continue
            hit = (
                (kind == 0x90 and trig.note == d1)
                or (kind == 0xC0 and trig.program == d1)
                or (kind == 0xB0 and trig.cc == d1)
            )
            if hit:
                self._midi_action(m.do, d2 if kind == 0xB0 else None, now)

    def _midi_action(self, do: Any, value: int | None, now: float) -> None:
        if self.cal_owner is not None:
            return  # calibration owns the output
        try:
            if isinstance(do, dict):
                if "scene" in do:
                    self.set_scene(str(do["scene"]), now)
                elif "scene_index" in do:
                    ids = self.scene_ids()
                    self.set_scene(ids[int(do["scene_index"])], now)
            elif do == "next":
                self._step(+1, now)
            elif do == "blackout":
                self.running = not self.running
                self._level_time = now
            elif do == "master" and value is not None:
                self.master = value / 127.0
        except (KeyError, IndexError, ValueError) as e:
            log.warning("midi action %r failed: %s", do, e)

    # --- per tick / per frame ------------------------------------------------------------

    def tick(self, now: float) -> None:
        """Time-driven decisions (random / sequence switching). Called by the control thread, so
        the show keeps changing even if the display stalls; ``frame`` calls it too (idempotent)."""
        self._director_step(self.clock.snapshot(now), now)

    def _tile(self) -> tuple[float, float | None]:
        b = self.config.ball
        if b.row_pitch_deg is None:
            return DEFAULT_TILE_DEG, None
        return b.row_pitch_deg, b.row_offset_deg

    def _slot(self, sid: str, snap, now: float, trans: float) -> SlotSpec:
        sc = self.scenes[sid]
        tile, row_offset = self._tile()
        if snap.beat < self._scene_start.get(sid, snap.beat):  # beat counter jumped back (MIDI Start / SPP)
            self._scene_start[sid] = snap.beat
        env = make_env(
            beat=snap.beat, phase=snap.phase, beats_per_bar=sc.beats_per_bar or snap.beats_per_bar,
            scene_beat=snap.beat - self._scene_start.get(sid, snap.beat), time=now - self.t0, bpm=snap.bpm,
            trans=trans, tile=tile, row_offset=row_offset, params=self.params.get(sid),
        )
        return SlotSpec(sc.evaluate(env), sc.spin(env), sc.asset_order)

    def frame(self, now: float, aspect: float = 16 / 9) -> FrameSpec:
        snap = self.clock.snapshot(now)
        cal = self.calibration(aspect)

        if self.cal_owner is not None:
            pv = self._cal_preview
            if pv is None:
                cal_view = self._cal_before or cal
                lines = (cal_view.left, cal_view.right, cal_view.top, cal_view.bottom)
                overlay = Overlay(lines=lines, levels=(0.6,) * 4)
            else:
                cal_view = Calibration(pv["left"], pv["right"], pv["top"], pv["bottom"], pv["roll_deg"], pv["tilt_deg"])
                visible = 1.0 if not pv["blink"] or int(now * 2) % 2 == 0 else 0.0
                levels = tuple((1.0 if pv["active"] == side else 0.6) * visible for side in ("left", "right", "top", "bottom"))
                rows = pv.get("rows")
                overlay = Overlay(
                    lines=(cal_view.left, cal_view.right, cal_view.top, cal_view.bottom) if pv.get("lines", True) else None,
                    levels=levels, grid=pv["grid"],  # type: ignore[arg-type]
                    crosshair=tuple(pv["crosshair"]) if pv.get("crosshair") else None,
                    rows=(rows["mode"], rows["pitch_deg"], rows["offset_deg"]) if rows else None,
                )
            return FrameSpec(cal=cal_view, incoming=None, overlay=overlay)

        if self.test_pattern:
            return FrameSpec(cal=cal, incoming=None, black=True,
                             overlay=Overlay(lines=(0.004, 0.996, 0.004, 0.996), levels=(0.6,) * 4, grid=True))

        self._director_step(snap, now)

        # blackout fade (dt-based and clamped, so a long display stall just completes it)
        target = 1.0 if self.running else 0.0
        step = (now - self._level_time) / FADE_S
        self._level_time = now
        self._level = min(target, self._level + step) if target > self._level else max(target, self._level - step)
        master = self.master * self._level

        if self.current is None or self.current not in self.scenes:
            return FrameSpec(cal=cal, incoming=None, master=master, black=True)
        black = self._level <= 0.0 or master <= 0.0

        mix, ttype, outgoing = 1.0, "cut", None
        tr = self._transition
        if tr is not None:
            p = (now - tr.start) / tr.duration_s
            if p >= 1.0 or self.previous not in self.scenes:
                self._transition, self.previous = None, None
            else:
                mix, ttype = EASE[tr.ease](max(0.0, p)), tr.type
                outgoing = self._slot(self.previous, snap, now, 1.0)
        incoming = self._slot(self.current, snap, now, mix if tr is not None else 1.0)
        return FrameSpec(cal=cal, incoming=incoming, outgoing=outgoing, mix=mix, transition=ttype,
                         master=master, black=black)

    def next_change(self, snap) -> tuple[float, float] | tuple[None, None]:
        """(beat of the director's next scene change, length in beats of the period it ends), or (None, None)
        in manual mode or when it never will."""
        none = (None, None)
        if self.mode == "manual" or self.cal_owner is not None or not self.scenes:
            return none
        items = self._sequence()
        if self.mode == "sequence" and items:
            if self._seq_next is None or self._seq_index >= len(items):  # the sequence starts on the next tick
                length = items[0].bars * snap.beats_per_bar
                return snap.beat + length, float(length)
            if self._seq_next == float("inf"):
                return none
            return self._seq_next, float(items[self._seq_index].bars * snap.beats_per_bar)
        every = float(self.every_beats)
        return (int(snap.beat // every) + 1) * every, every

    # --- state for clients ---------------------------------------------------------------

    def state(self, now: float) -> dict[str, Any]:
        snap = self.clock.snapshot(now)
        cfg = self.config
        next_change = self.next_change(snap)
        return {
            "event": "state",
            "rev": P.REV,
            "clock": {
                "source": snap.source,
                "port": cfg.clock.midi_port if snap.source == "midi" else None,
                "bpm": round(snap.bpm, 3),
                "beat": round(snap.beat, 4),
                "phase": round(snap.phase, 4),
                "bar": round(snap.bar, 4),
                "beats_per_bar": snap.beats_per_bar,
                "locked": snap.locked,
                "status": snap.status,
            },
            "scene": self.current,
            "scenes": self.scene_ids(),
            "running": self.running,
            "mode": self.mode,
            "every_beats": self.every_beats,
            "next_change_beat": next_change[0],
            "change_interval_beats": next_change[1],
            "master": self.master,
            "params": self.params.get(self.current or "", {}),
            "calibrating": self.cal_owner is not None,
            "test_pattern": self.test_pattern,
            "learning": self.learn,
            "sequence_index": self._seq_index if self.mode == "sequence" else None,
            "window": self.window,
            "output": self.output_state(now),
            "fps": round(self.fps, 1),
            "scene_errors": {sid: e.problems for sid, e in self.scene_errors.items()},
            "config_error": self.config_error,
            "midi": self.midi_status,
        }
