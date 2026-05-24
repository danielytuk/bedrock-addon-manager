import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import detect_packs, process_addon, format_bytes
from core import AddonItem

RP_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BP_UUID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

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
        "modules": [{"type": "resources", "uuid": "a0000000-0000-4000-8000-000000000000", "version": [1, 0, 0]}],
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
        "modules": [{"type": "data", "uuid": "b0000000-0000-4000-8000-000000000000", "version": [1, 0, 0]}],
    }
    en_us_lang = "pack.name=My Cool Pack\npack.description=An awesome test pack\n"

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("resources/manifest.json", json.dumps(manifest_rp, indent=2))
        zf.writestr("data/manifest.json", json.dumps(manifest_bp, indent=2))
        zf.writestr("texts/en_US.lang", en_us_lang)
        zf.writestr("resources/textures/test.png", b"fake png data")
        zf.writestr("resources/textures/test2.png", b"fake png data 2")

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
    print("[OK] detect_packs")

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

    out_dir = tmp / "output"
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
    print("\nAll tests passed!")

if __name__ == "__main__":
    test_detect()
