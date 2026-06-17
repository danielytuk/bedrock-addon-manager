import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid as uuid_mod
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from imageio_ffmpeg import get_ffmpeg_exe

SUPPORTED_EXT = {".mcaddon", ".zip", ".mcpack"}
IMAGE_EXT = {".png", ".apng", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"}
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
RP_FOLDER_KEYWORDS = ["resource", "respack", "_rp", "/rp", "texture"]
BP_FOLDER_KEYWORDS = ["behavior", "behaviour", "behpack", "_bp", "/bp", "data", "script"]


@dataclass
class AddonItem:
    file_path: Path
    has_rp: bool = False
    has_bp: bool = False
    rp_manifest_path: str = ""
    bp_manifest_path: str = ""
    rp_original_uuid: str = ""
    bp_original_uuid: str = ""
    pack_name: str = ""
    pack_description: str = ""
    new_rp_uuid: str = ""
    new_bp_uuid: str = ""
    is_directory: bool = False

    @property
    def file_name(self) -> str:
        name = self.file_path.name
        if self.is_directory:
            name += " (folder)"
        return name

    @property
    def file_size(self) -> int:
        if self.is_directory:
            total = 0
            try:
                for f in self.file_path.rglob("*"):
                    if f.is_file():
                        total += f.stat().st_size
            except OSError:
                pass
            return total
        try:
            return self.file_path.stat().st_size
        except OSError:
            return 0

    @property
    def summary(self) -> str:
        parts = []
        if self.has_rp:
            parts.append("RP")
        if self.has_bp:
            parts.append("BP")
        return "+".join(parts) if parts else "\u2014"


def format_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def make_uuid() -> str:
    return str(uuid_mod.uuid4())


def get_ffmpeg_path():
    try:
        return Path(get_ffmpeg_exe())
    except Exception:
        return None


def _classify_manifest(manifest_path: str, manifest_data: dict) -> tuple[bool, bool]:
    module_types = [m.get("type", "").lower() for m in manifest_data.get("modules", [])]
    parent = manifest_path.replace("\\", "/").rsplit("/", 1)[0].lower() if "/" in manifest_path else ""
    is_rp = "resources" in module_types
    is_bp = any(t in module_types for t in ("data", "script", "client_data"))
    if not is_rp and not is_bp:
        is_rp = any(kw in parent for kw in RP_FOLDER_KEYWORDS)
        is_bp = any(kw in parent for kw in BP_FOLDER_KEYWORDS)
    return is_rp, is_bp


def _detect_packs_from_zip(zip_path: Path) -> dict:
    result: dict = {
        "has_rp": False, "has_bp": False,
        "rp_manifest_path": None, "bp_manifest_path": None,
        "rp_original_uuid": None, "bp_original_uuid": None,
        "pack_name": None, "pack_description": None,
    }
    if not zip_path.is_file() or zip_path.suffix.lower() not in SUPPORTED_EXT:
        return result
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n for n in zf.namelist() if not n.startswith("__MACOSX")]
            for mname in [n for n in names if n.lower().replace("\\", "/").endswith("manifest.json")]:
                try:
                    data = json.loads(zf.read(mname))
                except (json.JSONDecodeError, KeyError):
                    continue
                is_rp, is_bp = _classify_manifest(mname, data)
                uuid_val = (data.get("header", {}) or {}).get("uuid", "")
                if is_rp:
                    result.update(has_rp=True, rp_manifest_path=mname, rp_original_uuid=uuid_val)
                if is_bp:
                    result.update(has_bp=True, bp_manifest_path=mname, bp_original_uuid=uuid_val)
            for lp in [n for n in names if n.replace("\\", "/").lower().endswith("texts/en_us.lang")]:
                try:
                    for line in zf.read(lp).decode("utf-8-sig").splitlines():
                        line = line.strip()
                        if line.startswith("pack.name="):
                            result["pack_name"] = line.split("=", 1)[1].strip()
                        elif line.startswith("pack.description="):
                            result["pack_description"] = line.split("=", 1)[1].strip()
                except Exception:
                    continue
    except (zipfile.BadZipFile, OSError):
        pass
    return result


