"""Load Spotify Web API credentials from a friendly ``credentials.env`` file.

Two locations are searched (in priority order):

1. ``./credentials.env`` in the current working directory — for project-local
   overrides.
2. ``<user-config-dir>/spotify-video-combiner/credentials.env`` — the global
   per-user file. This is created automatically as a blank template the first
   time credentials are missing, so the user can edit it instead of futzing
   with shell environment variables.

Real environment variables still take precedence over both files (see
``spotify.py``), so CI and one-off overrides keep working.

The file format is the obvious ``KEY=VALUE`` style. Comments (``#``) and blank
lines are ignored. Values may be optionally double- or single-quoted. There is
deliberately no dependency on ``python-dotenv``: the parser is ~10 lines and
keeps install footprint minimal.
"""

from __future__ import annotations

import os
from pathlib import Path

CREDENTIALS_FILENAME = "credentials.env"
APP_NAME = "spotify-video-combiner"

CREDENTIALS_TEMPLATE = """\
# Spotify Web API credentials for spotify-video-combiner.
#
# This file is used for reading playlist metadata only. Audio downloads go
# through zotify, which has its own login flow (run `zotify --help` once).
#
# To populate it:
#   1. Go to https://developer.spotify.com/dashboard and create a free app.
#   2. Copy the Client ID and Client Secret from the app's settings.
#   3. Paste them after the `=` signs below (no quotes needed).
#   4. Save and re-run your `svc` command.

SPOTIPY_CLIENT_ID=
SPOTIPY_CLIENT_SECRET=
"""


def user_config_dir() -> Path:
    """Cross-platform per-user config directory for this app.

    Windows: ``%APPDATA%\\spotify-video-combiner``
    macOS:   ``~/Library/Application Support/spotify-video-combiner``
    Linux:   ``$XDG_CONFIG_HOME/spotify-video-combiner`` or ``~/.config/spotify-video-combiner``
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def credentials_search_paths() -> list[Path]:
    """Files to search for credentials, in priority order (highest first)."""
    return [Path.cwd() / CREDENTIALS_FILENAME, user_config_dir() / CREDENTIALS_FILENAME]


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` file. Comments (``#``) and blanks are ignored.

    Surrounding single/double quotes around values are stripped; nothing fancier
    (no shell interpolation, no escapes) so behaviour is predictable.
    """
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_credentials_files() -> dict[str, str]:
    """Merge values from the candidate files; earlier paths win on conflicts."""
    merged: dict[str, str] = {}
    for path in credentials_search_paths():
        if not path.is_file():
            continue
        for key, value in parse_env_file(path).items():
            # Earlier path wins; later files only fill in missing keys.
            merged.setdefault(key, value)
    return merged


def ensure_template_exists(path: Path | None = None) -> Path:
    """Write a blank credentials template if none exists. Returns its path."""
    target = path or (user_config_dir() / CREDENTIALS_FILENAME)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CREDENTIALS_TEMPLATE, encoding="utf-8")
    return target
