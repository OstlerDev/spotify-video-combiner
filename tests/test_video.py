from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from spotify_video_combiner.video import (
    EncodeSettings,
    FFmpegError,
    FFmpegVideoBuilder,
    Segment,
    _render_concat_list,
)


class FakeRunner:
    def __init__(self, *, write_outputs: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._write = write_outputs

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess:
        cmd_list = list(cmd)
        self.calls.append(cmd_list)
        if self._write and cmd_list:
            # Last positional arg is always the output path in our commands.
            output = Path(cmd_list[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(cmd_list, returncode=0)


class TestRenderConcatList:
    def test_uses_forward_slash_absolute_paths(self, tmp_path: Path) -> None:
        seg1 = tmp_path / "a.mp4"
        seg2 = tmp_path / "b.mp4"
        seg1.write_bytes(b"x")
        seg2.write_bytes(b"x")

        rendered = _render_concat_list([seg1, seg2])
        lines = rendered.strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert line.startswith("file '")
            assert line.endswith(".mp4'")
            # Even on Windows we render with forward slashes to satisfy ffmpeg.
            assert "\\" not in line

    def test_escapes_single_quotes(self, tmp_path: Path) -> None:
        sub = tmp_path / "wei'rd"
        sub.mkdir()
        seg = sub / "x.mp4"
        seg.write_bytes(b"x")

        rendered = _render_concat_list([seg])
        assert r"'\''" in rendered


class TestFFmpegVideoBuilder:
    def test_ensure_available_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.video.resolve_binary", lambda _: None)
        with pytest.raises(FFmpegError, match="not available"):
            FFmpegVideoBuilder().ensure_available()

    def test_segment_command_includes_essentials(self, tmp_path: Path) -> None:
        seg = Segment(tmp_path / "i.png", tmp_path / "a.ogg", tmp_path / "o.mp4")
        builder = FFmpegVideoBuilder(settings=EncodeSettings(width=1280, height=720, fps=24))

        cmd = builder.build_segment_command(seg)

        assert cmd[0] == "ffmpeg"
        assert "-loop" in cmd and cmd[cmd.index("-loop") + 1] == "1"
        assert "-shortest" in cmd
        assert "-tune" in cmd and cmd[cmd.index("-tune") + 1] == "stillimage"
        assert str(seg.image_path) in cmd
        assert str(seg.audio_path) in cmd
        assert cmd[-1] == str(seg.output_path)
        # The custom resolution propagates into the scale/pad filter.
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1280:720" in vf
        assert "pad=1280:720" in vf

    def test_concat_command_uses_concat_demuxer_and_stream_copy(self, tmp_path: Path) -> None:
        builder = FFmpegVideoBuilder()
        cmd = builder.build_concat_command(tmp_path / "list.txt", tmp_path / "out.mp4")

        assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
        assert "-safe" in cmd and cmd[cmd.index("-safe") + 1] == "0"
        assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
        assert cmd[-1] == str(tmp_path / "out.mp4")

    def test_encode_segment_invokes_runner_and_creates_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.video.resolve_binary", lambda _: "/fake/ffmpeg")
        runner = FakeRunner()
        builder = FFmpegVideoBuilder(runner=runner)
        out = tmp_path / "out.mp4"

        builder.encode_segment(Segment(tmp_path / "i.png", tmp_path / "a.ogg", out))

        assert len(runner.calls) == 1
        assert out.is_file()

    def test_concat_runs_ffmpeg_and_cleans_up_list_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.video.resolve_binary", lambda _: "/fake/ffmpeg")
        runner = FakeRunner()
        builder = FFmpegVideoBuilder(runner=runner)

        seg1 = tmp_path / "s1.mp4"
        seg2 = tmp_path / "s2.mp4"
        seg1.write_bytes(b"x")
        seg2.write_bytes(b"x")
        out = tmp_path / "final.mp4"

        builder.concat([seg1, seg2], out)

        assert len(runner.calls) == 1
        assert out.is_file()
        # The transient concat list file should be removed.
        assert not out.with_suffix(".concat.txt").exists()

    def test_concat_with_no_segments_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("spotify_video_combiner.video.resolve_binary", lambda _: "/fake/ffmpeg")
        builder = FFmpegVideoBuilder()
        with pytest.raises(FFmpegError, match="no segments"):
            builder.concat([], tmp_path / "out.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestFFmpegIntegration:
    """End-to-end exercise: produce a tiny 1-second clip from a still image + silent audio."""

    def test_real_segment_then_concat(self, tmp_path: Path) -> None:
        from PIL import Image

        image = tmp_path / "slide.png"
        Image.new("RGB", (320, 240), (40, 80, 120)).save(image)

        audio = tmp_path / "silent.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", str(audio),
            ],
            check=True,
        )

        builder = FFmpegVideoBuilder(
            settings=EncodeSettings(width=320, height=240, fps=15, audio_bitrate="64k")
        )
        seg1 = tmp_path / "seg1.mp4"
        seg2 = tmp_path / "seg2.mp4"
        builder.encode_segment(Segment(image, audio, seg1))
        builder.encode_segment(Segment(image, audio, seg2))
        assert seg1.is_file() and seg1.stat().st_size > 0
        assert seg2.is_file() and seg2.stat().st_size > 0

        out = tmp_path / "combined.mp4"
        builder.concat([seg1, seg2], out)
        assert out.is_file() and out.stat().st_size > 0
