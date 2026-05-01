"""Self-bootstrap zotify when missing.

Users who installed an early version of this package (before zotify was a
declared dependency) won't have ``zotify`` on PATH. Rather than tell them to
re-run ``pip install``, we just install it for them on first use.

In a PyInstaller-frozen build this is a no-op: zotify is bundled as a Python
package and invoked via the ``--zotify-mode`` proxy flag, so PATH never enters
the picture.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .bundled import is_frozen

ZOTIFY_PIP_SPEC = "zotify @ git+https://github.com/DraftKinner/zotify.git"

LogFn = Callable[[str], None]
SubprocessRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=False)


def _noop(_: str) -> None:  # pragma: no cover - trivial
    pass


class InstallError(RuntimeError):
    """Raised when we can't auto-install zotify."""


def can_auto_install() -> bool:
    """True when we have a usable Python interpreter to run ``pip`` with.

    False inside a PyInstaller bundle (no real ``python.exe`` available) and
    when a pip module isn't importable from the current interpreter.
    """
    if is_frozen():
        return False
    if not Path(sys.executable).is_file():
        return False
    try:
        import pip  # noqa: F401
    except ImportError:
        return False
    return True


def install_zotify(
    *, log: LogFn = _noop, runner: SubprocessRunner | None = None
) -> None:
    """Install zotify into the current Python environment via ``pip``.

    Raises :class:`InstallError` if the install can't be performed (frozen
    runtime or missing pip) or if ``pip`` exits non-zero.
    """
    if not can_auto_install():
        raise InstallError(
            "Cannot auto-install zotify in this environment. "
            "Install it manually: pipx install git+https://github.com/DraftKinner/zotify.git"
        )

    run = runner or _default_runner
    log("zotify is not installed yet — installing it now into the current Python environment...")
    log(f"  $ {sys.executable} -m pip install {ZOTIFY_PIP_SPEC}")

    result = run([sys.executable, "-m", "pip", "install", ZOTIFY_PIP_SPEC])
    if result.returncode != 0:
        raise InstallError(
            f"`pip install` exited with code {result.returncode}. "
            "See the output above for details, or install manually:\n"
            f"  {sys.executable} -m pip install {ZOTIFY_PIP_SPEC}"
        )
    log("zotify installed successfully.")
