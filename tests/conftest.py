"""Shared pytest fixtures."""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PIL import Image

from spotify_video_combiner.tracks import Track


def _tiny_image_bytes(fmt: str, color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format=fmt.upper().replace("JPG", "JPEG"))
    return buf.getvalue()


@dataclass
class TrackSpec:
    """In-memory description of a fake on-disk track for tests."""

    index: int
    spotify_id: str
    title: str
    artists: list[str]
    album: str = "An Album"
    artwork: bytes | None = None
    artwork_fmt: str = "jpeg"
    ext: str = "ogg"
    duration: float | None = None

    @property
    def filename(self) -> str:
        return f"{self.index:02d}.{self.spotify_id}.{self.ext}"


@pytest.fixture
def track_specs() -> list[TrackSpec]:
    return [
        TrackSpec(
            index=1,
            spotify_id="1aaaaaaaaaaaaaaaaaaaaa",
            title="First Song",
            artists=["Alpha", "Beta"],
            artwork=_tiny_image_bytes("jpeg", (200, 50, 50)),
        ),
        TrackSpec(
            index=2,
            spotify_id="2bbbbbbbbbbbbbbbbbbbbb",
            title="Second Song: A Subtitle",
            artists=["Gamma"],
            artwork=_tiny_image_bytes("png", (50, 200, 50)),
            artwork_fmt="png",
            album="Another Album",
        ),
    ]


@pytest.fixture
def sample_tracks(tmp_path: Path, track_specs: list[TrackSpec]) -> list[Track]:
    """A list of :class:`Track` objects pointing at real (empty) on-disk files.

    The audio files contain only zeros — they're enough for slide rendering
    and ffmpeg-stub tests, which never actually decode audio.
    """
    tracks: list[Track] = []
    for spec in track_specs:
        audio_path = tmp_path / spec.filename
        audio_path.write_bytes(b"\x00")
        cover_path = audio_path.with_name(f"{audio_path.stem}.cover.jpg")
        if spec.artwork:
            cover_path.write_bytes(spec.artwork)
        tracks.append(
            Track(
                index=spec.index,
                spotify_id=spec.spotify_id,
                audio_path=audio_path,
                title=spec.title,
                artists=list(spec.artists),
                album=spec.album,
                cover_path=cover_path if spec.artwork else None,
            )
        )
    return tracks


class FakeArtwork:
    """Stand-in for ``music_tag.file.Artwork`` with just the fields we read."""

    def __init__(self, raw: bytes, fmt: str = "jpeg") -> None:
        self.raw = raw
        self.fmt = fmt


class FakeMetadataItem:
    """Stand-in for ``music_tag.file.MetadataItem``."""

    def __init__(self, value: object | None = None) -> None:
        self.value = value
        self.first = value if isinstance(value, FakeArtwork) else None


class FakeTags:
    """Mapping interface mimicking what ``music_tag.load_file`` returns.

    Only ``__getitem__`` is needed since :func:`spotify_video_combiner.tracks.read_tracks`
    treats unknown tags as missing via ``KeyError``.
    """

    def __init__(self, **values: object) -> None:
        self._values = values

    def __getitem__(self, key: str) -> FakeMetadataItem:
        if key not in self._values:
            return FakeMetadataItem()
        return FakeMetadataItem(self._values[key])


@pytest.fixture
def fake_music_tag(monkeypatch: pytest.MonkeyPatch):
    """Patch ``music_tag.load_file`` with a path -> FakeTags lookup table."""
    table: dict[Path, FakeTags] = {}

    def _load(path: Path) -> FakeTags:
        try:
            return table[Path(path)]
        except KeyError as exc:
            raise OSError(f"unknown fake audio file: {path}") from exc

    monkeypatch.setattr("spotify_video_combiner.tracks.music_tag.load_file", _load)
    return table


@dataclass
class FakeWorkdir:
    """A populated workdir of fake audio files, ready for :func:`read_tracks`."""

    workdir: Path
    track_specs: list[TrackSpec]
    audio_paths: list[Path] = field(default_factory=list)


@pytest.fixture
def make_fake_workdir(tmp_path: Path, fake_music_tag: dict):
    """Factory that builds a workdir of zero-byte audio files + matching tags."""

    def _make(specs: Iterable[TrackSpec], subdir: str = "wd") -> FakeWorkdir:
        workdir = tmp_path / subdir
        workdir.mkdir(parents=True, exist_ok=True)
        spec_list = list(specs)
        paths: list[Path] = []
        for spec in spec_list:
            audio_path = workdir / spec.filename
            audio_path.write_bytes(b"\x00")
            paths.append(audio_path)
            tag_kwargs: dict[str, object] = {
                "title": spec.title,
                "artist": ", ".join(spec.artists),
                "album": spec.album,
            }
            if spec.artwork is not None:
                tag_kwargs["artwork"] = FakeArtwork(spec.artwork, spec.artwork_fmt)
            if spec.duration is not None:
                tag_kwargs["#length"] = spec.duration
            fake_music_tag[audio_path] = FakeTags(**tag_kwargs)
        return FakeWorkdir(workdir=workdir, track_specs=spec_list, audio_paths=paths)

    return _make


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


def cmd_to_str(cmd: Sequence[str]) -> str:
    """Helper for assertion error messages."""
    return " ".join(cmd)
