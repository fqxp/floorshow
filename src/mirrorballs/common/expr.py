"""Tiny, safe expression language for scene files.

Scene YAML holds strings such as ``"(1 - 2*i) * 90 * ease_out(phase)"``. They are parsed with
``ast``, checked against a whitelist of node types and names, compiled once, and evaluated
every frame with a plain dict of variables. Scene files are meant to be shared, so the
whitelist is a security boundary: no attribute access (except ``param.<name>``), subscripts,
lambdas, comprehensions, keyword arguments or imports.

All numeric literals are floats, so ``2 ** 99999`` cannot build a huge int. Values are floats,
or tuples for colours (``rgb``, ``hsv``, ``hex``, ``mix`` of colours).
"""

from __future__ import annotations

import ast
import colorsys
import difflib
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

MAX_SOURCE_LEN = 400
MAX_NODES = 200

Value = float | tuple[float, ...]


class ExprError(ValueError):
    """Invalid expression. The message names the problem and, where possible, a fix."""


# --- functions ---------------------------------------------------------------------------


def _fract(x: float) -> float:
    return x - math.floor(x)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _smoothstep(e0: float, e1: float, x: float) -> float:
    if e0 == e1:
        return 0.0 if x < e0 else 1.0
    u = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def _mix(a: Value, b: Value, u: float) -> Value:
    if isinstance(a, tuple) and isinstance(b, tuple):
        if len(a) != len(b):
            raise ValueError("mix: colour size mismatch")
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    if isinstance(a, tuple) or isinstance(b, tuple):
        raise ValueError("mix: cannot mix a number with a colour")
    return a + (b - a) * u


def _step(edge: float, x: float) -> float:
    return 1.0 if x >= edge else 0.0


def _ease_in(u: float) -> float:
    u = _clamp(u, 0.0, 1.0)
    return u * u


def _ease_out(u: float) -> float:
    u = _clamp(u, 0.0, 1.0)
    return 1.0 - (1.0 - u) * (1.0 - u)


