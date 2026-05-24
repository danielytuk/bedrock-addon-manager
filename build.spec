# -*- mode: python ; coding: utf-8 -*-

import os
import sys
sys.path.insert(0, os.getcwd())
from version import VERSION

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('blue_bundle.ico', '.'),
    ],
    hiddenimports=[
        'PIL',
        'PIL._imaging',
        'PIL.Image',
        'qt_material',
        'qt_material.resources',
        'imageio_ffmpeg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6', 'PyQt5', 'PyQt6.QtCore', 'PyQt5.QtCore', 'PyQt6.QtWidgets', 'PyQt5.QtWidgets', 'PyQt6.QtGui', 'PyQt5.QtGui', 'tkinter'],
    noarchive=False,
    module_collection_mode={},
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f'BedrockAddonManager_{VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='blue_bundle.ico',
)
