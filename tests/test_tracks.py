from __future__ import annotations

from pathlib import Path

import pytest

from spotify_video_combiner.tracks import (
    Track,
    find_audio_files,
    read_tracks,
    safe_filename,
)

from .conftest import FakeArtwork, FakeTags, FakeWorkdir, TrackSpec


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("simple", "simple"),
            ("with spaces", "with spaces"),
            ("a/b\\c:d", "a_b_c_d"),
            ("trim trailing dots...", "trim trailing dots"),
            ("    ", "untitled"),
            ("", "untitled"),
            ("multi\nline\ttext", "multi line text"),
            ("control\x01char", "control_char"),
        ],
    )
    def test_sanitises(self, raw: str, expected: str) -> None:
        assert safe_filename(raw) == expected

    def test_truncates(self) -> None:
        result = safe_filename("x" * 500, max_len=50)
        assert len(result) == 50
        assert result == "x" * 50


class TestTrack:
    def test_artists_joined(self) -> None:
        t = Track(1, "id", Path("x.ogg"), "Song", ["A", "B"], "Album")
        assert t.artists_joined == "A, B"

    def test_slug_is_safe_and_indexed(self) -> None:
        t = Track(3, "id", Path("x.ogg"), "Track / With Slash", ["Artist"], "Album")
        assert t.slug == "03 - Artist - Track _ With Slash"


class TestFindAudioFiles:
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        assert find_audio_files(tmp_path / "nope") == []

    def test_filters_to_known_audio_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "01.aaaaaaaaaaaaaaaaaaaaaa.ogg").write_bytes(b"")
        (tmp_path / "02.bbbbbbbbbbbbbbbbbbbbbb.txt").write_bytes(b"")
        (tmp_path / "03.cccccccccccccccccccccc.mp3").write_bytes(b"")
        result = find_audio_files(tmp_path)
        assert [p.suffix for p in result] == [".ogg", ".mp3"]

    def test_sorted_by_playlist_index(self, tmp_path: Path) -> None:
        # Create out of order; expect sorted ascending.
        for i in (5, 1, 10, 2):
            (tmp_path / f"{i:02d}.{'x' * 22}.ogg").write_bytes(b"")
        result = find_audio_files(tmp_path)
        names = [p.stem.split(".", 1)[0] for p in result]
        assert names == ["01", "02", "05", "10"]

    def test_skips_files_not_matching_pattern(self, tmp_path: Path) -> None:
        # Wrong shape: missing index, non-Spotify-id, etc.
        (tmp_path / "no-index.ogg").write_bytes(b"")
        (tmp_path / "01.shortid.ogg").write_bytes(b"")
        (tmp_path / "01.aaaaaaaaaaaaaaaaaaaaaa.ogg").write_bytes(b"")
        result = find_audio_files(tmp_path)
        assert len(result) == 1


class TestReadTracks:
    def test_empty_workdir_returns_empty(self, tmp_path: Path) -> None:
        assert read_tracks(tmp_path) == []

    def test_reads_tags_in_playlist_order(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs)
        tracks = read_tracks(fw.workdir)
        assert [t.index for t in tracks] == [s.index for s in track_specs]
        assert [t.title for t in tracks] == [s.title for s in track_specs]
        assert tracks[0].artists == ["Alpha", "Beta"]
        assert tracks[1].artists == ["Gamma"]

    def test_extracts_cover_to_sibling_file(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs)
        tracks = read_tracks(fw.workdir)
        # First track was tagged as JPEG, second as PNG.
        assert tracks[0].cover_path is not None
        assert tracks[0].cover_path.suffix == ".jpg"
        assert tracks[0].cover_path.read_bytes() == track_specs[0].artwork
        assert tracks[1].cover_path is not None
        assert tracks[1].cover_path.suffix == ".png"
        assert tracks[1].cover_path.read_bytes() == track_specs[1].artwork

    def test_reuses_existing_cover_file(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs[:1])
        # Pre-create a cover file; read_tracks should not overwrite or re-extract.
        audio_path = fw.audio_paths[0]
        existing = audio_path.with_name(f"{audio_path.stem}.cover.webp")
        existing.write_bytes(b"already-here")
        tracks = read_tracks(fw.workdir)
        assert tracks[0].cover_path == existing
        assert existing.read_bytes() == b"already-here"

    def test_track_without_artwork_has_no_cover_path(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        spec = track_specs[0]
        spec.artwork = None
        fw: FakeWorkdir = make_fake_workdir([spec])
        tracks = read_tracks(fw.workdir)
        assert tracks[0].cover_path is None

    def test_reads_duration_from_length_tag(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        track_specs[0].duration = 123.45
        fw: FakeWorkdir = make_fake_workdir(track_specs)
        tracks = read_tracks(fw.workdir)
        assert tracks[0].duration == pytest.approx(123.45)
        assert tracks[1].duration is None  # no #length tag set

    def test_falls_back_when_tags_missing(
        self,
        tmp_path: Path,
        fake_music_tag: dict,
    ) -> None:
        path = tmp_path / "07.ffffffffffffffffffffff.ogg"
        path.write_bytes(b"\x00")
        # No title/artist/album tags configured.
        fake_music_tag[path] = FakeTags()
        tracks = read_tracks(tmp_path)
        assert len(tracks) == 1
        assert tracks[0].title == path.stem
        assert tracks[0].artists == ["Unknown Artist"]


class TestArtworkExtensionMapping:
    @pytest.mark.parametrize(
        ("fmt", "expected_ext"),
        [("jpeg", "jpg"), ("jpg", "jpg"), ("png", "png"), ("webp", "webp")],
    )
    def test_format_maps_to_filename_ext(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
        fmt: str,
        expected_ext: str,
    ) -> None:
        spec = track_specs[0]
        spec.artwork = b"img"
        spec.artwork_fmt = fmt
        fw: FakeWorkdir = make_fake_workdir([spec])
        tracks = read_tracks(fw.workdir)
        assert tracks[0].cover_path is not None
        assert tracks[0].cover_path.suffix == f".{expected_ext}"

    def test_unknown_artwork_format_does_not_crash(
        self,
        track_specs: list[TrackSpec],
        make_fake_workdir,
    ) -> None:
        spec = track_specs[0]
        spec.artwork = b"img"
        spec.artwork_fmt = "tiff"  # not in our mapping
        fw: FakeWorkdir = make_fake_workdir([spec])
        # Should still extract a cover, just with the raw fmt as ext.
        tracks = read_tracks(fw.workdir)
        assert tracks[0].cover_path is not None
        assert tracks[0].cover_path.read_bytes() == b"img"


class TestArtworkRawFallback:
    def test_artwork_object_without_raw_attribute_treated_as_missing(
        self,
        tmp_path: Path,
        fake_music_tag: dict,
    ) -> None:
        path = tmp_path / "01.eeeeeeeeeeeeeeeeeeeeee.ogg"
        path.write_bytes(b"\x00")
        # FakeArtwork with empty bytes -> _ensure_cover_extracted returns None.
        fake_music_tag[path] = FakeTags(
            title="t",
            artist="a",
            album="al",
            artwork=FakeArtwork(b"", "jpeg"),
        )
        tracks = read_tracks(tmp_path)
        assert tracks[0].cover_path is None
