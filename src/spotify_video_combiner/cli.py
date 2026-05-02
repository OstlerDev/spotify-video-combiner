"""Click CLI: thin adapters around the pipeline functions."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import click

from . import __version__
from .auth import (
    credentials_path,
    current_username,
    is_signed_in,
    sign_in,
    sign_out,
)
from .bundled import ZOTIFY_PROXY_FLAG
from .errors import AuthError, UserFacingError
from .pipeline import build_video, download_playlist
from .processes import LogChannels
from .slides import SlideRenderer

_workdir_opt = click.option(
    "--workdir",
    "-w",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Working directory. Defaults to ./output/<playlist-name>/.",
)
_zotify_opt = click.option(
    "--zotify-arg",
    "zotify_extra",
    multiple=True,
    metavar="ARG",
    help="Extra argument forwarded to zotify (repeatable, e.g. `--zotify-arg=--download-format=mp3`).",
)
_font_opt = click.option(
    "--font",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="Path to a TTF/OTF font for slide text (auto-detected if omitted).",
)
_output_opt = click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output MP4 path. Defaults to <workdir>/<playlist-name>.mp4.",
)


def _click_log(message: str) -> None:
    click.echo(message)


def _click_channels() -> LogChannels:
    """CLI sends both pipeline and subprocess output to the same stdout."""
    return LogChannels.single(_click_log)


def _require_sign_in() -> None:
    if not is_signed_in():
        raise AuthError(
            "Not signed in to Spotify. Run `svc signin` first."
        )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="spotify-video-combiner")
def cli() -> None:
    """Combine a Spotify playlist into a single MP4 (audio + cover-art slideshow)."""


@cli.command()
@click.option(
    "--username",
    default=None,
    help="Spotify username (usually your email). Prompted if omitted.",
)
def signin(username: str | None) -> None:
    """Authorise this app to read your Spotify playlists and download audio."""
    if is_signed_in():
        click.echo(f"Already signed in as {current_username() or 'Spotify'}.")
        return
    user = (username or click.prompt("Spotify username")).strip()

    def show_url(url: str) -> None:
        click.echo(f"Open this URL in your browser to sign in:\n  {url}\n")
        webbrowser.open(url)

    click.echo("Waiting for authorisation...")
    sign_in(user, show_url)
    click.secho(f"Signed in as {user}.", fg="green")


@cli.command()
def signout() -> None:
    """Forget the cached Spotify session."""
    if sign_out():
        click.echo(f"Removed {credentials_path()}.")
    else:
        click.echo("Not signed in; nothing to do.")


@cli.command()
@click.argument("playlist")
@_workdir_opt
@_zotify_opt
def download(playlist: str, workdir: Path | None, zotify_extra: tuple[str, ...]) -> None:
    """Download audio + cover art for PLAYLIST (URL, URI, or ID) into a working folder."""
    _require_sign_in()
    download_playlist(
        playlist,
        workdir=workdir,
        zotify_extra=zotify_extra,
        channels=_click_channels(),
    )


@cli.command()
@click.argument(
    "workdir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
)
@_output_opt
@_font_opt
def build(workdir: Path, output: Path | None, font: Path | None) -> None:
    """Build the MP4 from a previously-downloaded WORKDIR."""
    renderer = SlideRenderer(font_path=str(font) if font else None)
    build_video(workdir, output=output, renderer=renderer, channels=_click_channels())


@cli.command(name="all")
@click.argument("playlist")
@_workdir_opt
@_output_opt
@_font_opt
@_zotify_opt
def all_cmd(
    playlist: str,
    workdir: Path | None,
    output: Path | None,
    font: Path | None,
    zotify_extra: tuple[str, ...],
) -> None:
    """Run download then build in one shot."""
    _require_sign_in()
    channels = _click_channels()
    resolved_workdir = download_playlist(
        playlist,
        workdir=workdir,
        zotify_extra=zotify_extra,
        channels=channels,
    )
    renderer = SlideRenderer(font_path=str(font) if font else None)
    build_video(resolved_workdir, output=output, renderer=renderer, channels=channels)


def _maybe_run_as_zotify() -> bool:
    """In a frozen ``.exe``, intercept the ``--zotify-mode`` flag and run zotify in-process.

    Returns True if we ran as zotify (caller should exit), False otherwise.
    The flag is the second argv element when invoked as a zotify proxy:
    ``svc.exe --zotify-mode <url> ...`` becomes ``zotify <url> ...``.

    Sign-in always happens in our own GUI/CLI before any download, so by the
    time zotify runs ``credentials.json`` already exists and zotify never
    needs an interactive TTY -- the bundled proxy can stay completely
    headless.
    """
    if ZOTIFY_PROXY_FLAG not in sys.argv:
        return False
    _bind_repl_exit_builtins()
    idx = sys.argv.index(ZOTIFY_PROXY_FLAG)
    sys.argv = ["zotify", *sys.argv[idx + 1 :]]
    from zotify.__main__ import main as zotify_main

    zotify_main()
    return True


def _bind_repl_exit_builtins() -> None:
    """Re-add ``exit`` and ``quit`` to ``builtins`` before invoking zotify.

    Zotify's CLI calls bare ``exit(0)``/``exit(1)`` (the REPL builtins normally
    injected by ``site.py``). PyInstaller's stub site does not install them,
    so frozen builds raise ``NameError: name 'exit' is not defined`` at the
    end of every successful run -- which masks any real error zotify printed
    just before exiting. Binding them back to :func:`sys.exit` is the
    smallest fix that lets zotify shut down cleanly.
    """
    import builtins

    if not hasattr(builtins, "exit"):
        builtins.exit = sys.exit
    if not hasattr(builtins, "quit"):
        builtins.quit = sys.exit


def main() -> None:
    """Entry point that renders ``UserFacingError`` cleanly instead of a traceback."""
    if _maybe_run_as_zotify():
        return
    try:
        cli(standalone_mode=False)
    except click.exceptions.Abort:
        click.echo("Aborted.", err=True)
        sys.exit(1)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except UserFacingError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
