"""Scene file operations for the scenes dialog: create, duplicate, rename, delete.

Files are edited with a round-trip YAML loader so a user's comments and layout survive a rename.
A duplicated bundled scene gets absolute asset paths, because its relative ones (``../assets/x.jpg``)
only make sense next to the original."""

from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML

TEMPLATE = """\
schema: 1
name: {name}
description: ""
tags: []
layers:
  - {{type: band, lat: 0, width: 12, color: white}}
"""


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = None
    y.width = 4096
    return y


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "scene"


def unique_id(directory: Path, base: str, taken: set[str] | None = None) -> str:
    """``base``, or ``base-2``, ``base-3``... so that no file (or known scene id) is overwritten."""
    taken = taken or set()
    n, sid = 1, base
    while (directory / f"{sid}.yaml").exists() or sid in taken:
        n += 1
        sid = f"{base}-{n}"
    return sid


def create_scene(directory: Path, name: str, taken: set[str] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{unique_id(directory, slugify(name), taken)}.yaml"
    path.write_text(TEMPLATE.format(name=_yaml_scalar(name)), encoding="utf-8")
    return path


def _yaml_scalar(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r"[:#\[\]{},&*!|>'\"%@`]", text) or text != text.strip() else text


def duplicate_scene(src: Path, directory: Path, new_name: str, taken: set[str] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    y = _yaml()
    data = y.load(src.read_text(encoding="utf-8"))
    data["name"] = new_name
    if src.parent.resolve() != directory.resolve():
        assets = data.get("assets")
        if assets:
            for key, rel in list(assets.items()):
                assets[key] = str((src.parent / rel).resolve()) if not Path(rel).is_absolute() else rel
    dst = directory / f"{unique_id(directory, slugify(new_name), taken)}.yaml"
    with dst.open("w", encoding="utf-8") as f:
        y.dump(data, f)
    return dst


def rename_scene(path: Path, new_name: str) -> None:
    """Change the scene's display name (the ``name:`` field). The file name, which is its id, stays."""
    y = _yaml()
    data = y.load(path.read_text(encoding="utf-8"))
    data["name"] = new_name
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        y.dump(data, f)
    tmp.replace(path)


def delete_scene(path: Path) -> None:
    path.unlink()
