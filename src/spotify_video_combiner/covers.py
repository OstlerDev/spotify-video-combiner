"""Download cover-art images for tracks via plain HTTP."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import requests

from .manifest import Track

_DEFAULT_TIMEOUT = 30
_CHUNK = 64 * 1024

# Tests inject a fake fetcher to avoid network calls.
Fetcher = Callable[[str], bytes]


def _http_fetcher(url: str) -> bytes:
    response = requests.get(url, timeout=_DEFAULT_TIMEOUT, stream=True)
    response.raise_for_status()
    chunks = bytearray()
    for chunk in response.iter_content(_CHUNK):
        chunks.extend(chunk)
    return bytes(chunks)


def _ext_from_url(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower().lstrip(".")
    return suffix if suffix in {"jpg", "jpeg", "png", "webp"} else "jpg"


def download_covers(
    tracks: Sequence[Track],
    dest_dir: Path,
    fetcher: Fetcher | None = None,
) -> dict[str, Path]:
    """Download cover art for each track and return ``{spotify_id: path}``.

    Files are named ``<spotify_id>.<ext>`` so they are easy to map back to
    tracks and to skip on subsequent runs. Tracks with no ``cover_url`` are
    silently omitted from the result.
    """
    fetch = fetcher or _http_fetcher
    dest_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    for track in tracks:
        if not track.cover_url:
            continue
        existing = _find_existing(dest_dir, track.spotify_id)
        if existing is not None:
            results[track.spotify_id] = existing
            continue
        ext = _ext_from_url(track.cover_url)
        path = dest_dir / f"{track.spotify_id}.{ext}"
        path.write_bytes(fetch(track.cover_url))
        results[track.spotify_id] = path
    return results


def _find_existing(dest_dir: Path, spotify_id: str) -> Path | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = dest_dir / f"{spotify_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None
