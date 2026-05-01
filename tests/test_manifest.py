from __future__ import annotations

from pathlib import Path

import pytest

from spotify_video_combiner.manifest import (
    Playlist,
    Track,
    manifest_path_for,
    safe_filename,
)


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
        t = Track(1, "id", "url", "Song", ["A", "B"], "Album", 1000)
        assert t.artists_joined == "A, B"

    def test_slug_is_safe_and_indexed(self) -> None:
        t = Track(3, "id", "url", "Track / With Slash", ["Artist"], "Album", 1000)
        assert t.slug == "03 - Artist - Track _ With Slash"


class TestPlaylistRoundTrip:
    def test_write_and_read(self, sample_playlist: Playlist, tmp_path: Path) -> None:
        manifest = manifest_path_for(tmp_path)
        sample_playlist.write(manifest)

        loaded = Playlist.read(manifest)
        assert loaded.name == sample_playlist.name
        assert loaded.spotify_id == sample_playlist.spotify_id
        assert len(loaded.tracks) == len(sample_playlist.tracks)
        assert loaded.tracks[0].name == sample_playlist.tracks[0].name
        assert loaded.tracks[1].artists == sample_playlist.tracks[1].artists

    def test_unsupported_schema_raises(self, sample_playlist: Playlist, tmp_path: Path) -> None:
        manifest = manifest_path_for(tmp_path)
        sample_playlist.schema_version = 999
        sample_playlist.write(manifest)

        with pytest.raises(ValueError, match="schema version"):
            Playlist.read(manifest)

    def test_creates_parent_directory(self, sample_playlist: Playlist, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested"
        sample_playlist.write(manifest_path_for(nested))
        assert manifest_path_for(nested).is_file()
