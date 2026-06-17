import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import (
    detect_packs, process_addon, format_bytes, merge_pack_entries,
    _extract_owned_uuids, _sanitize_folder_name, _classify_manifest,
)
from core import AddonItem

RP_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BP_UUID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
RP_MODULE_UUID = "a0000000-0000-4000-8000-000000000000"
BP_MODULE_UUID = "b0000000-0000-4000-8000-000000000000"


def create_test_addon(path: Path):
    manifest_rp = {
        "format_version": 2,
        "header": {
            "name": "Test Resource Pack",
            "description": "A test resource pack",
            "uuid": RP_UUID,
            "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [{"type": "resources", "uuid": RP_MODULE_UUID, "version": [1, 0, 0]}],
    }
    manifest_bp = {
        "format_version": 2,
        "header": {
            "name": "Test Behavior Pack",
            "description": "A test behavior pack",
            "uuid": BP_UUID,
            "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [{"type": "data", "uuid": BP_MODULE_UUID, "version": [1, 0, 0]}],
    }
    en_us_lang = "pack.name=My Cool Pack\npack.description=An awesome test pack\n"

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("resources/manifest.json", json.dumps(manifest_rp, indent=2))
        zf.writestr("data/manifest.json", json.dumps(manifest_bp, indent=2))
        zf.writestr("texts/en_US.lang", en_us_lang)
        zf.writestr("resources/textures/test.png", b"fake png data")
        zf.writestr("resources/textures/test2.png", b"fake png data 2")


def create_test_addon_dir(base_dir: Path):
    (base_dir / "resources").mkdir(parents=True, exist_ok=True)
    (base_dir / "data").mkdir(parents=True, exist_ok=True)
    (base_dir / "texts").mkdir(parents=True, exist_ok=True)

    manifest_rp = {
        "format_version": 2,
        "header": {
            "name": "Test Resource Pack",
            "description": "A test resource pack",
            "uuid": RP_UUID,
            "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [{"type": "resources", "uuid": RP_MODULE_UUID, "version": [1, 0, 0]}],
    }
    manifest_bp = {
        "format_version": 2,
        "header": {
            "name": "Test Behavior Pack",
            "description": "A test behavior pack",
            "uuid": BP_UUID,
            "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [{"type": "data", "uuid": BP_MODULE_UUID, "version": [1, 0, 0]}],
    }

    (base_dir / "resources" / "manifest.json").write_text(json.dumps(manifest_rp, indent=2), "utf-8")
    (base_dir / "data" / "manifest.json").write_text(json.dumps(manifest_bp, indent=2), "utf-8")
    (base_dir / "texts" / "en_US.lang").write_text("pack.name=My Cool Pack\npack.description=An awesome test pack\n", "utf-8")
    (base_dir / "resources" / "textures").mkdir(parents=True, exist_ok=True)
    (base_dir / "resources" / "textures" / "test.png").write_bytes(b"fake png data")
    (base_dir / "resources" / "textures" / "test2.png").write_bytes(b"fake png data 2")


def test_detect():
    tmp = Path(tempfile.mkdtemp())
    addon_path = tmp / "test_addon.mcaddon"
    create_test_addon(addon_path)

    info = detect_packs(addon_path)
    assert info["has_rp"], "Should detect RP"
    assert info["has_bp"], "Should detect BP"
    assert info["rp_original_uuid"] == RP_UUID, f"RP UUID mismatch: {info['rp_original_uuid']}"
    assert info["bp_original_uuid"] == BP_UUID, f"BP UUID mismatch: {info['bp_original_uuid']}"
    assert info["pack_name"] == "My Cool Pack", f"pack_name mismatch: {info['pack_name']}"
    assert info["pack_description"] == "An awesome test pack", f"desc mismatch: {info['pack_description']}"
    print("[OK] detect_packs (zip)")

    addon = AddonItem(
        file_path=addon_path,
        has_rp=info["has_rp"],
        has_bp=info["has_bp"],
        rp_manifest_path=info["rp_manifest_path"] or "",
        bp_manifest_path=info["bp_manifest_path"] or "",
        rp_original_uuid=info["rp_original_uuid"] or "",
        bp_original_uuid=info["bp_original_uuid"] or "",
        pack_name=info["pack_name"] or "",
        pack_description=info["pack_description"] or "",
    )

    out_dir = tmp / "output1"
    out_dir.mkdir()

    def on_progress(pct, msg):
        pass

    process_addon(addon, out_dir, on_progress, [False])

    out_files = list(out_dir.iterdir())
    patched = [f for f in out_files if f.suffix == ".mcaddon"]
    assert len(patched) == 1, f"Expected 1 output .mcaddon, got {len(patched)}"
    patched_path = patched[0]
    assert patched_path.stat().st_size > 0, "Output file should not be empty"
    print(f"[OK] Process addon: output = {patched_path.name} ({format_bytes(patched_path.stat().st_size)})")

    with zipfile.ZipFile(patched_path, "r") as zf:
        names = zf.namelist()
        assert "resources/manifest.json" in names, "Output missing resources/manifest.json"
        assert "data/manifest.json" in names, "Output missing data/manifest.json"

        rp = json.loads(zf.read("resources/manifest.json"))
        bp = json.loads(zf.read("data/manifest.json"))

        assert rp["header"]["uuid"] != RP_UUID, "RP UUID should have changed"
        assert bp["header"]["uuid"] != BP_UUID, "BP UUID should have changed"
        assert rp["header"]["name"] == "My Cool Pack - Patch", f"RP name not patched: {rp['header']['name']}"
        assert bp["header"]["name"] == "My Cool Pack - Patch", f"BP name not patched: {bp['header']['name']}"
        assert rp["header"]["description"] == "An awesome test pack (patched)", f"RP desc not patched: {rp['header']['description']}"
        assert " - Patch" in rp["header"]["name"], "RP name should contain ' - Patch'"
        print("[OK] UUID patching and naming")

        rp_deps = rp.get("dependencies", [])
        bp_deps = bp.get("dependencies", [])
        assert any(d.get("uuid") == bp["header"]["uuid"] for d in rp_deps), "RP should depend on BP"
        assert any(d.get("uuid") == rp["header"]["uuid"] for d in bp_deps), "BP should depend on RP"
        print("[OK] Dependencies cross-linked")

        assert "resources/textures/test.png" in names, "Output missing test.png"
        assert "texts/en_US.lang" in names, "Output missing en_US.lang"
        print("[OK] All files preserved")

    assert addon.new_rp_uuid == rp["header"]["uuid"], "new_rp_uuid mismatch"
    assert addon.new_bp_uuid == bp["header"]["uuid"], "new_bp_uuid mismatch"
    print(f"[OK] New UUIDs set on addon: RP={addon.new_rp_uuid[:8]}... BP={addon.new_bp_uuid[:8]}...")

    import json as _json
    rp_entries = [{"pack_id": addon.new_rp_uuid, "version": [1, 0, 0]}]
    bp_entries = [{"pack_id": addon.new_bp_uuid, "version": [1, 0, 0]}]
    world_rp = out_dir / "world_resource_packs.json"
    world_bp = out_dir / "world_behavior_packs.json"
    world_rp.write_text(_json.dumps(rp_entries, indent=2), "utf-8")
    world_bp.write_text(_json.dumps(bp_entries, indent=2), "utf-8")
    assert world_rp.exists()
    assert world_bp.exists()
    print("[OK] world_*_packs.json written")

    shutil.rmtree(tmp)
    print("[OK] test_detect passed")


def test_detect_dir():
    tmp = Path(tempfile.mkdtemp())
    addon_dir = tmp / "test_addon_dir"
    create_test_addon_dir(addon_dir)

    info = detect_packs(addon_dir)
    assert info["has_rp"], "Should detect RP in directory"
    assert info["has_bp"], "Should detect BP in directory"
    assert info["rp_original_uuid"] == RP_UUID, f"RP UUID mismatch: {info['rp_original_uuid']}"
    assert info["bp_original_uuid"] == BP_UUID, f"BP UUID mismatch: {info['bp_original_uuid']}"
    assert info["pack_name"] == "My Cool Pack", f"pack_name mismatch: {info['pack_name']}"
    assert info["pack_description"] == "An awesome test pack", f"desc mismatch: {info['pack_description']}"
    print("[OK] detect_packs (directory)")

    addon = AddonItem(
        file_path=addon_dir,
        has_rp=info["has_rp"],
        has_bp=info["has_bp"],
        rp_manifest_path=info["rp_manifest_path"] or "",
        bp_manifest_path=info["bp_manifest_path"] or "",
        rp_original_uuid=info["rp_original_uuid"] or "",
        bp_original_uuid=info["bp_original_uuid"] or "",
        pack_name=info["pack_name"] or "",
        pack_description=info["pack_description"] or "",
        is_directory=True,
    )

    out_dir = tmp / "output_dir"
    out_dir.mkdir()

    def on_progress(pct, msg):
        pass

    process_addon(addon, out_dir, on_progress, [False])

    out_files = list(out_dir.iterdir())
    patched = [f for f in out_files if f.suffix == ".mcaddon"]
    assert len(patched) == 1, f"Expected 1 output .mcaddon from dir, got {len(patched)}"
    patched_path = patched[0]
    assert patched_path.stat().st_size > 0, "Output file should not be empty"
    print(f"[OK] Process directory addon: output = {patched_path.name}")

    with zipfile.ZipFile(patched_path, "r") as zf:
        names = zf.namelist()
        assert "resources/manifest.json" in names, "Output missing resources/manifest.json"
        assert "data/manifest.json" in names, "Output missing data/manifest.json"
        rp = json.loads(zf.read("resources/manifest.json"))
        bp = json.loads(zf.read("data/manifest.json"))
        assert rp["header"]["uuid"] != RP_UUID, "RP UUID should have changed"
        assert bp["header"]["uuid"] != BP_UUID, "BP UUID should have changed"
    print("[OK] Directory addon UUIDs patched")

    shutil.rmtree(tmp)
    print("[OK] test_detect_dir passed")


def test_full_uuid_remap():
    tmp = Path(tempfile.mkdtemp())
    addon_path = tmp / "test_remap.mcaddon"

    rp_uuid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    bp_uuid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    rp_mod1 = "c0000000-0000-4000-8000-000000000001"
    rp_mod2 = "c0000000-0000-4000-8000-000000000002"
    bp_mod1 = "d0000000-0000-4000-8000-000000000001"

    manifest_rp = {
        "format_version": 2,
        "header": {
            "name": "RP", "description": "RP desc",
            "uuid": rp_uuid, "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [
            {"type": "resources", "uuid": rp_mod1, "version": [1, 0, 0]},
            {"type": "resources", "uuid": rp_mod2, "version": [1, 0, 0]},
        ],
    }
    manifest_bp = {
        "format_version": 2,
        "header": {
            "name": "BP", "description": "BP desc",
            "uuid": bp_uuid, "version": [1, 0, 0],
            "min_engine_version": [1, 19, 0],
        },
        "modules": [
            {"type": "data", "uuid": bp_mod1, "version": [1, 0, 0]},
        ],
    }

    with zipfile.ZipFile(addon_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rp/manifest.json", json.dumps(manifest_rp, indent=2))
        zf.writestr("bp/manifest.json", json.dumps(manifest_bp, indent=2))

    info = detect_packs(addon_path)
    addon = AddonItem(
        file_path=addon_path,
        has_rp=info["has_rp"], has_bp=info["has_bp"],
        rp_manifest_path=info["rp_manifest_path"] or "",
        bp_manifest_path=info["bp_manifest_path"] or "",
        rp_original_uuid=info["rp_original_uuid"] or "",
        bp_original_uuid=info["bp_original_uuid"] or "",
    )

    out_dir = tmp / "out_remap"
    out_dir.mkdir()

    def on_progress(pct, msg):
        pass

    process_addon(addon, out_dir, on_progress, [False])

    patched = list(out_dir.glob("*.mcaddon"))[0]
    with zipfile.ZipFile(patched, "r") as zf:
        rp = json.loads(zf.read("rp/manifest.json"))
        bp = json.loads(zf.read("bp/manifest.json"))

    assert rp["header"]["uuid"] not in (rp_uuid, ""), "RP header UUID unchanged"
    assert rp["modules"][0]["uuid"] not in (rp_mod1, ""), "RP module1 UUID unchanged"
    assert rp["modules"][1]["uuid"] not in (rp_mod2, ""), "RP module2 UUID unchanged"
    assert bp["header"]["uuid"] not in (bp_uuid, ""), "BP header UUID unchanged"
    assert bp["modules"][0]["uuid"] not in (bp_mod1, ""), "BP module UUID unchanged"

    owned = _extract_owned_uuids(rp, bp)
    assert len(owned) == 5, f"Expected 5 owned UUIDs, got {len(owned)}: {owned}"
    print(f"[OK] All 5 owned UUIDs extracted: {owned}")

    all_vals = {rp["header"]["uuid"], rp["modules"][0]["uuid"], rp["modules"][1]["uuid"],
                bp["header"]["uuid"], bp["modules"][0]["uuid"]}
    assert len(all_vals) == 5, "All 5 UUIDs should be unique after remap"
    print("[OK] Full UUID remap verified")

    shutil.rmtree(tmp)
    print("[OK] test_full_uuid_remap passed")


def test_server_mode():
    tmp = Path(tempfile.mkdtemp())
    addon_path = tmp / "server_test.mcaddon"
    create_test_addon(addon_path)

    info = detect_packs(addon_path)
    addon = AddonItem(
        file_path=addon_path,
        has_rp=info["has_rp"], has_bp=info["has_bp"],
        rp_manifest_path=info["rp_manifest_path"] or "",
        bp_manifest_path=info["bp_manifest_path"] or "",
        rp_original_uuid=info["rp_original_uuid"] or "",
        bp_original_uuid=info["bp_original_uuid"] or "",
        pack_name=info["pack_name"] or "",
        pack_description=info["pack_description"] or "",
    )

    out_dir = tmp / "server_output"
    out_dir.mkdir()

    def on_progress(pct, msg):
        pass

    process_addon(addon, out_dir, on_progress, [False], server_mode=True)

    mcaddon_files = list(out_dir.glob("*.mcaddon"))
    assert len(mcaddon_files) == 0, "No .mcaddon should be created in server mode"

    rp_folder = out_dir / "resource_packs"
    bp_folder = out_dir / "behavior_packs"
    assert rp_folder.is_dir(), "resource_packs/ directory should exist"
    assert bp_folder.is_dir(), "behavior_packs/ directory should exist"

    rp_items = list(rp_folder.iterdir())
    bp_items = list(bp_folder.iterdir())
    assert len(rp_items) > 0, "resource_packs should have content"
    assert len(bp_items) > 0, "behavior_packs should have content"

    rp_manifest = rp_folder / rp_items[0].name / "manifest.json"
    bp_manifest = bp_folder / bp_items[0].name / "manifest.json"
    assert rp_manifest.exists(), f"RP manifest should exist: {rp_manifest}"
    assert bp_manifest.exists(), f"BP manifest should exist: {bp_manifest}"

    rp_data = json.loads(rp_manifest.read_text("utf-8"))
    bp_data = json.loads(bp_manifest.read_text("utf-8"))
    assert rp_data["header"]["name"] == "My Cool Pack - Patch", "RP name should be patched"
    assert bp_data["header"]["name"] == "My Cool Pack - Patch", "BP name should be patched"
    print(f"[OK] Server mode: RP in {rp_items[0].name}, BP in {bp_items[0].name}")

    shutil.rmtree(tmp)
    print("[OK] test_server_mode passed")


def test_merge_pack_entries():
    existing = [
        {"pack_id": "aaa", "version": [1, 0, 0]},
        {"pack_id": "bbb", "version": [1, 0, 0]},
    ]
    new = [
        {"pack_id": "bbb", "version": [2, 0, 0]},
        {"pack_id": "ccc", "version": [1, 0, 0]},
    ]
    merged = merge_pack_entries(existing, new)
    assert len(merged) == 3, f"Expected 3 entries, got {len(merged)}"
    merged_ids = [e["pack_id"] for e in merged]
    assert "aaa" in merged_ids, "aaa should be preserved"
    assert "bbb" in merged_ids, "bbb should be present"
    assert "ccc" in merged_ids, "ccc should be added"
    bbb_entry = [e for e in merged if e["pack_id"] == "bbb"][0]
    assert bbb_entry["version"] == [2, 0, 0], "bbb should be replaced with new version"
    print("[OK] Merge: aaa preserved, bbb replaced, ccc appended")

    empty = merge_pack_entries([], [])
    assert empty == [], "Empty merge should return empty list"
    print("[OK] Merge: empty input returns empty list")

    only_new = merge_pack_entries([], [{"pack_id": "x", "version": [1, 0, 0]}])
    assert len(only_new) == 1 and only_new[0]["pack_id"] == "x"
    print("[OK] Merge: only new entries works")

    print("[OK] test_merge_pack_entries passed")


def test_sanitize_folder_name():
    assert _sanitize_folder_name("My Cool Pack") == "My_Cool_Pack"
    assert _sanitize_folder_name("Test: Pack?") == "Test__Pack"
    assert _sanitize_folder_name("") == "unnamed_pack"
    assert _sanitize_folder_name("  ") == "unnamed_pack"
    assert _sanitize_folder_name("simple") == "simple"
    print("[OK] test_sanitize_folder_name passed")


def test_extract_owned_uuids():
    rp = {
        "header": {"uuid": RP_UUID},
        "modules": [
            {"uuid": RP_MODULE_UUID},
            {"uuid": "c0000000-0000-4000-8000-000000000000"},
        ],
    }
    bp = {
        "header": {"uuid": BP_UUID},
        "modules": [
            {"uuid": BP_MODULE_UUID},
        ],
    }
    uuids = _extract_owned_uuids(rp, bp)
    assert len(uuids) == 5, f"Expected 5 UUIDs, got {len(uuids)}: {uuids}"
    assert RP_UUID in uuids
    assert RP_MODULE_UUID in uuids
    assert BP_UUID in uuids
    assert BP_MODULE_UUID in uuids
    print(f"[OK] _extract_owned_uuids returns 5 UUIDs: {uuids}")

    none_result = _extract_owned_uuids(None, None)
    assert none_result == [], "None manifests should return empty list"
    print("[OK] _extract_owned_uuids handles None")

    print("[OK] test_extract_owned_uuids passed")


if __name__ == "__main__":
    test_detect()
    test_detect_dir()
    test_full_uuid_remap()
    test_server_mode()
    test_merge_pack_entries()
    test_sanitize_folder_name()
    test_extract_owned_uuids()
    print("\nAll tests passed!")
