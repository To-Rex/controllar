# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

hiddenimports = [
    "zeroconf.asyncio",
    "qrcode",
    "qrcode.image.pil",
    "PIL.Image",
    "zxingcpp",
    "cryptography.hazmat.bindings._rust",
    "torexlink",
]
hiddenimports += collect_submodules("zeroconf")

if IS_MAC:
    hiddenimports += [
        "objc",
        "Quartz",
        "AppKit",
        "ApplicationServices",
        "CoreFoundation",
        "Foundation",
    ]

datas = [
    (str(ROOT / "torexlink" / "resources"), "torexlink/resources"),
]

excludes = [
    "tkinter",
    "pytest",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

SIGNING_IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None
icon = None
if IS_MAC:
    icon = str(ROOT / "packaging" / "assets" / "ToRexLink.icns")
elif IS_WINDOWS:
    icon = str(ROOT / "packaging" / "assets" / "ToRexLink.ico")

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ToRexLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=SIGNING_IDENTITY,
    entitlements_file=str(ROOT / "packaging" / "entitlements.plist"),
    icon=icon,
    version=str(ROOT / "packaging" / "version_info.txt") if IS_WINDOWS else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ToRexLink",
)

if IS_MAC:
    with (ROOT / "packaging" / "Info.plist").open("rb") as plist_handle:
        info_plist = plistlib.load(plist_handle)
    app = BUNDLE(
        coll,
        name="ToRexLink.app",
        icon=str(ROOT / "packaging" / "assets" / "ToRexLink.icns"),
        bundle_identifier="uz.torexlink.app",
        version="1.0.7",
        info_plist=info_plist,
    )
