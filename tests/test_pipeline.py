from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from spotify_video_combiner.audio import ZotifyDownloader
from spotify_video_combiner.manifest import Playlist, Track, manifest_path_for
from spotify_video_combiner.pipeline import (
    build_video,
    default_workdir,
    download_playlist,
)
from spotify_video_combiner.slides import SlideRenderer, SlideStyle
from spotify_video_combiner.video import FFmpegVideoBuilder, Segment


def _fake_audio_files_for(audio_dir: Path, tracks: Sequence[Track]) -> dict[str, Path]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for track in tracks:
        path = audio_dir / f"{track.spotify_id}.ogg"
        path.write_bytes(b"audio")
        paths[track.spotify_id] = path
    return paths


def _fake_cover_files_for(cover_dir: Path, tracks: Sequence[Track]) -> dict[str, Path]:
    cover_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for track in tracks:
        path = cover_dir / f"{track.spotify_id}.jpg"
        Image.new("RGB", (100, 100), (50, 50, 50)).save(path)
        paths[track.spotify_id] = path
    return paths


class TestDefaultWorkdir:
    def test_uses_safe_filename(self) -> None:
        result = default_workdir("My / Crazy: Playlist", root=Path("base"))
        assert result == Path("base") / "My _ Crazy_ Playlist"


class TestDownloadPlaylist:
    def test_writes_manifest_and_populates_relative_paths(
        self, sample_playlist: Playlist, tmp_path: Path
    ) -> None:
        metadata = MagicMock()
        metadata.fetch_playlist.return_value = sample_playlist

        downloader = MagicMock(spec=ZotifyDownloader)
        downloader.download_tracks.side_effect = lambda tracks, dest: _fake_audio_files_for(dest, tracks)

        def fake_cover_fetcher(tracks, dest):
            return _fake_cover_files_for(dest, tracks)

        playlist, workdir = download_playlist(
            "https://open.spotify.com/playlist/abc",
            workdir=tmp_path / "wd",
            metadata=metadata,
            downloader=downloader,
            cover_fetcher=fake_cover_fetcher,
        )

        assert workdir == tmp_path / "wd"
        manifest = manifest_path_for(workdir)
        assert manifest.is_file()

        loaded = Playlist.read(manifest)
        for track in loaded.tracks:
            assert track.audio_path is not None
            assert track.cover_path is not None
            # Paths must be relative + use forward slashes for portability.
            assert "\\" not in track.audio_path
            assert "\\" not in track.cover_path
            assert (workdir / track.audio_path).is_file()
            assert (workdir / track.cover_path).is_file()

        assert playlist.tracks[0].audio_path == "audio/track1.ogg"
        assert playlist.tracks[0].cover_path == "covers/track1.jpg"

    def test_warns_about_missing_audio(
        self, sample_playlist: Playlist, tmp_path: Path
    ) -> None:
        metadata = MagicMock()
        metadata.fetch_playlist.return_value = sample_playlist

        # Downloader returns nothing (simulate failed downloads).
        downloader = MagicMock(spec=ZotifyDownloader)
        downloader.download_tracks.return_value = {}

        messages: list[str] = []
        download_playlist(
            "url",
            workdir=tmp_path,
            metadata=metadata,
            downloader=downloader,
            cover_fetcher=lambda tracks, dest: {},
            log=messages.append,
        )

        joined = "\n".join(messages)
        assert "WARNING" in joined and "no audio" in joined

    def test_uses_default_workdir_when_not_specified(
        self, sample_playlist: Playlist, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        metadata = MagicMock()
        metadata.fetch_playlist.return_value = sample_playlist

        downloader = MagicMock(spec=ZotifyDownloader)
        downloader.download_tracks.side_effect = lambda tracks, dest: _fake_audio_files_for(dest, tracks)

        _, workdir = download_playlist(
            "url",
            metadata=metadata,
            downloader=downloader,
            cover_fetcher=lambda tracks, dest: _fake_cover_files_for(dest, tracks),
        )

        assert workdir == Path("output") / "Test Playlist!"


class TestBuildVideo:
    def _prepare_workdir(self, tmp_path: Path, sample_playlist: Playlist) -> Path:
        workdir = tmp_path / "wd"
        audio_dir = workdir / "audio"
        cover_dir = workdir / "covers"
        _fake_audio_files_for(audio_dir, sample_playlist.tracks)
        _fake_cover_files_for(cover_dir, sample_playlist.tracks)

        for track in sample_playlist.tracks:
            track.audio_path = f"audio/{track.spotify_id}.ogg"
            track.cover_path = f"covers/{track.spotify_id}.jpg"
        sample_playlist.write(manifest_path_for(workdir))
        return workdir

    def test_builds_one_segment_per_track_then_concats(
        self, sample_playlist: Playlist, tmp_path: Path
    ) -> None:
        workdir = self._prepare_workdir(tmp_path, sample_playlist)

        encoded: list[Segment] = []
        concatted: list[Sequence[Path]] = []

        renderer = SlideRenderer(style=SlideStyle(size=(320, 240)))
        builder = MagicMock(spec=FFmpegVideoBuilder)

        def fake_encode(segment: Segment) -> Path:
            encoded.append(segment)
            segment.output_path.parent.mkdir(parents=True, exist_ok=True)
            segment.output_path.write_bytes(b"mp4")
            return segment.output_path

        def fake_concat(segments: Sequence[Path], output: Path) -> Path:
            concatted.append(list(segments))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"final")
            return output

        builder.encode_segment.side_effect = fake_encode
        builder.concat.side_effect = fake_concat

        out = build_video(workdir, renderer=renderer, builder=builder)

        assert out.is_file()
        assert len(encoded) == len(sample_playlist.tracks)
        assert (workdir / "slides" / "001_track1.png").is_file()
        assert (workdir / "slides" / "002_track2.png").is_file()
        assert len(concatted) == 1
        assert len(concatted[0]) == len(sample_playlist.tracks)

    def test_skips_re_encoding_existing_segments(
        self, sample_playlist: Playlist, tmp_path: Path
    ) -> None:
        workdir = self._prepare_workdir(tmp_path, sample_playlist)

        # Pre-populate existing slides + segments to simulate a partial run.
        slides_dir = workdir / "slides"
        segments_dir = workdir / "segments"
        slides_dir.mkdir()
        segments_dir.mkdir()
        for track in sample_playlist.tracks:
            stem = f"{track.index:03d}_{track.spotify_id}"
            Image.new("RGB", (10, 10)).save(slides_dir / f"{stem}.png")
            (segments_dir / f"{stem}.mp4").write_bytes(b"cached")

        renderer_called = MagicMock()
        renderer = MagicMock(spec=SlideRenderer, render_to_file=renderer_called)
        builder = MagicMock(spec=FFmpegVideoBuilder)
        builder.concat.side_effect = lambda segs, out: (out.write_bytes(b"final"), out)[1]

        build_video(workdir, renderer=renderer, builder=builder)

        renderer_called.assert_not_called()
        builder.encode_segment.assert_not_called()
        builder.concat.assert_called_once()

    def test_raises_when_no_audio_anywhere(
        self, sample_playlist: Playlist, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "wd"
        for track in sample_playlist.tracks:
            track.audio_path = None
            track.cover_path = None
        sample_playlist.write(manifest_path_for(workdir))

        with pytest.raises(RuntimeError, match="No tracks to combine"):
            build_video(
                workdir,
                renderer=SlideRenderer(style=SlideStyle(size=(64, 48))),
                builder=MagicMock(spec=FFmpegVideoBuilder),
            )
