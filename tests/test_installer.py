from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

import pytest

from spotify_video_combiner import installer


class TestCanAutoInstall:
    def test_false_when_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: True)
        assert installer.can_auto_install() is False

    def test_true_in_normal_venv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: False)
        # In our venv, sys.executable is real and pip is importable.
        assert installer.can_auto_install() is True


class TestInstallZotify:
    def test_runs_pip_with_correct_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: False)

        calls: list[list[str]] = []

        def fake_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(list(cmd), returncode=0)

        installer.install_zotify(runner=fake_runner)

        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ["-m", "pip", "install"]
        assert installer.ZOTIFY_PIP_SPEC in cmd

    def test_raises_when_pip_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: False)

        def fake_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(list(cmd), returncode=1)

        with pytest.raises(installer.InstallError, match="exited with code 1"):
            installer.install_zotify(runner=fake_runner)

    def test_raises_when_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: True)

        with pytest.raises(installer.InstallError, match="Cannot auto-install"):
            installer.install_zotify(runner=lambda _: pytest.fail("should not be called"))

    def test_logs_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.installer.is_frozen", lambda: False)
        messages: list[str] = []

        def fake_runner(cmd):
            return subprocess.CompletedProcess(list(cmd), returncode=0)

        installer.install_zotify(log=messages.append, runner=fake_runner)

        joined = "\n".join(messages)
        assert "installing it now" in joined.lower()
        assert "successfully" in joined.lower()
