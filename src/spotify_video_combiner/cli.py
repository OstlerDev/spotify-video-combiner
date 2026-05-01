"""Click CLI: thin adapters around the pipeline functions."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .bundled import ZOTIFY_PROXY_FLAG
from .errors import UserFacingError
from .pipeline import build_video, download_playlist
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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="spotify-video-combiner")
def cli() -> None:
    """Combine a Spotify playlist into a single MP4 (audio + cover-art slideshow)."""


@cli.command()
@click.argument("playlist")
@_workdir_opt
@_zotify_opt
def download(playlist: str, workdir: Path | None, zotify_extra: tuple[str, ...]) -> None:
    """Download audio + cover art for PLAYLIST (URL, URI, or ID) into a working folder."""
    download_playlist(
        playlist,
        workdir=workdir,
        zotify_extra=zotify_extra,
        log=_click_log,
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
    build_video(workdir, output=output, renderer=renderer, log=_click_log)


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
    _, resolved_workdir = download_playlist(
        playlist,
        workdir=workdir,
        zotify_extra=zotify_extra,
        log=_click_log,
    )
    renderer = SlideRenderer(font_path=str(font) if font else None)
    build_video(resolved_workdir, output=output, renderer=renderer, log=_click_log)


def _maybe_run_as_zotify() -> bool:
    """In a frozen ``.exe``, intercept the ``--zotify-mode`` flag and run zotify in-process.

    Returns True if we ran as zotify (caller should exit), False otherwise.
    The flag is the second argv element when invoked as a zotify proxy:
    ``svc.exe --zotify-mode <url> ...`` becomes ``zotify <url> ...``.

    A windowed PyInstaller bundle has no stdin/stdout, so zotify's first-run
    credential prompt + tqdm progress bars would have nowhere to go. We attach
    a fresh console with ``AllocConsole`` and rebind the std streams before
    handing control over.
    """
    if ZOTIFY_PROXY_FLAG not in sys.argv:
        return False
    _attach_console_if_needed()
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
    end of every successful run — which masks any real error zotify printed
    just before exiting. Binding them back to :func:`sys.exit` is the
    smallest fix that lets zotify shut down cleanly.
    """
    import builtins

    if not hasattr(builtins, "exit"):
        builtins.exit = sys.exit
    if not hasattr(builtins, "quit"):
        builtins.quit = sys.exit


def _attach_console_if_needed() -> None:
    """Allocate a Windows console + rebind std streams when frozen + windowed."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # AttachConsole(-1) attaches to the parent's console if one exists;
        # AllocConsole creates a fresh one if not. Try attach first to avoid
        # popping a stray window when the parent already has one.
        if not kernel32.AttachConsole(-1):
            kernel32.AllocConsole()
        # Streams must outlive this function: they replace process-wide stdio
        # for the duration of the run, so a context manager would defeat them.
        sys.stdin = open("CONIN$", encoding="utf-8")  # noqa: SIM115
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    except Exception:  # pragma: no cover - defensive; never let UI startup fail here
        pass


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
