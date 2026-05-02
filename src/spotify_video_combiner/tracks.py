"""Read what zotify wrote to disk and turn it into ordered :class:`Track` records.

After ``zotify`` finishes, the workdir contains one audio file per playlist
track (named ``<NN>.<spotify_id>.<ext>`` thanks to our ``--output`` template)
with the title, artist, album, and cover art embedded as metadata. This
module's job is the small one of pulling those tags back out and exposing them
as a flat list of :class:`Track` objects in playlist order.

We deliberately do **not** keep a separate ``playlist.json`` manifest: the
audio files themselves are the source of truth, and they are bit-for-bit
compatible with what ``zotify`` produces standalone. Cover art is extracted
from each audio file to a sibling ``<stem>.cover.<ext>`` so :mod:`slides` can
hand a path to Pillow without having to learn about audio containers.

Tag reading goes through `music_tag`, which is already a transitive dep of
zotify and gives us a single ``f["title"] / f["artist"] / f["album"] /
f["artwork"]`` interface across mp3/ogg/m4a/flac/... — we don't have to care
which format the user picked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import music_tag

KNOWN_AUDIO_EXTS = ("ogg", "mp3", "m4a", "opus", "aac", "flac", "wav")
"""Extensions zotify can produce (see ``zotify.utils.AudioFormat``)."""

# Audio filenames look like ``<NN>.<22-char-spotify-id>.<ext>``. The leading
# zero-padded number is what gives us playlist order via plain alphabetical
# sort, so we extract it for tie-breaking when ``Path.name`` order isn't
# stable enough (mixed widths shouldn't happen, but be defensive).
_FILENAME_RE = re.compile(r"^(?P<index>\d+)\.(?P<spotid>[A-Za-z0-9]{22})\.(?P<ext>\w+)$")

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


@dataclass(frozen=True)
class Track:
    """One playlist track, in memory only — no JSON serialization."""

    index: int  # 1-based playlist position
    spotify_id: str
    audio_path: Path
    title: str
    artists: list[str]
    album: str
    cover_path: Path | None = None

    @property
    def artists_joined(self) -> str:
        return ", ".join(self.artists)

    @property
    def slug(self) -> str:
        """Stable, filesystem-safe stem like ``01 - Artist - Title``."""
        return safe_filename(f"{self.index:02d} - {self.artists_joined} - {self.title}")


def find_audio_files(workdir: Path) -> list[Path]:
    """Return zotify-produced audio files in playlist order (sorted by ``NN``)."""
    if not workdir.is_dir():
        return []
    candidates: list[tuple[int, Path]] = []
    for entry in workdir.iterdir():
        if not entry.is_file() or entry.suffix.lstrip(".").lower() not in KNOWN_AUDIO_EXTS:
            continue
        match = _FILENAME_RE.match(entry.name)
        if match:
            candidates.append((int(match.group("index")), entry))
    candidates.sort(key=lambda pair: pair[0])
    return [path for _, path in candidates]


def read_tracks(workdir: Path) -> list[Track]:
    """Walk ``workdir``, read tags, and emit playlist-ordered tracks.

    Cover art embedded in each audio file is extracted to a sibling
    ``<stem>.cover.<ext>`` if it isn't already present. Tracks missing both
    the file and any extractable artwork still appear in the result with
    ``cover_path=None`` so the slide renderer can fall back to a plain
    background.
    """
    tracks: list[Track] = []
    for audio_path in find_audio_files(workdir):
        match = _FILENAME_RE.match(audio_path.name)
        if not match:  # pragma: no cover - filtered out by find_audio_files
            continue
        index = int(match.group("index"))
        spotid = match.group("spotid")

        title, artists, album = _read_text_tags(audio_path)
        cover_path = _ensure_cover_extracted(audio_path)

        tracks.append(
            Track(
                index=index,
                spotify_id=spotid,
                audio_path=audio_path,
                title=title or audio_path.stem,
                artists=artists or ["Unknown Artist"],
                album=album,
                cover_path=cover_path,
            )
        )
    return tracks


def _read_text_tags(audio_path: Path) -> tuple[str, list[str], str]:
    """Return ``(title, artists, album)`` from an audio file's embedded tags."""
    try:
        tags = music_tag.load_file(audio_path)
    except (OSError, ValueError):  # pragma: no cover - corrupt file
        return ("", [], "")
    title = _string_tag(tags, "title")
    raw_artists = _string_tag(tags, "artist")
    artists = [a.strip() for a in raw_artists.split(",") if a.strip()] if raw_artists else []
    album = _string_tag(tags, "album")
    return title, artists, album


def _string_tag(tags: object, name: str) -> str:
    try:
        value = tags[name].value  # type: ignore[index]
    except (KeyError, AttributeError):
        return ""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value)


def _ensure_cover_extracted(audio_path: Path) -> Path | None:
    """Extract the embedded cover art to a sibling file (idempotent).

    The cover lives at ``<audio_stem>.cover.<ext>`` next to the audio file.
    Returns the path on success, or ``None`` if the audio has no embedded
    artwork (in which case the slide renderer falls back to a plain bg).
    """
    existing = _find_existing_cover(audio_path)
    if existing is not None:
        return existing

    try:
        tags = music_tag.load_file(audio_path)
        artwork = tags["artwork"].first  # music_tag's Artwork object or None
    except (KeyError, OSError, ValueError, AttributeError):
        return None
    if artwork is None or not getattr(artwork, "raw", None):
        return None

    fmt = (getattr(artwork, "fmt", None) or "jpeg").lower()
    ext = "jpg" if fmt in ("jpeg", "jpg") else fmt
    cover_path = audio_path.with_name(f"{audio_path.stem}.cover.{ext}")
    cover_path.write_bytes(artwork.raw)
    return cover_path


def _find_existing_cover(audio_path: Path) -> Path | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = audio_path.with_name(f"{audio_path.stem}.cover.{ext}")
        if candidate.is_file():
            return candidate
    return None
