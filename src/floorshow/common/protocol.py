"""Controller <-> renderer messages. Requests carry an optional ``id`` and get ``{"id", "ok"}`` or
``{"id", "ok": false, "error"}`` back. Events (``"event": ...``) are unsolicited, renderer -> client."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .config import Every, TransitionConfig

PROTO = 1
# Bumped whenever the controller needs something new from the renderer (a command, a state field). A renderer
# that reports a lower "rev" (or none) was started before the controller was updated, and the controller says so.
REV = 9


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int | str | None = None


class Hello(_Cmd):
    cmd: Literal["hello"]
    proto: int = PROTO


class GetState(_Cmd):
    cmd: Literal["get_state"]


class SceneSet(_Cmd):
    cmd: Literal["scene.set"]
    scene: str
    transition: TransitionConfig | None = None


class RunningSet(_Cmd):
    cmd: Literal["running.set"]
    running: bool


class ParamSet(_Cmd):
    cmd: Literal["param.set"]
    name: str
    value: float | int | bool | str | list[float]


class ModeSet(_Cmd):
    cmd: Literal["mode.set"]
    mode: Literal["manual", "random", "sequence"]
    every: Every | None = None


class BpmSet(_Cmd):
    cmd: Literal["bpm.set"]
    bpm: float = Field(ge=20, le=300)  # internal clock only; a MIDI clock owns the tempo


class MasterSet(_Cmd):
    cmd: Literal["master.set"]
    value: float = Field(ge=0, le=1)


class TrailSet(_Cmd):
    cmd: Literal["trail.set"]
    seconds: float = Field(ge=0, le=3)  # how long what disappears keeps glowing; 0 = off


class AccentSet(_Cmd):
    cmd: Literal["accent.set"]
    color: tuple[float, float, float]  # linear 0..1, what scenes read as ``accent``

    @field_validator("color")
    @classmethod
    def _in_range(cls, v: tuple[float, float, float]) -> tuple[float, float, float]:
        if not all(0.0 <= c <= 1.0 for c in v):
            raise ValueError("colour components must be between 0 and 1")
        return v


class CalibrationBegin(_Cmd):
    cmd: Literal["calibration.begin"]


class RowsPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "equator"] = "all"  # every row in alternating colours, or the equator's row alone
    pitch_deg: float = Field(ge=1.0, le=12.0)
    offset_deg: float = Field(ge=-12.0, le=12.0)


class CalibrationPreview(_Cmd):
    cmd: Literal["calibration.preview"]
    left: float
    right: float
    top: float
    bottom: float
    roll_deg: float = 0.0
    tilt_deg: float = 0.0
    blink: bool = False
    grid: bool = False
    active: Literal["left", "right", "top", "bottom"] | None = None  # last-moved line, drawn brighter
    lines: bool = True  # the four silhouette lines (step 1)
    crosshair: tuple[float, float] | None = None  # step 2: where the pole marker is drawn (window fractions, top-down)
    rows: RowsPreview | None = None  # step 3: the tile-row stripes
    spin: bool = False  # step 4: turn the grid about the ball's axis, so the ball looks like it is spinning


class CalibrationEnd(_Cmd):
    cmd: Literal["calibration.end"]
    commit: bool = False


class TestPatternSet(_Cmd):
    cmd: Literal["testpattern.set"]
    on: bool


class SimulationSet(_Cmd):
    cmd: Literal["simulation.set"]
    on: bool  # the output window shows the haze simulation instead of the projector image


class MidiLearn(_Cmd):
    cmd: Literal["midi.learn"]
    on: bool  # the next note / CC / program change on the control port is reported as a "midi_learned" event
    role: Literal["control", "clock"] = "control"  # "clock": the next note on the clock port (beat-notes mode)


class AudioPreview(_Cmd):
    cmd: Literal["audio.preview"]
    # While any client has this on, every audio source is metered (the state's "audio.sources" carry levels) and the
    # chosen audio source is tracked even when another clock drives the show (its tempo shows before switching to it).
    # A client that disconnects is off.
    on: bool


class MidiPreviewSet(_Cmd):
    cmd: Literal["midi.preview"]
    # While any client has this on, every MIDI input port is opened and the state's "midi_ports" say which ones carry
    # a beat (clock ticks or evenly repeating notes) and at what tempo. A client that disconnects is off.
    on: bool


class Simple(_Cmd):
    cmd: Literal["reload", "quit", "tap", "sync_downbeat", "scene.next", "scene.prev"]


Command = Annotated[
    Hello | GetState | SceneSet | RunningSet | ParamSet | ModeSet | BpmSet | MasterSet
    | CalibrationBegin | CalibrationPreview | CalibrationEnd | TestPatternSet | MidiLearn | TrailSet | AccentSet
    | AudioPreview | MidiPreviewSet | SimulationSet | Simple,
    Field(discriminator="cmd"),
]
_adapter: TypeAdapter[Any] = TypeAdapter(Command)


def parse_command(msg: dict[str, Any]) -> _Cmd:
    """Validate an incoming message. Raises ValueError with a readable message."""
    try:
        return _adapter.validate_python(msg)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"][1:]) or "message"
        raise ValueError(f"{where}: {'unknown key' if first['type'] == 'extra_forbidden' else first['msg']}") from None


def ok(req_id: Any = None, **data: Any) -> dict[str, Any]:
    return {"id": req_id, "ok": True, **data}


def error(req_id: Any, message: str) -> dict[str, Any]:
    return {"id": req_id, "ok": False, "error": message}


def event(name: str, **data: Any) -> dict[str, Any]:
    return {"event": name, **data}
