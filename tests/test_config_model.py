from datetime import datetime

import pytest
from ruamel.yaml import YAML

from mirrorballs.common.scene import load_scene
from mirrorballs.control import config_model as cm
from mirrorballs.control import scene_files as sf


def test_note_and_trigger_labels():
    assert cm.note_name(60) == "C4" and cm.note_name(36) == "C2" and cm.note_name(32) == "G#1"
    assert cm.trigger_label({"note": 36}) == "Note C2 (36)"
    assert cm.trigger_label({"note": 36, "ch": 10}) == "Note C2 (36) · ch 10"
    assert cm.trigger_label({"cc": 7}) == "CC 7" and cm.trigger_label({"program": 3, "ch": 1}) == "Program 3 · ch 1"
    assert cm.trigger_label({}) == "Not set"
    assert cm.trigger_from_learned({"note": 36, "ch": 2, "junk": 1}) == {"note": 36, "ch": 2}


def test_actions():
    opts = cm.action_options(["a", "b"], {"a": "Alpha", "b": "Beta"})
    assert [label for _, label in opts] == ["Scene 1 · Alpha", "Scene 2 · Beta", "Next scene", "Master brightness", "Blackout"]
    assert cm.action_index({"scene": "b"}, opts) == 1 and cm.action_index("blackout", opts) == 4 and cm.action_index("zzz", opts) == -1
    assert cm.normalise_action({"scene_index": 1}, ["a", "b"]) == {"scene": "b"}
    assert cm.normalise_action({"scene_index": 9}, ["a", "b"]) == {"scene_index": 9}
    assert cm.normalise_action("next", ["a"]) == "next"


def test_ball_conversions_match_the_design_example():
    assert cm.rows_from_tile(30, 10) == pytest.approx(47.12, abs=0.01)  # the design shows 47 rows for 30 cm / 10 mm
    assert cm.tile_from_rows(30, cm.rows_from_tile(30, 10)) == pytest.approx(10)
    assert cm.pitch_from_rows(47.3) == pytest.approx(3.806, abs=0.001)  # design: "rows ≈ 47.3 · tile 3.8°"


def test_describe_time():
    now = datetime(2026, 9, 18, 22, 0)
    assert cm.describe_time("2026-09-18T21:14", now) == "Today, 21:14"
    assert cm.describe_time("2026-09-17T09:02", now) == "Yesterday, 09:02"
    assert cm.describe_time("2026-09-01T18:30", now) == "1 Sep, 18:30"
    assert cm.describe_time(None, now) == "Never" and cm.describe_time("garbage", now) == "Never"


def test_calibration_readout():
    assert cm.calibration_readout({"left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8}, 1920, 1080) == ("960, 540 px", "768 × 648 px")
    assert cm.calibration_readout(None, 1920, 1080)[0] == "Not calibrated"


# --- scene files ---------------------------------------------------------------------------


def test_slug_and_unique_ids(tmp_path):
    assert sf.slugify("  Aurora Bands! ") == "aurora-bands" and sf.slugify("###") == "scene"
    (tmp_path / "aurora.yaml").write_text("x")
    assert sf.unique_id(tmp_path, "aurora") == "aurora-2"
    assert sf.unique_id(tmp_path, "aurora", {"aurora-2"}) == "aurora-3"
    assert sf.unique_id(tmp_path, "fresh") == "fresh"


def test_created_scene_is_valid(tmp_path):
    p = sf.create_scene(tmp_path, "My: New #1 scene")
    sc = load_scene(p)
    assert sc.name == "My: New #1 scene" and p.name == "my-new-1-scene.yaml" and sc.layers
    p2 = sf.create_scene(tmp_path, "My: New #1 scene")
    assert p2 != p and load_scene(p2)


def test_duplicate_keeps_comments_and_fixes_asset_paths(tmp_path):
    src_dir = tmp_path / "bundled" / "scenes"
    (tmp_path / "bundled" / "assets").mkdir(parents=True)
    src_dir.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (4, 4)).save(tmp_path / "bundled" / "assets" / "t.png")
    src = src_dir / "orig.yaml"
    src.write_text("schema: 1\nname: Original  # my favourite\nassets:\n  t: ../assets/t.png\nlayers:\n  - {type: fill, texture: {asset: t}}\n")
    user = tmp_path / "user"
    dst = sf.duplicate_scene(src, user, "Original copy")
    assert dst.parent == user and dst.name == "original-copy.yaml"
    sc = load_scene(dst)  # loads: the asset path was made absolute, so it still resolves from another directory
    assert sc.name == "Original copy" and sc.asset_order[0].endswith("assets/t.png")
    assert load_scene(src).name == "Original"  # the source is untouched


def test_rename_changes_the_name_only_and_keeps_other_comments(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("# header comment\nschema: 1\nname: Old\ntags: [a]  # keep me\nlayers:\n  - {type: fill}\n")
    sf.rename_scene(p, "New Name")
    text = p.read_text()
    assert "# header comment" in text and "# keep me" in text
    sc = load_scene(p)
    assert sc.name == "New Name" and sc.id == "s" and not list(tmp_path.glob("*.tmp"))
    sf.delete_scene(p)
    assert not p.exists()
