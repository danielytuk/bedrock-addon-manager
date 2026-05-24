# Bedrock Addon Manager

A desktop tool for Minecraft: Bedrock Edition addon creators and server admins. Import, patch, compress, and export addons with fresh UUIDs — ready for world import without conflicts.

## Features

- **Drag-and-drop** import of `.mcaddon`, `.mcpack`, `.zip` files
- **Auto-detects** Resource Packs (RP) and Behavior Packs (BP) from manifest content and folder structure
- **Re-UUIDs** all packs to prevent UUID collisions
- **Patches** names/descriptions (appends " - Patch" / "(patched)")
- **Cross-links dependencies** so RP and BP reference each other
- **Media compression** — optimizes images (PNG/JPEG/WebP via Pillow) and audio (OGG/Vorbis via FFmpeg)
- **Exports** patched archives and generates `world_resource_packs.json` / `world_behavior_packs.json`
- **Dark & light themes** (Qt Material)
- **Simple GUI** — no command-line fiddling

## Requirements

- Python 3.11+
- FFmpeg (optional — for audio compression; auto-bundled via `imageio-ffmpeg`)

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

1. Drop addon files onto the dashed zone, or click to browse
2. Review detected packs (RP/BP indicators, file sizes)
3. Choose an output folder (defaults to `Desktop/BedrockOutput`)
4. Click **Export & Patch All**
5. The patched `.mcaddon`/`.mcpack`/`.zip` files and JSON world-pack manifests appear in the output folder

## Build executable

```bash
pip install pyinstaller
pyinstaller build.spec
```

The standalone `.exe` will be at `dist/BedrockAddonManager_<version>.exe`.

## Test

```bash
python tests/test_pipeline.py
```

## License

MIT

---

This project is a 50/50 collaboration between human creativity and AI implementation. The initial concept, requirements, and rough functionality are created by a human developer, then refined and polished by AI to ensure smooth functionality, proper error handling, and production-ready code quality.