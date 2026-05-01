"""PyInstaller entry-point for the windowed ``svc-gui`` bundle.

This module exists because PyInstaller's ``Analysis`` needs a real ``.py``
file as its entry point, not a Python ``console_scripts`` reference. Keeping
this thin (just dispatching ``--zotify-mode`` then launching the GUI) keeps
the spec file simple.
"""

from spotify_video_combiner.cli import _maybe_run_as_zotify
from spotify_video_combiner.gui import main as gui_main


def run() -> None:
    if _maybe_run_as_zotify():
        return
    gui_main()


if __name__ == "__main__":
    run()
