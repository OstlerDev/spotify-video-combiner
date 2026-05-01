"""Playlist manifest: the on-disk record of what was downloaded.

The manifest lives at ``<workdir>/playlist.json`` and is the single source of
truth shared between the ``download`` and ``build`` subcommands. Paths inside
the manifest are stored relative to the manifest file so working directories
stay portable between machines.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MANIFEST_FILENAME = "playlist.json"
SCHEMA_VERSION = 1

_SAFE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name: str, *, max_len: int = 80) -> str:
    """Return ``name`` stripped of characters illegal in Windows/POSIX filenames.

    Whitespace (including tabs/newlines) is collapsed to single spaces *first*
    so filenames stay human-readable; then illegal/control characters are
    replaced with underscores. The result is length-bounded so paths don't
    blow past Windows' 260-char limit when combined with parent directories.
    """
    normalised = re.sub(r"\s+", " ", name)
    cleaned = _SAFE_NAME_RE.sub("_", normalised).strip(" .") or "untitled"
    return cleaned[:max_len].rstrip(" .") or "untitled"


@dataclass
class Track:
    """One row in a playlist."""

    index: int  # 1-based playlist position
    spotify_id: str
    spotify_url: str
    name: str
    artists: list[str]
    album: str
    duration_ms: int
    cover_url: str | None = None  # highest-res image URL from Spotify
    audio_path: str | None = None  # relative to manifest
    cover_path: str | None = None  # relative to manifest

    @property
    def artists_joined(self) -> str:
        return ", ".join(self.artists)

    @property
    def slug(self) -> str:
        """Stable, filesystem-safe stem like ``01 - Artist - Title``."""
        return safe_filename(f"{self.index:02d} - {self.artists_joined} - {self.name}")


@dataclass
class Playlist:
    """A downloaded playlist plus its tracks."""

    spotify_id: str
    spotify_url: str
    name: str
    owner: str
    description: str = ""
    tracks: list[Track] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Playlist:
        version = data.get("schema_version", 1)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported manifest schema version {version} (expected {SCHEMA_VERSION})"
            )
        tracks = [Track(**t) for t in data.get("tracks", [])]
        return cls(
            spotify_id=data["spotify_id"],
            spotify_url=data["spotify_url"],
            name=data["name"],
            owner=data["owner"],
            description=data.get("description", ""),
            tracks=tracks,
            schema_version=version,
        )

    def write(self, manifest_path: Path) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def read(cls, manifest_path: Path) -> Playlist:
        return cls.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))


def manifest_path_for(workdir: Path) -> Path:
    return workdir / MANIFEST_FILENAME
