"""Wrap the ``zotify`` CLI to download per-track audio.

We deliberately invoke zotify as a subprocess rather than importing it: zotify
is a CLI-first tool whose internal API is unstable across versions/forks, but
its CLI surface is stable enough to depend on.

DraftKinner's fork uses ``--library <root>`` for the destination directory and
``--output <template>`` where ``{spotid}`` expands to the Spotify track ID.
The file extension is appended automatically based on ``--audio-format``, so
the output template should not include ``.{ext}``. Setting the template to
``{spotid}`` gives us files at ``<library>/<spotify_id>.<ext>`` — exactly what
the rest of the pipeline expects to find.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .bundled import ZOTIFY_PROXY_FLAG, is_frozen, resolve_binary
from .errors import UserFacingError
from .installer import InstallError, can_auto_install, install_zotify
from .manifest import Track

# Audio formats zotify can produce. Used to discover already-downloaded files.
KNOWN_AUDIO_EXTS = ("ogg", "mp3", "m4a", "opus", "aac", "flac", "wav")

# Default subprocess runner. Tests inject a fake to assert command construction
# without spawning real processes.
SubprocessRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    # check=False: zotify may exit non-zero when a single track fails; we
    # detect outcomes by inspecting the filesystem instead.
    #
    # When running from a PyInstaller GUI bundle on Windows, our parent
    # process has no console — but zotify needs one for first-time login
    # prompts (and benefits from one for live progress output). Spawn it
    # in a fresh console window so the user can interact with it.
    kwargs: dict = {}
    if is_frozen() and sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    return subprocess.run(list(cmd), check=False, **kwargs)


class ZotifyError(UserFacingError):
    """Raised for unrecoverable zotify problems (missing binary, no downloads)."""


class ZotifyDownloader:
    """Resolve and invoke zotify, transparently handling dev vs frozen builds.

    In a normal venv install, ``zotify.exe`` lives on ``PATH`` (installed as a
    declared dependency) and we subprocess it directly. If it isn't there yet,
    we silently ``pip install`` it on first use.

    In a PyInstaller-frozen build there is no separate ``zotify.exe``: zotify
    is bundled as a Python package, and we re-enter our own ``.exe`` with a
    ``--zotify-mode`` flag that runs zotify's entry point in-process.

    Parameters
    ----------
    executable:
        Name or path of the zotify binary. Defaults to looking up ``zotify``
        anywhere we know to look (bundled binary, then PATH).
    extra_args:
        Additional flags forwarded verbatim to every invocation (e.g.
        ``["--audio-format=mp3"]`` to override the default OGG/Vorbis output).
    runner:
        Subprocess runner; injected by tests.
    """

    def __init__(
        self,
        executable: str = "zotify",
        extra_args: Iterable[str] | None = None,
        runner: SubprocessRunner | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._executable = executable
        self._extra_args = list(extra_args or [])
        self._runner = runner or _default_runner
        self._log = log or (lambda _: None)

    def ensure_available(self) -> str:
        """Return the invocation prefix for zotify.

        For a frozen build this is a sentinel constant (the actual command is
        constructed by :meth:`build_command`). For dev installs this is the
        absolute path to ``zotify.exe`` — auto-installed via pip on first use
        if not already present.
        """
        if is_frozen():
            return "frozen://zotify"

        resolved = resolve_binary(self._executable)
        if resolved is not None:
            return resolved

        if can_auto_install():
            try:
                install_zotify(log=self._log, runner=self._runner)
            except InstallError as exc:
                raise ZotifyError(str(exc)) from exc
            resolved = resolve_binary(self._executable)
            if resolved is not None:
                return resolved

        raise ZotifyError(
            f"`{self._executable}` is not available and could not be auto-installed.\n"
            "Install it manually and re-run:\n"
            "  pipx install git+https://github.com/DraftKinner/zotify.git"
        )

    def build_command(self, urls: Sequence[str], dest_dir: Path) -> list[str]:
        """Construct the zotify command line. Pure function for ease of testing.

        In a frozen build the command re-enters this same ``.exe`` with the
        proxy flag instead of invoking a separate ``zotify.exe``.
        """
        prefix = (
            [sys.executable, ZOTIFY_PROXY_FLAG]
            if is_frozen()
            else [self._executable]
        )
        return [
            *prefix,
            *urls,
            "--library",
            str(dest_dir),
            "--output",
            "{spotid}",
            *self._extra_args,
        ]

    def download_tracks(
        self, tracks: Sequence[Track], dest_dir: Path
    ) -> dict[str, Path]:
        """Download any tracks not already present and return ``{spotify_id: path}``.

        Skips tracks already present on disk so re-runs are cheap. Raises
        :class:`ZotifyError` if zotify is missing; per-track download failures
        are reported via missing entries in the returned dict (callers decide
        whether to retry or abort).
        """
        self.ensure_available()
        dest_dir.mkdir(parents=True, exist_ok=True)

        existing = {t.spotify_id: p for t in tracks if (p := find_existing_audio(dest_dir, t.spotify_id))}
        pending = [t for t in tracks if t.spotify_id not in existing]

        if pending:
            cmd = self.build_command([t.spotify_url for t in pending], dest_dir)
            result = self._runner(cmd)
            if result.returncode != 0:
                self._log(
                    f"warning: zotify exited with code {result.returncode}. "
                    "Some tracks may have failed to download. "
                    "If running from the GUI, check the console window that opened "
                    "during the download for details."
                )

        results: dict[str, Path] = dict(existing)
        for track in pending:
            path = find_existing_audio(dest_dir, track.spotify_id)
            if path is not None:
                results[track.spotify_id] = path
        return results


def find_existing_audio(dest_dir: Path, spotify_id: str) -> Path | None:
    """Return the on-disk audio file for ``spotify_id`` if zotify already produced one."""
    for ext in KNOWN_AUDIO_EXTS:
        candidate = dest_dir / f"{spotify_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None
