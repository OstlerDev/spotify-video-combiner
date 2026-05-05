"""Build the final MP4 by ffmpeg-encoding still+audio segments and concatenating them.

Two-phase approach:

1. For each track, encode a ``segment_NN.mp4`` with the slide PNG looped over
   the audio (``-loop 1 ... -shortest``). Every segment is produced with
   identical codec parameters so the concat demuxer can stream-copy them.
2. ``ffmpeg -f concat -c copy`` stitches all segments into the final MP4.
   Stream-copy keeps concatenation fast (no re-encode) and lossless.

The output is YouTube/VRChat-friendly: H.264 + AAC, yuv420p, faststart.

Encoder settings are tuned for the fact that every frame within a segment is
**byte-identical** (a single still image looped over the audio). At 30 fps a
4-minute song is 7,200 identical frames; the encoder spends most of its time
re-discovering that nothing changed. Two cheap defaults make this 5-10x
faster with no perceptible quality loss: a low output framerate and a
``veryfast`` preset. See :class:`EncodeSettings` for the rationale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .bundled import resolve_binary
from .errors import UserFacingError
from .processes import LogFn, SubprocessRunner, make_runner


class FFmpegError(UserFacingError):
    """Raised when the ffmpeg binary is missing or fails."""


@dataclass(frozen=True)
class EncodeSettings:
    """Encoder knobs. Defaults are tuned for static-image content (slideshows).

    Each segment is one cover-art slide looped over a track's audio, so every
    frame in the encoded video is identical. That lets us safely lean on:

    - ``video_preset='veryfast'`` — there is nothing to predict between
      identical frames, so a faster preset costs no quality but cuts encode
      time roughly in half versus ``medium``.
    - ``fps=2`` — drops a 4-minute track from 7,200 frames to 480 with no
      visual difference. Compatible with every YouTube/VRChat player tested.
    - ``keyframe_interval`` controls ``-g``; default of 0 means "let ffmpeg
      decide" (it picks ``2*fps`` for libx264, which is fine here).
    """

    width: int = 1920
    height: int = 1080
    fps: int = 2
    keyframe_interval: int = 0
    video_codec: str = "libx264"
    video_preset: str = "veryfast"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 44100


@dataclass(frozen=True)
class Segment:
    """One slide+audio pair to encode.

    ``audio_duration`` (seconds) bounds the looped still image to the audio
    length. Without it ffmpeg's ``-shortest`` plus ``-loop 1`` can leave up
    to ~30 s of silent video tail at low fps (ffmpeg trac #2622): the image
    demuxer pre-buffers frames before noticing the audio EOF, and those
    buffered frames get muxed out as silence between songs.
    """

    image_path: Path
    audio_path: Path
    output_path: Path
    audio_duration: float | None = None


class FFmpegVideoBuilder:
    """Build per-track segments and concatenate them into a single MP4.

    Pass ``log=<callable>`` to stream ffmpeg's stdout/stderr line-by-line into
    a callback (used by the GUI). Without ``log`` the child inherits stdio,
    which is the right default for terminal use. Either way, the popup
    console window that windowed PyInstaller bundles otherwise spawn for
    every ffmpeg call is suppressed on Windows.
    """

    def __init__(
        self,
        executable: str = "ffmpeg",
        settings: EncodeSettings | None = None,
        runner: SubprocessRunner | None = None,
        log: LogFn | None = None,
    ) -> None:
        self._executable = executable
        self._resolved: str | None = None
        self.settings = settings or EncodeSettings()
        self._runner = runner or make_runner(log, check=True)

    def ensure_available(self) -> str:
        """Resolve and memoise the absolute path to the ffmpeg binary.

        Frozen ``svc-gui.exe`` builds ship ffmpeg under ``<_MEIPASS>/binaries``
        which is *not* on the user's ``PATH``, so command builders must use
        the resolved absolute path -- spawning the bare name ``"ffmpeg"`` would
        leave Windows' ``CreateProcess`` to search ``PATH`` and fail with
        ``[WinError 2]`` on machines without a system ffmpeg.
        """
        if self._resolved is None:
            resolved = resolve_binary(self._executable)
            if resolved is None:
                raise FFmpegError(
                    f"`{self._executable}` is not available. Install ffmpeg from https://ffmpeg.org/ "
                    "and make sure it is on your PATH (or use the bundled installer/.exe build)."
                )
            self._resolved = resolved
        return self._resolved

    # --- segment encode -------------------------------------------------

    def build_segment_command(self, segment: Segment) -> list[str]:
        s = self.settings
        loop_input: list[str] = ["-loop", "1", "-framerate", str(s.fps)]
        if segment.audio_duration is not None:
            # Bound the looped image to the audio length so ``-shortest`` has a
            # finite video stream to compare against (see Segment docstring).
            loop_input += ["-t", f"{segment.audio_duration:.6f}"]
        cmd = [
            self._resolved or self._executable,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-stats",
            *loop_input,
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
        ]
        if s.keyframe_interval > 0:
            cmd.extend(["-g", str(s.keyframe_interval)])
        cmd.append(str(segment.output_path))
        return cmd

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
            self._resolved or self._executable,
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
