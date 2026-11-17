"""Every scene example in the documentation must load and run.

The scene language is the part of the app people write by hand, so its docs are the part most worth
keeping honest: a renamed key or a dropped function would otherwise be found by a reader, at a party.
"""

import re
from pathlib import Path

import pytest

from floorshow.common.scene import SceneError, load_scene_text, make_env

ROOT = Path(__file__).resolve().parent.parent
TUTORIAL, REFERENCE = ROOT / "docs/scenes-tutorial.md", ROOT / "docs/scenes-reference.md"
DOCS = [TUTORIAL, REFERENCE]
SCENE_DIR = ROOT / "scenes"  # so ../assets/... resolves as it does for a real scene


def yaml_blocks(path: Path) -> list[tuple[int, str]]:
    """(line number, source) of every ```yaml block, minus the ones that are only key listings."""
    out = []
    for m in re.finditer(r"```yaml\n(.*?)```", path.read_text(encoding="utf-8"), re.S):
        text = m.group(1)
        if "layers:" not in text or "..." in text:
            continue  # a fragment showing keys, not a scene
        out.append((path.read_text(encoding="utf-8")[: m.start()].count("\n") + 1, text))
    return out


def clock_states(scene):
    for beat in (0.0, 0.37, 1.0, 2.5, 7.9, 33.3):
        yield make_env(beat=beat, phase=beat % 1, beats_per_bar=4, scene_beat=beat, time=beat / 2,
                       bpm=120.0, trans=min(1.0, beat), tile=3.8, row_offset=0.4,
                       params=scene.default_params())


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_every_documented_scene_loads_and_evaluates(path):
    blocks = yaml_blocks(path)
    assert blocks, f"{path.name}: no scene examples found (did the fences change?)"
    for line, text in blocks:
        where = f"{path.name}:{line}"
        if "schema:" not in text:  # the docs shorten some examples to the interesting part
            text = "schema: 1\nname: t\n" + text
        try:
            scene = load_scene_text(text, "t", SCENE_DIR)
        except SceneError as e:
            pytest.fail(f"{where}: {e}")
            raise  # unreachable; keeps `scene` defined for type checkers
        for env in clock_states(scene):
            scene.evaluate(env)
            scene.spin(env)
        assert not scene.runtime_errors(), f"{where}: {scene.runtime_errors()}"


def test_the_tutorial_names_every_bundled_example():
    named = set(re.findall(r"`([a-z-]+)`", TUTORIAL.read_text(encoding="utf-8")))
    bundled = {p.stem for p in SCENE_DIR.glob("*.yaml")}
    assert bundled <= named, f"the tutorial points at scenes/ but does not mention {sorted(bundled - named)}"
