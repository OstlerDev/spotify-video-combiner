# PyInstaller spec for the spotify-video-combiner GUI bundle.
#
# Produces a single windowed .exe at dist/svc-gui.exe that contains:
#   - The Tkinter GUI (svc-gui's entry point)
#   - All Python deps (zotify, librespot, spotipy, Pillow, click, requests, ...)
#   - ffmpeg.exe staged at runtime under sys._MEIPASS/binaries/ffmpeg.exe
#
# Build by running build_exe.ps1, which downloads a fresh ffmpeg static build
# into build/ffmpeg.exe and then invokes pyinstaller against this spec.
#
# Notes on tricky packages:
#   - zotify is a Python package (no separate exe needed); we re-enter our own
#     bundle via the --zotify-mode flag (see cli.main). We also use it as a
#     library for the in-app sign-in flow and metadata reads.
#   - librespot has a few non-importable runtime imports; we use --collect-all.
#   - protobuf < 4 (pinned by zotify) ships a `_internal_create_key` symbol
#     PyInstaller doesn't see by default.

# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
project_root = Path.cwd()
ffmpeg_path = project_root / "build" / "ffmpeg.exe"

binaries = []
if ffmpeg_path.is_file():
    binaries.append((str(ffmpeg_path), "binaries"))

datas = []
hiddenimports = collect_submodules("zotify") + collect_submodules("librespot")

for pkg in ("zotify", "librespot", "music_tag"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports


a = Analysis(
    [str(project_root / "src" / "spotify_video_combiner" / "__main_gui__.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_cov"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="svc-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app (no console flash on launch)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
