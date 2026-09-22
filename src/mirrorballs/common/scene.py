"""Scene files: YAML -> validated model -> compiled scene that yields shader parameters per frame.

Angles in scene files are degrees; ``ShapeParams`` (what the renderer uploads) is in radians.
Every shape type reduces to the demo's single primitive, an *annulus*: a centre direction plus an
inner and outer angular radius. A latitude band is an annulus about the north pole, a meridian
band is an annulus of radius 90 degrees about an equatorial point, and so on.

Loading collects *all* problems in a file (with paths such as ``layers[1].radius``) into one
``SceneError``, so hand-written YAML gets useful feedback.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .expr import CLOCK_VARS, CONSTANTS, FUNCTIONS, Expr, ExprError, compile_expr, parse_hex_color

SCHEMA_VERSION = 1
MAX_SHAPES = 64
DEFAULT_SOFTNESS_DEG = 1.0

Vec3 = tuple[float, float, float]
Num = float | int | str  # a number, or an expression string


class SceneError(ValueError):
    """A scene file is invalid. ``problems`` holds one 'path: message' line per issue."""

    def __init__(self, file: str, problems: list[str]):
        self.file = file
        self.problems = problems
        super().__init__(f"{file}: " + "; ".join(problems))


# --- raw model (what the YAML may contain) -----------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamDef(_Base):
    type: Literal["float", "int", "bool", "color", "choice"] = "float"
    default: float | int | bool | str | list[float] = 0
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    label: str | None = None
    choices: list[str] | None = None


class TextureDef(_Base):
    asset: str
    mapping: Literal["shape", "latlon"] = "shape"
    scale: tuple[Num, Num] = (1, 1)
    scroll: Num = 0


class At(_Base):
    lat: Num = 0
    lon: Num = 0


class Layer(_Base):
    id: str | None = None
    type: Literal["band", "meridian", "circle", "ring", "annulus", "fill"]
    repeat: int = Field(1, ge=1, le=MAX_SHAPES)
    enabled: Num = 1
    # band
    lat: Num | None = None
    lat_from: Num | None = None
    lat_to: Num | None = None
    # meridian
    lon: Num | None = None
    # circle / ring / annulus
    at: At | None = None
    radius: Num | None = None
    inner: Num | None = None
    outer: Num | None = None
    width: Num | None = None
    # common
    color: Num | list[float] = "white"
    alpha: Num = 1
    blend: Literal["over", "add", "multiply", "screen"] = "over"
    softness: Num | None = None
    texture: TextureDef | None = None
    snap: Literal["rows"] | None = None
    frame: Literal["ball", "world"] = "ball"


class TransitionDef(_Base):
    type: Literal["crossfade", "cut", "wipe_lat", "iris", "flash", "random"] = "crossfade"
    beats: float = Field(1.0, ge=0)
    ease: Literal["linear", "in", "out", "in_out"] = "in_out"


class TransitionSpec(_Base):
    in_: TransitionDef | None = Field(None, alias="in")


class Spin(_Base):
    axis: Literal["x", "y", "z"] = "y"
    deg: Num = 0


class Tempo(_Base):
    beats_per_bar: int | None = Field(None, ge=1, le=16)  # None: use the clock's / config's value


class SceneFile(_Base):
    schema_version: int = Field(alias="schema")
    name: str
    description: str = ""
    tags: list[str] = []
    tempo: Tempo = Tempo()
    params: dict[str, ParamDef] = {}
    assets: dict[str, str] = {}
    transition: TransitionSpec = TransitionSpec()
    spin: Spin | None = None
    layers: list[Layer]


# --- compiled scene ----------------------------------------------------------------------


@dataclass(frozen=True)
class ShapeParams:
    """Everything the shader needs for one shape, in shader units (radians, linear colour)."""

    center: Vec3
    inner: float
    outer: float
    softness: float
    color: Vec3
    alpha: float
    blend: str
    texture: int  # index into Scene.asset_order, or -1
    tex_mapping: str  # "shape" | "latlon"
    tex_scale: tuple[float, float]
    tex_scroll: float
    world_frame: bool


@dataclass
class CompiledLayer:
    id: str | None
    type: str
    repeat: int
    enabled: Expr
    snap: bool
    world_frame: bool
    blend: str
    softness: Expr | None
    alpha: Expr
    color: Expr | tuple[float, float, float]
    fields: dict[str, Expr]  # lat, width, lon, at_lat, at_lon, radius, inner, outer, lat_from, lat_to
    tex_asset: int
    tex_mapping: str
    tex_scale: tuple[Expr, Expr] | None
    tex_scroll: Expr | None


@dataclass
class Scene:
    id: str
    name: str
    description: str
    tags: list[str]
    beats_per_bar: int | None
    params: dict[str, ParamDef]
    asset_order: list[str]  # texture files (absolute paths), index = ShapeParams.texture
    transition_in: TransitionDef | None
    spin_axis: str | None
    spin_deg: Expr | None
    layers: list[CompiledLayer]
    source: Path | None = None
    errors: list[str] = field(default_factory=list)  # runtime expression errors seen (for the UI)

    def default_params(self) -> dict[str, Any]:
        return {name: p.default for name, p in self.params.items()}

    def spin(self, env: Mapping[str, Any]) -> tuple[str, float] | None:
        """(axis, radians) for the whole-scene spin, or None."""
        if self.spin_deg is None or self.spin_axis is None:
            return None
        return self.spin_axis, math.radians(_num(self.spin_deg(env)))

    def evaluate(self, env: Mapping[str, Any]) -> list[ShapeParams]:
        """Shape parameters for the current frame. ``env`` holds the clock variables, the
        ``param.<name>`` values, ``tile`` and ``row_offset`` (see :func:`make_env`)."""
        out: list[ShapeParams] = []
        for layer in self.layers:
            for k in range(layer.repeat):
                e = {**env, "i": float(k), "n": float(layer.repeat)}
                sp = _evaluate_layer(layer, e)
                if sp is not None:
                    out.append(sp)
        return out[:MAX_SHAPES]

    def runtime_errors(self) -> list[str]:
        errs: list[str] = []
        for layer in self.layers:
            for ex in _layer_exprs(layer):
                if ex.last_error:
                    errs.append(ex.last_error)
        return errs


def make_env(
    *,
    beat: float,
    phase: float,
    beats_per_bar: int,
    scene_beat: float,
    time: float,
    bpm: float,
    trans: float = 1.0,
    tile: float = 4.0,
    row_offset: float | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Variable environment for :meth:`Scene.evaluate`. ``beats_per_bar`` is the scene's own value
    if it declares one, else the clock's."""
    env: dict[str, Any] = dict(
        beat=beat, phase=phase, bar=beat / beats_per_bar, scene_beat=scene_beat, time=time, bpm=bpm, trans=trans,
        tile=tile, row_offset=row_offset,
    )
    for name, value in (params or {}).items():
        env[f"param.{name}"] = _param_value(value)
    return env


