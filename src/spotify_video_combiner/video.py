"""Build the final MP4 by ffmpeg-encoding still+audio segments and concatenating them.

Two-phase approach:

1. For each track, encode a ``segment_NN.mp4`` with the slide PNG looped over
   the audio (``-loop 1 ... -shortest``). Every segment is produced with
   identical codec parameters so the concat demuxer can stream-copy them.
2. ``ffmpeg -f concat -c copy`` stitches all segments into the final MP4.
   Stream-copy keeps concatenation fast (no re-encode) and lossless.

The output is YouTube/VRChat-friendly: H.264 + AAC, yuv420p, faststart.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .bundled import resolve_binary
from .errors import UserFacingError

SubprocessRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=True)


class FFmpegError(UserFacingError):
    """Raised when the ffmpeg binary is missing or fails."""


@dataclass(frozen=True)
class EncodeSettings:
    """Encoder knobs. Defaults are tuned for YouTube upload of static-image video."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "libx264"
    video_preset: str = "medium"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 44100


@dataclass(frozen=True)
class Segment:
    """One slide+audio pair to encode."""

    image_path: Path
    audio_path: Path
    output_path: Path


class FFmpegVideoBuilder:
    """Build per-track segments and concatenate them into a single MP4."""

    def __init__(
        self,
        executable: str = "ffmpeg",
        settings: EncodeSettings | None = None,
        runner: SubprocessRunner | None = None,
    ) -> None:
        self._executable = executable
        self.settings = settings or EncodeSettings()
        self._runner = runner or _default_runner

    def ensure_available(self) -> str:
        resolved = resolve_binary(self._executable)
        if resolved is None:
            raise FFmpegError(
                f"`{self._executable}` is not available. Install ffmpeg from https://ffmpeg.org/ "
                "and make sure it is on your PATH (or use the bundled installer/.exe build)."
            )
        return resolved

    # --- segment encode -------------------------------------------------

    def build_segment_command(self, segment: Segment) -> list[str]:
        s = self.settings
        return [
            self._executable,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-stats",
            "-loop", "1",
            "-framerate", str(s.fps),
            "-i", str(segment.image_path),
            "-i", str(segment.audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", s.video_codec,
            "-preset", s.video_preset,
            "-tune", "stillimage",
            "-pix_fmt", s.pixel_format,
            "-r", str(s.fps),
            "-vf", f"scale={s.width}:{s.height}:force_original_aspect_ratio=decrease,"
                   f"pad={s.width}:{s.height}:-1:-1:color=black,setsar=1",
            "-c:a", s.audio_codec,
            "-b:a", s.audio_bitrate,
            "-ar", str(s.audio_sample_rate),
            "-ac", "2",
            "-shortest",
            "-movflags", "+faststart",
            str(segment.output_path),
        ]

    def encode_segment(self, segment: Segment) -> Path:
        self.ensure_available()
        segment.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._runner(self.build_segment_command(segment))
        return segment.output_path

    def encode_segments(self, segments: Sequence[Segment]) -> list[Path]:
        return [self.encode_segment(s) for s in segments]

    # --- concat ---------------------------------------------------------

    def build_concat_command(self, list_file: Path, output: Path) -> list[str]:
        return [
            self._executable,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-stats",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output),
        ]

    def concat(self, segments: Sequence[Path], output: Path) -> Path:
        """Concatenate already-encoded segments into ``output`` without re-encoding."""
        self.ensure_available()
        if not segments:
            raise FFmpegError("Cannot concatenate: no segments provided.")
        output.parent.mkdir(parents=True, exist_ok=True)
        list_file = output.with_suffix(".concat.txt")
        list_file.write_text(_render_concat_list(segments), encoding="utf-8")
        try:
            self._runner(self.build_concat_command(list_file, output))
        finally:
            list_file.unlink(missing_ok=True)
        return output


def _render_concat_list(segments: Sequence[Path]) -> str:
    """ffmpeg concat demuxer needs ``file '<path>'`` lines with single quotes escaped."""
    lines = []
    for path in segments:
        absolute = path.resolve().as_posix()
        # ffmpeg's concat demuxer escapes single quotes via the awkward `'\''` dance.
        escaped = absolute.replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"