def _ease_in_out(u: float) -> float:
    u = _clamp(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def _tri(x: float) -> float:
    """Triangle wave, 0..1, period 1 (0 at integers, 1 at half-integers)."""
    return 1.0 - abs(2.0 * _fract(x) - 1.0)


def _pulse(x: float, width: float) -> float:
    """1 for the first ``width`` (0..1) of each period, else 0."""
    return 1.0 if _fract(x) < width else 0.0


_M32 = 0xFFFFFFFF


def _rand(seed: float) -> float:
    """Deterministic hash of ``floor(seed)`` to [0, 1) (Wang hash)."""
    n = int(math.floor(seed)) & _M32
    n = (n ^ 61) ^ (n >> 16)
    n = (n * 9) & _M32
    n ^= n >> 4
    n = (n * 0x27D4EB2D) & _M32
    n ^= n >> 15
    return n / 4294967296.0


def _noise(x: float) -> float:
    """Smooth 1-D value noise in [0, 1)."""
    i = math.floor(x)
    u = _smoothstep(0.0, 1.0, x - i)
    a, b = _rand(i), _rand(i + 1)
    return a + (b - a) * u


def _snap_row(lat: float, tile: float, offset: float | None) -> float:
    """Latitude of the centre of the tile row containing ``lat``. Identity if rows are uncalibrated."""
    if offset is None:
        return lat
    return offset + (math.floor((lat - offset) / tile) + 0.5) * tile


def _snap_edge(lat: float, tile: float, offset: float | None) -> float:
    """Latitude of the nearest tile row boundary. Identity if rows are uncalibrated."""
    if offset is None:
        return lat
    return offset + round((lat - offset) / tile) * tile


def _rgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    return (r, g, b)


def _hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    return colorsys.hsv_to_rgb(_fract(h), _clamp(s, 0.0, 1.0), _clamp(v, 0.0, 1.0))


def parse_hex_color(text: str) -> tuple[float, float, float]:
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise ValueError(f"bad hex colour {text!r}")
    return tuple(int(t[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "min": min,
    "max": max,
    "floor": lambda x: float(math.floor(x)),
    "ceil": lambda x: float(math.ceil(x)),
    "round": lambda x: float(round(x)),
    "fract": _fract,
    "sqrt": math.sqrt,
    "pow": math.pow,
    "clamp": _clamp,
    "mix": _mix,
    "smoothstep": _smoothstep,
    "step": _step,
    "ease_in": _ease_in,
    "ease_out": _ease_out,
    "ease_in_out": _ease_in_out,
    "tri": _tri,
    "saw": _fract,
    "pulse": _pulse,
    "rand": _rand,
    "noise": _noise,
    "snap_row": _snap_row,
    "snap_edge": _snap_edge,
    "rgb": _rgb,
    "hsv": _hsv,
    "hex": parse_hex_color,
}

# Functions that get the row grid (``tile``, ``row_offset``) appended as hidden arguments.
_ROW_FUNCTIONS = {"snap_row", "snap_edge"}

CONSTANTS: dict[str, Value] = {
    "pi": math.pi,
    "tau": math.tau,
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
    "yellow": (1.0, 1.0, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "orange": (1.0, 0.5, 0.0),
    "pink": (1.0, 0.4, 0.7),
    "purple": (0.6, 0.2, 1.0),
}

# Names every evaluation environment must provide. ``row_offset`` is internal (used by the
# hidden snap_row / snap_edge arguments); it is ``None`` when the rows are uncalibrated.
CLOCK_VARS = ("beat", "phase", "bar", "scene_beat", "time", "bpm", "trans", "tile")
INTERNAL_VARS = ("row_offset",)

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARY = (ast.USub, ast.UAdd, ast.Not)
_ALLOWED_CMP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _hint(name: str, candidates: Iterable[str]) -> str:
    close = difflib.get_close_matches(name, list(candidates), n=1)
    return f" (did you mean '{close[0]}'?)" if close else ""


class _Rewriter(ast.NodeTransformer):
    """Validates the tree against the whitelist and rewrites it for evaluation."""

    def __init__(self, variables: set[str], params: set[str]):
        self.variables = variables
        self.params = params

    def _fail(self, msg: str, node: ast.AST) -> ExprError:
        return ExprError(f"{msg} at column {getattr(node, 'col_offset', 0) + 1}")

    def generic_visit(self, node: ast.AST) -> ast.AST:
        raise self._fail(f"'{type(node).__name__}' is not allowed in expressions", node)

    def visit_Expression(self, node: ast.Expression) -> ast.AST:
        node.body = self.visit(node.body)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        v = node.value
        if isinstance(v, bool):
            return ast.copy_location(ast.Constant(1.0 if v else 0.0), node)
        if isinstance(v, (int, float)):
            return ast.copy_location(ast.Constant(float(v)), node)
        # Strings are only valid inside hex("#rrggbb"), which visit_Call handles without visiting its argument.
        raise self._fail("only numbers are allowed as literals (strings only inside hex())", node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if not isinstance(node.ctx, ast.Load):
            raise self._fail("assignment is not allowed", node)
        name = node.id
        if name in FUNCTIONS:
            raise self._fail(f"'{name}' is a function; call it as {name}(...)", node)
        if name in CONSTANTS or name in self.variables:
            return node
        known = [*self.variables, *CONSTANTS]
        raise self._fail(f"unknown name '{name}'{_hint(name, known)}", node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if isinstance(node.value, ast.Name) and node.value.id == "param":
            if node.attr not in self.params:
                raise self._fail(
                    f"unknown parameter 'param.{node.attr}'{_hint(node.attr, self.params)}", node
                )
            # 'param.width' cannot be typed as an identifier, so it cannot collide with a variable.
            return ast.copy_location(ast.Name(id=f"param.{node.attr}", ctx=ast.Load()), node)
        raise self._fail("attribute access is not allowed (only param.<name>)", node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise self._fail(f"operator '{type(node.op).__name__}' is not allowed", node)
        node.left, node.right = self.visit(node.left), self.visit(node.right)
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise self._fail(f"operator '{type(node.op).__name__}' is not allowed", node)
        node.operand = self.visit(node.operand)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        node.values = [self.visit(v) for v in node.values]
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if not all(isinstance(op, _ALLOWED_CMP) for op in node.ops):
            raise self._fail("only ==, !=, <, <=, >, >= comparisons are allowed", node)
        node.left = self.visit(node.left)
        node.comparators = [self.visit(c) for c in node.comparators]
        return node

    def visit_IfExp(self, node: ast.IfExp) -> ast.AST:
        node.test, node.body, node.orelse = (
            self.visit(node.test),
            self.visit(node.body),
            self.visit(node.orelse),
        )
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if not isinstance(node.func, ast.Name):
            raise self._fail("only direct calls to built-in functions are allowed", node)
        fname = node.func.id
        if fname not in FUNCTIONS:
            raise self._fail(f"unknown function '{fname}'{_hint(fname, FUNCTIONS)}", node)
        if node.keywords:
            raise self._fail("keyword arguments are not allowed", node)
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise self._fail("'*' arguments are not allowed", node)
        if fname == "hex":
            if len(node.args) != 1 or not (
                isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                raise self._fail('hex() takes one string literal, e.g. hex("#ff8800")', node)
            try:
                parse_hex_color(node.args[0].value)
            except ValueError as e:
                raise self._fail(str(e), node) from None
            return node
        node.args = [self.visit(a) for a in node.args]
        if fname in _ROW_FUNCTIONS:
            node.args += [ast.Name(id="tile", ctx=ast.Load()), ast.Name(id="row_offset", ctx=ast.Load())]
        return node


_GLOBALS: dict[str, Any] = {"__builtins__": {}, **FUNCTIONS, **CONSTANTS}


class Expr:
    """A compiled expression. Call with a mapping of variables to get a float or colour tuple."""

    __slots__ = ("source", "_code", "last_error")

    def __init__(self, source: str, code: Any):
        self.source = source
        self._code = code
        self.last_error: str | None = None

    def __call__(self, env: Mapping[str, Any]) -> Value:
        """Evaluate. A runtime error (division by zero, sqrt of a negative, a variable this
        environment does not provide) yields 0.0 and is recorded in ``last_error``, so one bad
        scene can never crash the renderer."""
        try:
            result = eval(self._code, _GLOBALS, env)  # noqa: S307
        except (ArithmeticError, ValueError, TypeError, NameError) as e:
            self.last_error = f"{self.source!r}: {e}"
            return 0.0
        if isinstance(result, (float, tuple)):
            return result
        if isinstance(result, (int, bool)):
            return float(result)
        self.last_error = f"{self.source!r}: result is {type(result).__name__}, not a number"  # e.g. (-8)**0.5
        return 0.0

    def __repr__(self) -> str:
        return f"Expr({self.source!r})"


def compile_expr(
    source: str | int | float,
    *,
    variables: Iterable[str] = CLOCK_VARS,
    params: Iterable[str] = (),
) -> Expr:
    """Compile ``source``. Raises ExprError naming the problem.

    ``variables`` are the free names the expression may use (clock variables plus e.g. ``i``,
    ``n`` inside a repeat). ``params`` are the scene parameter names usable as ``param.<name>``.
    """
    text = str(source).strip()
    if isinstance(source, (int, float)) and not isinstance(source, bool):
        text = repr(float(source))
    if not text:
        raise ExprError("empty expression")
    if len(text) > MAX_SOURCE_LEN:
        raise ExprError(f"expression too long ({len(text)} > {MAX_SOURCE_LEN} characters)")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg} at column {e.offset or 1}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExprError("expression too complex")
    tree = _Rewriter({*variables, *INTERNAL_VARS}, set(params)).visit(tree)
    ast.fix_missing_locations(tree)
    return Expr(text, compile(tree, "<scene-expr>", "eval"))
