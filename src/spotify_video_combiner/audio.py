"""Subprocess wrapper around ``zotify`` that downloads a whole playlist.

The actual logic of "scan a playlist, download every track, embed title /
artist / album / cover-art metadata, skip already-downloaded files" lives in
``zotify`` — we just hand it the playlist URL and a workdir and let it run.
The result on disk is a flat directory of audio files named
``<NN>.<spotify_id>.<ext>`` with everything we need embedded as tags;
:mod:`spotify_video_combiner.tracks` reads them back out.

Why subprocess? Because ``zotify``'s CLI surface (``--library``, ``--output``,
``--audio-format``) is the part of zotify that's stable across versions. Its
internal ``App.download_all`` is a moving target. Sign-in still happens
in-process via :mod:`spotify_video_combiner.auth` so by the time we shell out
the credentials file already exists and zotify never has to prompt.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .bundled import ZOTIFY_PROXY_FLAG, is_frozen, resolve_binary
from .errors import UserFacingError
from .installer import InstallError, can_auto_install, install_zotify
from .processes import SubprocessRunner, make_runner

# Output template for ``--output``. ``{playlist_number}`` is zotify's
# zero-padded playlist position, ``{spotid}`` is the 22-char Spotify ID.
# Files therefore land flat in the workdir as ``01.<spotid>.<ext>``,
# ``02.<spotid>.<ext>``, ... — alphabetical sort yields playlist order, and
# the filename alone tells us the index and the Spotify ID.
OUTPUT_TEMPLATE = "{playlist_number}.{spotid}"


class ZotifyError(UserFacingError):
    """Raised for unrecoverable zotify problems (missing binary, bad config)."""


class ZotifyDownloader:
    """Resolve and invoke zotify on a playlist URL.

    In a normal venv install, ``zotify.exe`` lives on ``PATH`` (a declared
    dependency) and we subprocess it directly. If it isn't there yet, we
    silently ``pip install`` it on first use.

    In a PyInstaller-frozen build there is no separate ``zotify.exe``: zotify
    is bundled as a Python package, and we re-enter our own ``.exe`` with a
    ``--zotify-mode`` flag that runs zotify's entry point in-process.
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
        self._log = log or (lambda _: None)
        # check=False: zotify may exit non-zero when a single track fails;
        # we detect outcomes by inspecting the filesystem afterwards.
        self._runner = runner or make_runner(log, check=False)

    def ensure_available(self) -> str:
        """Return the invocation prefix for zotify, auto-installing if needed."""
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

    def build_command(self, playlist_url: str, workdir: Path) -> list[str]:
        """Construct the zotify command line. Pure function for ease of testing."""
        prefix: Sequence[str] = (
            [sys.executable, ZOTIFY_PROXY_FLAG]
            if is_frozen()
            else [self._executable]
        )
        return [
            *prefix,
            playlist_url,
            "--library", str(workdir),
            "--output", OUTPUT_TEMPLATE,
            # Defaults we want to be explicit about:
            "--save-metadata",     # title/artist/album/cover-art into the audio file
            "--no-playlist-file",  # we don't need the .m3u8 sidecar
            *self._extra_args,
        ]

    def download(self, playlist_url: str, workdir: Path) -> int:
        """Run zotify on ``playlist_url``, dropping audio files into ``workdir``.

        Returns zotify's exit code. Re-runs are cheap because zotify's own
        ``--skip-previous`` logic (default-on) detects already-downloaded
        tracks via embedded ``spotid`` metadata and skips them.
        """
        self.ensure_available()
        workdir.mkdir(parents=True, exist_ok=True)
        result = self._runner(self.build_command(playlist_url, workdir))
        if result.returncode != 0:
            self._log(
                f"warning: zotify exited with code {result.returncode}. "
                "Some tracks may have failed to download. See log above for details."
            )
        return result.returncode
