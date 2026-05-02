from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from spotify_video_combiner.audio import ZotifyDownloader
from spotify_video_combiner.pipeline import (
    PipelineError,
    build_video,
    default_workdir,
    download_playlist,
    parse_playlist_id,
)
from spotify_video_combiner.processes import LogChannels
from spotify_video_combiner.slides import SlideRenderer, SlideStyle
from spotify_video_combiner.video import FFmpegVideoBuilder, Segment

from .conftest import FakeWorkdir, TrackSpec


class TestParsePlaylistId:
    @pytest.mark.parametrize(
        "value",
        [
            "37i9dQZF1DXcBWIGoYBM5M",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "https://open.spotify.com/intl-pt/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
        ],
    )
    def test_extracts_id(self, value: str) -> None:
        assert parse_playlist_id(value) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_invalid_input_raises(self) -> None:
        with pytest.raises(PipelineError, match="Could not extract"):
            parse_playlist_id("not-a-url")


class TestDefaultWorkdir:
    def test_uses_safe_filename(self) -> None:
        result = default_workdir("My / Crazy: Playlist", root=Path("base"))
        assert result == Path("base") / "My _ Crazy_ Playlist"


class TestDownloadPlaylist:
    def test_returns_explicit_workdir(self, tmp_path: Path) -> None:
        downloader = MagicMock(spec=ZotifyDownloader)
        target = tmp_path / "explicit"
        result = download_playlist(
            "https://open.spotify.com/playlist/abc",
            workdir=target,
            downloader=downloader,
        )
        assert result == target
        downloader.download.assert_called_once_with(
            "https://open.spotify.com/playlist/abc", target
        )

    def test_default_workdir_uses_mercury_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "spotify_video_combiner.pipeline.lookup_playlist_name",
            lambda _id: "My Test Playlist!",
        )
        downloader = MagicMock(spec=ZotifyDownloader)
        result = download_playlist(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            downloader=downloader,
        )
        assert result == Path("output") / "My Test Playlist!"

    def test_default_workdir_falls_back_to_id_when_lookup_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "spotify_video_combiner.pipeline.lookup_playlist_name", lambda _id: None
        )
        downloader = MagicMock(spec=ZotifyDownloader)
        result = download_playlist(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            downloader=downloader,
        )
        assert result == Path("output") / "37i9dQZF1DXcBWIGoYBM5M"

    def test_pipeline_progress_uses_pipeline_channel_only(self, tmp_path: Path) -> None:
        pipeline_msgs: list[str] = []
        subprocess_msgs: list[str] = []
        downloader = MagicMock(spec=ZotifyDownloader)

        download_playlist(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            workdir=tmp_path / "wd",
            downloader=downloader,
            channels=LogChannels(
                pipeline=pipeline_msgs.append,
                subprocess=subprocess_msgs.append,
            ),
        )
        assert any("Downloading playlist" in m for m in pipeline_msgs)
        assert subprocess_msgs == []


class TestBuildVideo:
    def test_raises_when_no_audio_files(self, tmp_path: Path) -> None:
        empty = tmp_path / "wd"
        empty.mkdir()
        with pytest.raises(PipelineError, match="No audio files"):
            build_video(
                empty,
                renderer=SlideRenderer(style=SlideStyle(size=(64, 48))),
                builder=MagicMock(spec=FFmpegVideoBuilder),
            )

    def test_renders_slides_and_encodes_each_track(
        self, track_specs: list[TrackSpec], make_fake_workdir
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs)
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

        out = build_video(fw.workdir, renderer=renderer, builder=builder)

        assert out.is_file()
        assert len(encoded) == len(track_specs)
        # Slides exist on disk and are named with the playlist index + spotify id.
        slide_names = sorted(p.name for p in (fw.workdir / "slides").glob("*.png"))
        assert slide_names == [
            f"{spec.index:03d}_{spec.spotify_id}.png" for spec in track_specs
        ]
        assert len(concatted) == 1
        assert len(concatted[0]) == len(track_specs)

    def test_skips_re_encoding_existing_segments(
        self, track_specs: list[TrackSpec], make_fake_workdir
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs)
        slides_dir = fw.workdir / "slides"
        segments_dir = fw.workdir / "segments"
        slides_dir.mkdir()
        segments_dir.mkdir()
        for spec in track_specs:
            stem = f"{spec.index:03d}_{spec.spotify_id}"
            Image.new("RGB", (10, 10)).save(slides_dir / f"{stem}.png")
            (segments_dir / f"{stem}.mp4").write_bytes(b"cached")

        renderer_called = MagicMock()
        renderer = MagicMock(spec=SlideRenderer, render_to_file=renderer_called)
        builder = MagicMock(spec=FFmpegVideoBuilder)
        builder.concat.side_effect = lambda segs, out: (out.write_bytes(b"final"), out)[1]

        build_video(fw.workdir, renderer=renderer, builder=builder)

        renderer_called.assert_not_called()
        builder.encode_segment.assert_not_called()
        builder.concat.assert_called_once()

    def test_output_filename_defaults_to_workdir_basename(
        self, track_specs: list[TrackSpec], make_fake_workdir
    ) -> None:
        fw: FakeWorkdir = make_fake_workdir(track_specs, subdir="My Playlist!")
        builder = MagicMock(spec=FFmpegVideoBuilder)
        builder.encode_segment.side_effect = lambda seg: (
            seg.output_path.parent.mkdir(parents=True, exist_ok=True),
            seg.output_path.write_bytes(b"mp4"),
            seg.output_path,
        )[2]
        builder.concat.side_effect = lambda segs, out: (out.write_bytes(b"final"), out)[1]

        out = build_video(
            fw.workdir,
            renderer=SlideRenderer(style=SlideStyle(size=(64, 48))),
            builder=builder,
        )
        assert out.name == "My Playlist!.mp4"
