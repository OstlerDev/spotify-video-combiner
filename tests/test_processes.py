"""Tests for the subprocess helpers used to suppress popup console windows."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from spotify_video_combiner import processes


class TestNoWindowKwargs:
    def test_empty_off_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(processes.sys, "platform", "linux")
        assert processes._no_window_kwargs() == {}

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific flags")
    def test_sets_create_no_window_and_hidden_startupinfo_on_windows(self) -> None:
        kwargs = processes._no_window_kwargs()
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        info = kwargs["startupinfo"]
        assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert info.wShowWindow == subprocess.SW_HIDE


class TestRunInherit:
    def test_passes_command_through_and_hides_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(processes.subprocess, "run", fake_run)
        # Force the windows-flags branch so the test is meaningful on all OSes.
        monkeypatch.setattr(
            processes, "_no_window_kwargs", lambda: {"creationflags": 0x08000000}
        )

        processes.run_inherit(["echo", "hi"])
        assert captured["cmd"] == ["echo", "hi"]
        assert captured["kwargs"]["creationflags"] == 0x08000000
        assert captured["kwargs"]["check"] is False

    def test_check_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(processes.subprocess, "run", fake_run)
        monkeypatch.setattr(processes, "_no_window_kwargs", lambda: {})
        processes.run_inherit(["x"], check=True)
        assert captured["check"] is True


class FakeProc:
    """Minimal Popen stand-in: yields preset stdout lines, then "exits"."""

    def __init__(self, lines: Sequence[str], returncode: int = 0) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode
        self._waited = 0

    def wait(self) -> int:
        self._waited += 1
        return self.returncode


class TestRunStreaming:
    def test_forwards_each_line_to_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []

        def fake_popen(cmd, **kwargs):
            return FakeProc(["first\n", "second\n", "\n", "third\n"])

        monkeypatch.setattr(processes.subprocess, "Popen", fake_popen)
        result = processes.run_streaming(["x"], log=captured.append)
        assert captured == ["first", "second", "third"]
        assert result.returncode == 0

    def test_strips_carriage_returns_from_progress_lines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []

        # tqdm-style: \r-only progress redraws arrive as separate "lines"
        # because Python's text mode splits on \r as well as \n. We strip
        # surrounding whitespace too so the GUI log doesn't render a wall of
        # leading spaces from progress-bar redraws.
        monkeypatch.setattr(
            processes.subprocess,
            "Popen",
            lambda cmd, **kwargs: FakeProc([" 50% |##  |\r", " done!\n"]),
        )
        processes.run_streaming(["x"], log=captured.append)
        assert captured == ["50% |##  |", "done!"]

    def test_check_raises_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            processes.subprocess,
            "Popen",
            lambda cmd, **kwargs: FakeProc([], returncode=2),
        )
        with pytest.raises(subprocess.CalledProcessError):
            processes.run_streaming(["x"], log=lambda _: None, check=True)

    def test_returns_completed_process_with_returncode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            processes.subprocess,
            "Popen",
            lambda cmd, **kwargs: FakeProc(["ok\n"], returncode=7),
        )
        result = processes.run_streaming(["x"], log=lambda _: None, check=False)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 7


class TestMakeRunner:
    def test_log_none_uses_run_inherit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            processes,
            "run_inherit",
            lambda cmd, **kw: seen.append("inherit") or subprocess.CompletedProcess(cmd, 0),
        )
        monkeypatch.setattr(
            processes,
            "run_streaming",
            lambda cmd, **kw: seen.append("streaming") or subprocess.CompletedProcess(cmd, 0),
        )
        processes.make_runner(None)(["x"])
        assert seen == ["inherit"]

    def test_log_callable_uses_run_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            processes,
            "run_inherit",
            lambda cmd, **kw: seen.append("inherit") or subprocess.CompletedProcess(cmd, 0),
        )
        monkeypatch.setattr(
            processes,
            "run_streaming",
            lambda cmd, **kw: seen.append("streaming") or subprocess.CompletedProcess(cmd, 0),
        )
        processes.make_runner(lambda _: None)(["x"])
        assert seen == ["streaming"]