def _detect_packs_from_dir(source: Path) -> dict:
    result: dict = {
        "has_rp": False, "has_bp": False,
        "rp_manifest_path": None, "bp_manifest_path": None,
        "rp_original_uuid": None, "bp_original_uuid": None,
        "pack_name": None, "pack_description": None,
    }
    if not source.is_dir():
        return result
    try:
        for mpath in source.rglob("manifest.json"):
            if "__MACOSX" in mpath.parts:
                continue
            try:
                data = json.loads(mpath.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rel = mpath.relative_to(source).as_posix()
            is_rp, is_bp = _classify_manifest(rel, data)
            uuid_val = (data.get("header", {}) or {}).get("uuid", "")
            if is_rp:
                result.update(has_rp=True, rp_manifest_path=rel, rp_original_uuid=uuid_val)
            if is_bp:
                result.update(has_bp=True, bp_manifest_path=rel, bp_original_uuid=uuid_val)
        for lang_path in source.rglob("texts/en_US.lang"):
            if "__MACOSX" in lang_path.parts:
                continue
            try:
                for line in lang_path.read_text("utf-8-sig").splitlines():
                    line = line.strip()
                    if line.startswith("pack.name="):
                        result["pack_name"] = line.split("=", 1)[1].strip()
                    elif line.startswith("pack.description="):
                        result["pack_description"] = line.split("=", 1)[1].strip()
            except Exception:
                continue
    except OSError:
        pass
    return result


def detect_packs(source: Path) -> dict:
    if source.is_dir():
        return _detect_packs_from_dir(source)
    return _detect_packs_from_zip(source)


def _extract_owned_uuids(*manifest_dicts: dict | None) -> list[str]:
    uuids = []
    for manifest in manifest_dicts:
        if not manifest:
            continue
        header = manifest.get("header", {})
        if isinstance(header, dict) and UUID_RE.match(str(header.get("uuid", ""))):
            uuids.append(header["uuid"])
        for module in manifest.get("modules", []):
            if isinstance(module, dict) and UUID_RE.match(str(module.get("uuid", ""))):
                uuids.append(module["uuid"])
    return uuids


def _deep_replace_uuid(obj, uuid_map: dict[str, str]):
    if isinstance(obj, dict):
        for key, val in list(obj.items()):
            if isinstance(val, str) and UUID_RE.match(val) and val in uuid_map:
                obj[key] = uuid_map[val]
            else:
                _deep_replace_uuid(val, uuid_map)
    elif isinstance(obj, list):
        for item in obj:
            _deep_replace_uuid(item, uuid_map)


def _patch_manifest_header(manifest: dict, pack_name: str, pack_description: str):
    header = manifest.get("header")
    if not header:
        return
    base = pack_name or header.get("name", "")
    if isinstance(base, str):
        header["name"] = base if base.endswith(" - Patch") else base + " - Patch"
    desc = pack_description or header.get("description", "")
    if isinstance(desc, str):
        header["description"] = desc if desc.endswith(" (patched)") else desc + " (patched)"


def _ensure_dependencies(rp_json: dict, bp_json: dict, new_rp_uuid: str, new_bp_uuid: str):
    rp_deps = rp_json.setdefault("dependencies", [])
    if not any(d.get("uuid") == new_bp_uuid for d in rp_deps):
        rp_deps.append({"uuid": new_bp_uuid, "version": bp_json.get("header", {}).get("version", [1, 0, 0])})
    bp_deps = bp_json.setdefault("dependencies", [])
    if not any(d.get("uuid") == new_rp_uuid for d in bp_deps):
        bp_deps.append({"uuid": new_rp_uuid, "version": rp_json.get("header", {}).get("version", [1, 0, 0])})


def _compress_image_file(file_path: Path) -> tuple[int, int]:
    from PIL import Image
    original_size = file_path.stat().st_size
    ext = file_path.suffix.lower()
    try:
        img = Image.open(file_path)
        output = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
        if ext in (".png", ".apng"):
            img.save(output, format="PNG", optimize=True)
        elif ext == ".webp":
            img.save(output, format="WEBP", quality=80, optimize=True)
        else:
            (img.convert("RGB") if img.mode == "RGBA" else img).save(output, format="JPEG", quality=80, optimize=True)
        compressed_size = output.tell()
        if 0 < compressed_size < original_size:
            output.seek(0)
            file_path.write_bytes(output.read())
            return original_size, compressed_size
        return original_size, original_size
    except Exception:
        return original_size, original_size


def _compress_audio_file(file_path: Path, ffmpeg_path: Path) -> tuple[int, int]:
    original_size = file_path.stat().st_size
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "output.ogg"
        subprocess.run(
            [str(ffmpeg_path), "-i", str(file_path), "-c:a", "libvorbis", "-qscale:a", "5", "-y", str(out)],
            capture_output=True, timeout=180,
        )
        if out.is_file():
            compressed_size = out.stat().st_size
            if 0 < compressed_size < original_size:
                shutil.copy2(str(out), str(file_path))
                return original_size, compressed_size
    except Exception:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return original_size, original_size


def _sanitize_folder_name(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", name)
    safe = re.sub(r"\s+", "_", safe)
    return safe.strip("_") or "unnamed_pack"


def merge_pack_entries(existing: list[dict], new: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for entry in existing:
        pid = entry.get("pack_id")
        if pid:
            merged[pid] = entry
    for entry in new:
        pid = entry.get("pack_id")
        if pid:
            merged[pid] = entry
    return sorted(merged.values(), key=lambda e: e.get("pack_id", ""))


def _collect_media_files(tmp_root: Path):
    images = []
    audio = []
    all_files = []
    for f in sorted(tmp_root.rglob("*"), key=lambda p: str(p)):
        if not f.is_file():
            continue
        all_files.append(f)
        ext = f.suffix.lower()
        if ext in IMAGE_EXT:
            images.append(f)
        elif ext in AUDIO_EXT:
            audio.append(f)
    return all_files, images, audio


def process_addon(
    addon: AddonItem,
    output_dir: Path,
    on_progress: Callable[[int, str], None],
    cancel_flag: list[bool],
    ffmpeg_path: Path | None = None,
    server_mode: bool = False,
):
    if not addon.file_path.exists():
        raise FileNotFoundError(f"Source not found: {addon.file_path}")

    tmp_root = Path(tempfile.mkdtemp(prefix="bedrock_"))
    try:
        if addon.file_path.is_dir():
            on_progress(0, "Copying directory...")
            shutil.copytree(str(addon.file_path), str(tmp_root), dirs_exist_ok=True)
        else:
            on_progress(0, "Extracting archive...")
            with zipfile.ZipFile(addon.file_path, "r") as zf:
                zf.extractall(str(tmp_root))

        on_progress(15, "Reading manifests...")
        rp_json = bp_json = None
        rp_file_path = bp_file_path = None

        if addon.has_rp and addon.rp_manifest_path:
            rp_file_path = tmp_root / addon.rp_manifest_path.replace("\\", os.sep)
            if rp_file_path.is_file():
                rp_json = json.loads(rp_file_path.read_text("utf-8"))
        else:
            for c in tmp_root.rglob("manifest.json"):
                if _classify_manifest(str(c.relative_to(tmp_root)).replace(os.sep, "/"), json.loads(c.read_text("utf-8")))[0]:
                    rp_file_path = c
                    rp_json = json.loads(c.read_text("utf-8"))
                    break

        if addon.has_bp and addon.bp_manifest_path:
            bp_file_path = tmp_root / addon.bp_manifest_path.replace("\\", os.sep)
            if bp_file_path.is_file():
                bp_json = json.loads(bp_file_path.read_text("utf-8"))
        else:
            for c in tmp_root.rglob("manifest.json"):
                if _classify_manifest(str(c.relative_to(tmp_root)).replace(os.sep, "/"), json.loads(c.read_text("utf-8")))[1]:
                    bp_file_path = c
                    bp_json = json.loads(c.read_text("utf-8"))
                    break

        if cancel_flag[0]:
            return

        on_progress(25, "Generating new UUIDs...")
        owned_uuids = _extract_owned_uuids(rp_json, bp_json)
        uuid_map = {uid: make_uuid() for uid in owned_uuids}

        if rp_json is not None:
            _deep_replace_uuid(rp_json, uuid_map)
            _patch_manifest_header(rp_json, addon.pack_name, addon.pack_description)
        if bp_json is not None:
            _deep_replace_uuid(bp_json, uuid_map)
            _patch_manifest_header(bp_json, addon.pack_name, addon.pack_description)

        new_rp = uuid_map.get(addon.rp_original_uuid) if addon.rp_original_uuid else None
        new_bp = uuid_map.get(addon.bp_original_uuid) if addon.bp_original_uuid else None

        if rp_json is not None and bp_json is not None and new_rp and new_bp:
            _ensure_dependencies(rp_json, bp_json, new_rp, new_bp)

        if cancel_flag[0]:
            return

        if rp_json is not None and rp_file_path is not None:
            rp_file_path.write_text(json.dumps(rp_json, indent=2), "utf-8")
        if bp_json is not None and bp_file_path is not None:
            bp_file_path.write_text(json.dumps(bp_json, indent=2), "utf-8")

        addon.new_rp_uuid = new_rp or ""
        addon.new_bp_uuid = new_bp or ""

        if cancel_flag[0]:
            return

        on_progress(35, "Analyzing media files...")
        all_files, image_files, audio_files = _collect_media_files(tmp_root)
        ffmpeg_ok = ffmpeg_path is not None
        total_media = len(image_files) + (len(audio_files) if ffmpeg_ok else 0)
        processed_media = 0
        total_saved = 0
        media_lock = Lock()

        def _report_media(pct_base: int, pct_range: int, label: str):
            nonlocal processed_media
            with media_lock:
                processed_media += 1
                if total_media > 0:
                    pct = pct_base + int(pct_range * processed_media / total_media)
                    on_progress(min(pct, pct_base + pct_range), f"{label}: {processed_media}/{total_media}")

        on_progress(35, "Compressing images...")
        if image_files:
            def _compress_img(f: Path):
                if cancel_flag[0]:
                    return (0, 0)
                return _compress_image_file(f)

            with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() - 1)) as ex:
                fut_map = {ex.submit(_compress_img, f): f for f in image_files}
                for future in as_completed(fut_map):
                    if cancel_flag[0]:
                        break
                    orig, comp = future.result()
                    with media_lock:
                        total_saved += orig - comp
                    _report_media(35, 15, "Compressing images")

        if ffmpeg_ok and audio_files and not cancel_flag[0]:
            on_progress(50, "Compressing audio...")
            def _compress_aud(f: Path):
                if cancel_flag[0]:
                    return (0, 0)
                return _compress_audio_file(f, ffmpeg_path)

            with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() - 1)) as ex:
                fut_map = {ex.submit(_compress_aud, f): f for f in audio_files}
                for future in as_completed(fut_map):
                    if cancel_flag[0]:
                        break
                    orig, comp = future.result()
                    with media_lock:
                        total_saved += orig - comp
                    _report_media(50, 15, "Compressing audio")

        if cancel_flag[0]:
            return

        if server_mode:
            on_progress(70, "Deploying packs...")
            if rp_json is not None and rp_file_path is not None:
                pack_name = rp_json.get("header", {}).get("name", "resource_pack")
                folder = _sanitize_folder_name(pack_name)
                rp_root = rp_file_path.parent
                dest = output_dir / "resource_packs" / folder
                dest.mkdir(parents=True, exist_ok=True)
                for item in rp_root.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(rp_root)
                        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(item), str(dest / rel))
                on_progress(80, f"Deployed RP to resource_packs/{folder}")

            if bp_json is not None and bp_file_path is not None:
                pack_name = bp_json.get("header", {}).get("name", "behavior_pack")
                folder = _sanitize_folder_name(pack_name)
                bp_root = bp_file_path.parent
                dest = output_dir / "behavior_packs" / folder
                dest.mkdir(parents=True, exist_ok=True)
                for item in bp_root.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(bp_root)
                        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(item), str(dest / rel))
                on_progress(90, f"Deployed BP to behavior_packs/{folder}")
        else:
            on_progress(70, "Repacking archive...")
            stem = addon.file_path.stem
            ext = addon.file_path.suffix.lower() if not addon.is_directory else ".mcaddon"
            if addon.is_directory:
                ext = ".mcaddon"
                output_name = f"{stem}_patched{ext}"
            else:
                output_name = f"{stem}_patched{ext}" if not stem.endswith("_patched") else f"{stem}{ext}"
            output_path = output_dir / output_name
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for i, f in enumerate(all_files):
                    if f.is_file():
                        zf.write(str(f), str(f.relative_to(tmp_root).as_posix()))
                    if cancel_flag[0]:
                        return
                    if i % 50 == 0:
                        pct = 70 + int(25 * i / max(len(all_files), 1))
                        on_progress(min(pct, 95), f"Packing: {f.relative_to(tmp_root)}")

        if cancel_flag[0]:
            return

        on_progress(100, f"Done \u2014 saved {format_bytes(total_saved)}")

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
