"""config.yaml: the persistent settings both processes share. The controller writes it, the renderer
reads (and hot-reloads) it. Missing file or missing keys fall back to defaults."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .geometry import Calibration

SCHEMA_VERSION = 1


class ConfigError(ValueError):
    pass


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClockConfig(_Base):
    source: Literal["midi", "internal"] = "internal"
    midi_port: str | None = None  # substring match; ALSA client numbers change between runs
    # How the MIDI source announces the beat. "clock": standard MIDI clock (24 per beat, Start/Stop).
    # "beats": one note per beat, as Mixxx's "MIDI for light" script sends. The defaults below are that script's.
    midi_mode: Literal["clock", "beats"] = "clock"
    beat_note: int = Field(50, ge=0, le=127)  # D
    beat_velocity: int = Field(100, ge=0, le=127)  # only this velocity is a beat; 0 = any
    midi_channel: int = Field(0, ge=0, le=16)  # 0 = any
    tempo_note: int = Field(52, ge=-1, le=127)  # E: its value announces the tempo (display only); -1 = none
    tempo_offset: int = Field(50, ge=-127, le=300)  # tempo = value + offset
    bpm: float = Field(120.0, ge=20, le=300)
    beats_per_bar: int = Field(4, ge=1, le=16)
    offset_ms: float = Field(0.0, ge=-500, le=500)


class BallConfig(_Base):
    diameter_cm: float | None = None
    tile_mm: float | None = Field(None, gt=0)  # starting value for calibration (with diameter_cm)
    row_pitch_deg: float | None = Field(None, gt=0)  # None: rows uncalibrated, tile = 4 deg, no snapping
    row_offset_deg: float = 0.0
    # Signed: + turns to the right as seen facing the ball, 0 = not turning. Nothing reads this yet; it is
    # what the later rotation feature needs to know about the rig, asked while the rest of it is measured.
    rotation_rpm: float = Field(0.0, ge=-60, le=60)
    motor: dict[str, Any] | None = None  # reserved


class CalibrationConfig(_Base):
    left: float
    right: float
    top: float
    bottom: float
    roll_deg: float = 0.0
    tilt_deg: float = 0.0
    calibrated_at: str | None = None  # ISO timestamp, shown in the configuration dialog

    def to_calibration(self) -> Calibration:
        return Calibration(self.left, self.right, self.top, self.bottom, self.roll_deg, self.tilt_deg)

    @staticmethod
    def from_calibration(c: Calibration) -> CalibrationConfig:
        return CalibrationConfig(left=c.left, right=c.right, top=c.top, bottom=c.bottom, roll_deg=c.roll_deg, tilt_deg=c.tilt_deg)


class Every(_Base):
    bars: int | None = Field(None, ge=1)
    beats: int | None = Field(None, ge=1)

    def in_beats(self, beats_per_bar: int) -> int:
        return self.beats if self.beats is not None else (self.bars or 8) * beats_per_bar


TRANSITION_TYPES = ("crossfade", "cut", "wipe_lat", "iris", "flash")  # what "random" picks from


class TransitionConfig(_Base):
    type: Literal["crossfade", "cut", "wipe_lat", "iris", "flash", "random"] = "crossfade"
    beats: float = Field(1.0, ge=0)


class DirectorConfig(_Base):
    mode: Literal["manual", "random", "sequence"] = "manual"  # the mode the renderer starts in
    every: Every = Every(bars=8)
    transition: TransitionConfig = TransitionConfig()
    # Scene pools, no-repeat and hand-written sequences were removed; old files that still have
    # these keys keep loading. Random never repeats the current scene, Sequence walks all scenes.
    no_repeat: Any | None = None
    pool: Any | None = None
    sequence: Any | None = None
    loop: Any | None = None


class MidiTrigger(_Base):
    note: int | None = Field(None, ge=0, le=127)
    program: int | None = Field(None, ge=0, le=127)
    cc: int | None = Field(None, ge=0, le=127)
    ch: int | None = Field(None, ge=1, le=16)  # None = any channel


class MidiMapping(_Base):
    on: MidiTrigger
    do: str | dict[str, str | int]  # {scene: id} | {scene_index: n} | "master" | "blackout" | "next"


class MidiConfig(_Base):
    control_port: str | None = None
    map: list[MidiMapping] = []


class Bindings(_Base):
    keys: dict[str, str] = {}  # controller-side keyboard: key -> scene id or action


class TrailConfig(_Base):
    # Turning light on and off on a mirror ball is a big movement; letting what disappears fade out
    # over a fraction of a second makes it read as smooth without softening the hit on the beat.
    seconds: float = Field(0.0, ge=0, le=3)  # 0 = off


class UiConfig(_Base):
    beat_indicator: bool = True  # the beat tile and bar pips in the controller


class Config(_Base):
    schema_version: int = Field(SCHEMA_VERSION, alias="schema")
    clock: ClockConfig = ClockConfig()
    ball: BallConfig = BallConfig()
    calibration: CalibrationConfig | None = None  # None: computed default
    director: DirectorConfig = DirectorConfig()
    midi: MidiConfig = MidiConfig()
    bindings: Bindings = Bindings()
    trail: TrailConfig = TrailConfig()
    safety: dict | None = None  # the flash limiter was removed; old files that still have this section keep loading
    ui: UiConfig = UiConfig()


def _yaml() -> YAML:
    y = YAML()  # round-trip: keeps the user's comments when the controller rewrites the file
    y.default_flow_style = False
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_config(path: Path) -> Config:
    """Load ``path``; a missing file gives the defaults. Raises ConfigError for a broken file."""
    path = Path(path)
    if not path.exists():
        return Config()
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: top level must be a mapping")
        return Config.model_validate(data)
    except YAMLError as e:
        raise ConfigError(f"{path}: YAML syntax error: {e}".replace("\n", " ")) from None
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {'unknown key' if err['type'] == 'extra_forbidden' else err['msg']}"
            for err in e.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None


def update_config(path: Path, updates: dict[str, Any]) -> None:
    """Merge ``updates`` (JSON-like, top-level sections) into the file and write it atomically.
    Comments and untouched keys survive. The result is validated before anything is written."""
    path = Path(path)
    y = _yaml()
    data = y.load(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None:
        data = {"schema": SCHEMA_VERSION}
    _merge(data, updates)
    Config.model_validate(_plain(data))  # raises ValidationError before we touch the file
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            y.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _merge(dst: Any, src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        elif v is None and k in dst:
            dst[k] = None
        else:
            dst[k] = v


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    return x
