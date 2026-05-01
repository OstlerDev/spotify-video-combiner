"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from PIL import Image

from spotify_video_combiner.manifest import Playlist, Track


@pytest.fixture
def sample_tracks() -> list[Track]:
    return [
        Track(
            index=1,
            spotify_id="track1",
            spotify_url="https://open.spotify.com/track/track1",
            name="First Song",
            artists=["Alpha", "Beta"],
            album="An Album",
            duration_ms=125_000,
            cover_url="https://i.scdn.co/image/abc.jpg",
        ),
        Track(
            index=2,
            spotify_id="track2",
            spotify_url="https://open.spotify.com/track/track2",
            name="Second Song: A Subtitle",
            artists=["Gamma"],
            album="Another Album",
            duration_ms=200_000,
            cover_url="https://i.scdn.co/image/def.png",
        ),
    ]


@pytest.fixture
def sample_playlist(sample_tracks: list[Track]) -> Playlist:
    return Playlist(
        spotify_id="abc123",
        spotify_url="https://open.spotify.com/playlist/abc123",
        name="Test Playlist!",
        owner="me",
        description="for testing",
        tracks=sample_tracks,
    )


@pytest.fixture
def make_image(tmp_path: Path):
    """Factory that writes a small solid-color PNG and returns its path."""

    def _make(name: str = "img.png", size: tuple[int, int] = (200, 200), color: tuple[int, int, int] = (200, 50, 50)) -> Path:
        path = tmp_path / name
        Image.new("RGB", size, color).save(path)
        return path

    return _make


@pytest.fixture
def make_audio(tmp_path: Path):
    """Factory that writes an empty placeholder audio file (used as a path-only stand-in)."""

    def _make(name: str, *, content: bytes = b"\x00") -> Path:
        path = tmp_path / name
        path.write_bytes(content)
        return path

    return _make


def cmd_to_str(cmd: Iterable[str]) -> str:
    """Helper for assertion error messages."""
    return " ".join(cmd)