def _param_value(value: Any) -> Any:
    """Scene parameter as an expression value: numbers/bools become floats, ``[r,g,b]`` lists and
    ``"#rrggbb"`` strings become colour tuples (usable in ``mix(param.tint, white, phase)``)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    if isinstance(value, str) and value.startswith("#"):
        try:
            return parse_hex_color(value)
        except ValueError:
            return 0.0
    return value


# --- evaluation --------------------------------------------------------------------------

_NORTH: Vec3 = (0.0, 1.0, 0.0)


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _latlon_dir(lat_deg: float, lon_deg: float) -> Vec3:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    return (math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon))


def _snap_edge(lat: float, env: Mapping[str, Any]) -> float:
    return FUNCTIONS["snap_edge"](lat, env["tile"], env["row_offset"])


def _color(c: Any) -> Vec3:
    if isinstance(c, tuple) and len(c) == 3:
        return (float(c[0]), float(c[1]), float(c[2]))
    g = _num(c)
    return (g, g, g)


def _layer_exprs(layer: CompiledLayer) -> list[Expr]:
    exprs = [layer.enabled, layer.alpha, *layer.fields.values()]
    for opt in (layer.softness, layer.tex_scroll):
        if opt is not None:
            exprs.append(opt)
    if layer.tex_scale:
        exprs.extend(layer.tex_scale)
    if isinstance(layer.color, Expr):
        exprs.append(layer.color)
    return exprs


def _evaluate_layer(layer: CompiledLayer, env: Mapping[str, Any]) -> ShapeParams | None:
    if not _num(layer.enabled(env)):
        return None
    f = {k: _num(ex(env)) for k, ex in layer.fields.items()}
    t = layer.type

    if t == "band":
        if "lat_from" in f:
            lo, hi = sorted((f["lat_from"], f["lat_to"]))
        else:
            lo, hi = f["lat"] - f["width"] / 2, f["lat"] + f["width"] / 2
        if layer.snap:
            lo, hi = _snap_edge(lo, env), _snap_edge(hi, env)
        lo, hi = max(-90.0, lo), min(90.0, hi)
        center, inner, outer = _NORTH, math.radians(90 - hi), math.radians(90 - lo)
    elif t == "meridian":
        half = f["width"] / 2
        center = _latlon_dir(0.0, f["lon"] + 90.0)
        inner, outer = math.radians(90 - half), math.radians(90 + half)
    elif t == "fill":
        center, inner, outer = _NORTH, 0.0, math.pi
    else:  # circle | ring | annulus
        center = _latlon_dir(f["at_lat"], f["at_lon"])
        if t == "circle":
            inner, outer = 0.0, math.radians(f["radius"])
        elif t == "ring":
            half = f["width"] / 2
            inner, outer = math.radians(f["radius"] - half), math.radians(f["radius"] + half)
        else:
            inner, outer = math.radians(f["inner"]), math.radians(f["outer"])
    inner, outer = max(0.0, inner), min(math.pi, max(outer, 0.0))

    if layer.snap:
        softness = 0.0
    elif layer.softness is not None:
        softness = math.radians(max(0.0, _num(layer.softness(env))))
    else:
        softness = math.radians(DEFAULT_SOFTNESS_DEG)

    color = layer.color if isinstance(layer.color, tuple) else _color(layer.color(env))
    scale = (
        (_num(layer.tex_scale[0](env)), _num(layer.tex_scale[1](env))) if layer.tex_scale else (1.0, 1.0)
    )
    return ShapeParams(
        center=center,
        inner=inner,
        outer=outer,
        softness=softness,
        color=color,
        alpha=min(1.0, max(0.0, _num(layer.alpha(env)))),
        blend=layer.blend,
        texture=layer.tex_asset,
        tex_mapping=layer.tex_mapping,
        tex_scale=scale,
        tex_scroll=_num(layer.tex_scroll(env)) if layer.tex_scroll else 0.0,
        world_frame=layer.world_frame,
    )


# --- compilation -------------------------------------------------------------------------

_REQUIRED: dict[str, tuple[str, ...]] = {
    "band": (),  # either lat+width or lat_from+lat_to, checked below
    "meridian": ("lon", "width"),
    "circle": ("at", "radius"),
    "ring": ("at", "radius", "width"),
    "annulus": ("at", "inner", "outer"),
    "fill": (),
}


class _Compiler:
    def __init__(self, model: SceneFile, base_dir: Path):
        self.model = model
        self.base_dir = base_dir
        self.problems: list[str] = []
        self.params = list(model.params)

    def expr(self, src: Any, where: str, extra_vars: tuple[str, ...] = ("i", "n")) -> Expr:
        try:
            return compile_expr(src, variables=(*CLOCK_VARS, *extra_vars), params=self.params)
        except ExprError as e:
            self.problems.append(f"{where}: {e}")
            return compile_expr(0)  # placeholder; the scene is rejected anyway

    def color(self, value: Any, where: str) -> Expr | tuple[float, float, float]:
        if isinstance(value, list):
            if len(value) != 3 or not all(0.0 <= v <= 1.0 for v in value):
                self.problems.append(f"{where}: colour list needs 3 numbers between 0 and 1")
                return (1.0, 1.0, 1.0)
            return (float(value[0]), float(value[1]), float(value[2]))
        if isinstance(value, str) and value.strip().startswith("#"):
            try:
                return parse_hex_color(value)
            except ValueError as e:
                self.problems.append(f"{where}: {e}")
                return (1.0, 1.0, 1.0)
        if isinstance(value, str) and value.strip() in CONSTANTS and isinstance(CONSTANTS[value.strip()], tuple):
            return CONSTANTS[value.strip()]  # type: ignore[return-value]
        return self.expr(value, where)

    def layer(self, idx: int, m: Layer, asset_order: list[str]) -> CompiledLayer:
        w = f"layers[{idx}]"
        fields: dict[str, Expr] = {}

        def put(key: str, value: Num | None, name: str | None = None) -> None:
            if value is not None:
                fields[key] = self.expr(value, f"{w}.{name or key}")

        for req in _REQUIRED[m.type]:
            if getattr(m, req) is None:
                self.problems.append(f"{w}: '{m.type}' needs '{req}'")
        if m.type == "band":
            if m.lat_from is not None or m.lat_to is not None:
                if m.lat_from is None or m.lat_to is None:
                    self.problems.append(f"{w}: 'band' needs both lat_from and lat_to")
                if m.lat is not None or m.width is not None:
                    self.problems.append(f"{w}: 'band' takes either lat+width or lat_from+lat_to, not both")
                put("lat_from", m.lat_from)
                put("lat_to", m.lat_to)
            else:
                if m.lat is None or m.width is None:
                    self.problems.append(f"{w}: 'band' needs lat and width (or lat_from and lat_to)")
                put("lat", m.lat)
                put("width", m.width)
        elif m.type == "meridian":
            put("lon", m.lon)
            put("width", m.width)
        elif m.type in ("circle", "ring", "annulus"):
            if m.at is not None:
                put("at_lat", m.at.lat, "at.lat")
                put("at_lon", m.at.lon, "at.lon")
            put("radius", m.radius)
            put("inner", m.inner)
            put("outer", m.outer)
            put("width", m.width)
        if m.snap and m.type not in ("band",):
            self.problems.append(f"{w}: 'snap: rows' only applies to bands")

        tex_asset, tex_mapping, tex_scale, tex_scroll = -1, "shape", None, None
        if m.texture is not None:
            if m.texture.asset not in self.model.assets:
                self.problems.append(
                    f"{w}.texture.asset: '{m.texture.asset}' is not declared under 'assets'"
                )
            else:
                tex_asset = asset_order.index(m.texture.asset)
            tex_mapping = m.texture.mapping
            tex_scale = (
                self.expr(m.texture.scale[0], f"{w}.texture.scale[0]"),
                self.expr(m.texture.scale[1], f"{w}.texture.scale[1]"),
            )
            tex_scroll = self.expr(m.texture.scroll, f"{w}.texture.scroll")

        return CompiledLayer(
            id=m.id,
            type=m.type,
            repeat=m.repeat,
            enabled=self.expr(m.enabled, f"{w}.enabled"),
            snap=m.snap == "rows",
            world_frame=m.frame == "world",
            blend=m.blend,
            softness=self.expr(m.softness, f"{w}.softness") if m.softness is not None else None,
            alpha=self.expr(m.alpha, f"{w}.alpha"),
            color=self.color(m.color, f"{w}.color"),
            fields=fields,
            tex_asset=tex_asset,
            tex_mapping=tex_mapping,
            tex_scale=tex_scale,
            tex_scroll=tex_scroll,
        )


def compile_scene(model: SceneFile, scene_id: str, base_dir: Path, source: Path | None = None) -> Scene:
    """Compile a validated model. Raises SceneError listing every problem."""
    c = _Compiler(model, base_dir)
    if model.schema_version != SCHEMA_VERSION:
        c.problems.append(f"schema: unsupported version {model.schema_version} (expected {SCHEMA_VERSION})")
    total = sum(layer.repeat for layer in model.layers)
    if total > MAX_SHAPES:
        c.problems.append(f"layers: {total} shapes after 'repeat' exceeds the maximum of {MAX_SHAPES}")
    for name, p in model.params.items():
        if p.type == "choice" and not p.choices:
            c.problems.append(f"params.{name}: type 'choice' needs 'choices'")

    asset_order = list(model.assets)
    asset_paths: list[str] = []
    for key in asset_order:
        path = (base_dir / model.assets[key]).resolve()
        if not path.is_file():
            c.problems.append(f"assets.{key}: file not found: {model.assets[key]}")
        asset_paths.append(str(path))

    layers = [c.layer(i, m, asset_order) for i, m in enumerate(model.layers)]
    # the whole-scene spin is evaluated once per frame, outside any repeat, so i and n do not exist there
    spin_deg = c.expr(model.spin.deg, "spin.deg", extra_vars=()) if model.spin else None

    if c.problems:
        raise SceneError(str(source or scene_id), c.problems)
    return Scene(
        id=scene_id,
        name=model.name,
        description=model.description,
        tags=model.tags,
        beats_per_bar=model.tempo.beats_per_bar,
        params=model.params,
        asset_order=asset_paths,
        transition_in=model.transition.in_,
        spin_axis=model.spin.axis if model.spin else None,
        spin_deg=spin_deg,
        layers=layers,
        source=source,
    )


# --- loading -----------------------------------------------------------------------------


def _format_validation(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ""
        for part in err["loc"]:
            loc += f"[{part}]" if isinstance(part, int) else (f".{part}" if loc else str(part))
        msg = err["msg"]
        if err["type"] == "extra_forbidden":
            msg = "unknown key"
        out.append(f"{loc or 'file'}: {msg}")
    return out


def load_scene_text(text: str, scene_id: str, base_dir: Path, source: Path | None = None) -> Scene:
    label = str(source or scene_id)
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as e:
        raise SceneError(label, [f"YAML syntax error: {e}".replace("\n", " ")]) from None
    if not isinstance(data, dict):
        raise SceneError(label, ["file must contain a mapping at the top level"])
    try:
        model = SceneFile.model_validate(data)
    except ValidationError as e:
        raise SceneError(label, _format_validation(e)) from None
    return compile_scene(model, scene_id, base_dir, source)


def load_scene(path: Path) -> Scene:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SceneError(str(path), [f"cannot read file: {e}"]) from None
    return load_scene_text(text, path.stem, path.parent, path)


def load_library(*dirs: Path) -> tuple[dict[str, Scene], dict[str, SceneError]]:
    """Load every ``*.yaml`` in ``dirs`` (later directories override earlier ones by scene id).
    Returns (scenes, errors); one broken file never hides the others."""
    scenes: dict[str, Scene] = {}
    errors: dict[str, SceneError] = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.yaml")):
            try:
                scenes[path.stem] = load_scene(path)
                errors.pop(path.stem, None)
            except SceneError as e:
                errors[path.stem] = e
                scenes.pop(path.stem, None)
    return scenes, errors
