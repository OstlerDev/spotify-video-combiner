"""Helpers for locating binaries that may be bundled inside a PyInstaller ``.exe``.

When packaged with ``pyinstaller --onefile``, external tools like ``ffmpeg``
and ``zotify`` get extracted at runtime into ``sys._MEIPASS``. We look there
first, then fall back to PATH (the developer's normal venv install).

Frozen-mode detection also drives whether ``ZotifyDownloader`` can subprocess
``zotify.exe`` directly (in dev) or whether it must re-enter our own ``.exe``
with the magic ``--zotify-mode`` flag (in a frozen build, where there's no
separate ``zotify.exe``).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ZOTIFY_PROXY_FLAG = "--zotify-mode"
"""Flag the frozen ``.exe`` recognises to run as a zotify shim. See ``cli.main``."""

BUNDLED_BIN_SUBDIR = "binaries"
"""Sub-directory inside ``sys._MEIPASS`` where we stash bundled ``.exe`` files."""


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return getattr(sys, "frozen", False)


def bundled_resource_root() -> Path | None:
    """Return the PyInstaller temp-extraction root, or None when not frozen."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def find_bundled_binary(name: str) -> str | None:
    """Look for ``<name>(.exe)?`` inside the PyInstaller bundle, if any.

    Returns the absolute path on hit, otherwise ``None``. Callers typically fall
    back to ``shutil.which(name)`` for the dev/venv case.
    """
    root = bundled_resource_root()
    if root is None:
        return None
    for candidate in (root / BUNDLED_BIN_SUBDIR / name, root / BUNDLED_BIN_SUBDIR / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_binary(name: str) -> str | None:
    """Find ``name`` as a bundled binary first, then anywhere on ``PATH``."""
    return find_bundled_binary(name) or shutil.which(name)
