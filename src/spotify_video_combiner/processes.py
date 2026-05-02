"""Subprocess helpers tuned for windowed (no-console) GUI bundles.

Without these helpers, every ``ffmpeg`` and ``zotify`` invocation from the
frozen ``svc-gui.exe`` pops a fresh black console window that steals focus
for as long as the child runs. The helpers below:

1. Suppress that popup on Windows by passing ``CREATE_NO_WINDOW`` plus a
   hidden ``STARTUPINFO`` to every child. These flags are no-ops on
   non-Windows platforms.
2. Optionally stream stdout+stderr line-by-line into a ``log`` callback so
   the child's progress shows up live in the GUI's log widget instead of
   in a vanished popup terminal.

Tests inject their own runners via the existing ``runner=`` constructor
parameters on :class:`~spotify_video_combiner.audio.ZotifyDownloader` and
:class:`~spotify_video_combiner.video.FFmpegVideoBuilder`, so this module
is pure runtime plumbing — no test fixtures depend on it directly.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

LogFn = Callable[[str], None]
SubprocessRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _noop(_: str) -> None:  # pragma: no cover - trivial
    pass


@dataclass(frozen=True)
class LogChannels:
    """Two log streams: high-level pipeline progress and verbose subprocess output.

    The pipeline drives a small number of ``pipeline`` lines per run (one per
    significant step) while ffmpeg/zotify subprocess output goes to
    ``subprocess`` -- which can be very chatty (per-track progress bars,
    encoder stats). Splitting them lets the GUI keep the user-facing log
    clean while still surfacing live child-process output in a separate
    pane. The CLI uses ``LogChannels.single(...)`` to keep both streams in
    one place (the terminal).
    """

    pipeline: LogFn
    subprocess: LogFn

    @classmethod
    def silent(cls) -> LogChannels:
        return cls(_noop, _noop)

    @classmethod
    def single(cls, log: LogFn) -> LogChannels:
        return cls(log, log)


def _no_window_kwargs() -> dict:
    """Return ``subprocess`` kwargs that suppress the console window on Windows.

    Combines ``CREATE_NO_WINDOW`` (which prevents the OS from auto-allocating
    a console for a windowed parent) with ``STARTUPINFO.SW_HIDE`` (which
    hides any console the child tries to create itself). On non-Windows this
    returns an empty dict, so callers can spread it unconditionally.
    """
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def run_inherit(cmd: Sequence[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """Run ``cmd`` with stdio inherited from the parent (CLI default)."""
    return subprocess.run(list(cmd), check=check, **_no_window_kwargs())


def run_streaming(
    cmd: Sequence[str], *, log: LogFn, check: bool = False
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with stdout+stderr streamed line-by-line into ``log``.

    Each line is forwarded as soon as the child flushes it, giving live
    feedback in the GUI even for long-running encoders. Carriage-return-only
    progress redraws (tqdm spinners, ffmpeg ``-stats``) arrive as separate
    lines because Python's text mode treats ``\\r`` as a newline; we strip
    ``\\r``/``\\n`` and skip empties so the GUI text widget stays readable.
    """
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **_no_window_kwargs(),
    )
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n").strip()
            if line:
                log(line)
    finally:
        proc.wait()
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, list(cmd))
    return subprocess.CompletedProcess(list(cmd), proc.returncode)


def make_runner(log: LogFn | None = None, *, check: bool = False) -> SubprocessRunner:
    """Pick the right runner for the current execution context.

    - With ``log=None``, child stdio is inherited (so CLI users see ffmpeg
      progress in their terminal).
    - With a callable ``log``, child stdio is captured and streamed into it
      (for the GUI, where there's no console to inherit).

    Either runner hides the console window on Windows.
    """
    if log is None:
        return lambda cmd: run_inherit(cmd, check=check)
    return lambda cmd: run_streaming(cmd, log=log, check=check)
