from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from spotify_video_combiner.audio import OUTPUT_TEMPLATE, ZotifyDownloader, ZotifyError


class FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess:
        cmd_list = list(cmd)
        self.calls.append(cmd_list)
        return subprocess.CompletedProcess(cmd_list, returncode=self._returncode)


class TestEnsureAvailable:
    def test_raises_when_missing_and_no_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: None)
        monkeypatch.setattr("spotify_video_combiner.audio.can_auto_install", lambda: False)
        with pytest.raises(ZotifyError, match="could not be auto-installed"):
            ZotifyDownloader().ensure_available()

    def test_auto_installs_when_possible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lookups = iter([None, "/fake/zotify"])
        install_calls: list[bool] = []

        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: next(lookups))
        monkeypatch.setattr("spotify_video_combiner.audio.can_auto_install", lambda: True)
        monkeypatch.setattr(
            "spotify_video_combiner.audio.install_zotify",
            lambda **kwargs: install_calls.append(True),
        )
        result = ZotifyDownloader().ensure_available()
        assert result == "/fake/zotify"
        assert install_calls == [True]

    def test_returns_frozen_sentinel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: True)
        result = ZotifyDownloader().ensure_available()
        assert result.startswith("frozen://")


class TestBuildCommand:
    def test_shape(self, tmp_path: Path) -> None:
        z = ZotifyDownloader(extra_args=["--audio-format=mp3"])
        cmd = z.build_command("https://open.spotify.com/playlist/abc", tmp_path)

        assert cmd[0] == "zotify"
        assert "https://open.spotify.com/playlist/abc" in cmd
        assert "--library" in cmd
        assert str(tmp_path) in cmd
        assert "--output" in cmd
        assert OUTPUT_TEMPLATE in cmd
        # Embedding metadata is what lets us read tags back out later.
        assert "--save-metadata" in cmd
        # The .m3u8 sidecar is noise we don't need.
        assert "--no-playlist-file" in cmd
        # Forwarded extras come last so they can override built-in flags.
        assert cmd[-1] == "--audio-format=mp3"

    def test_in_frozen_mode_reenters_self(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: True)
        monkeypatch.setattr("spotify_video_combiner.audio.sys.executable", "C:/dist/svc.exe")
        z = ZotifyDownloader()
        cmd = z.build_command("https://open.spotify.com/playlist/abc", tmp_path)

        assert cmd[:2] == ["C:/dist/svc.exe", "--zotify-mode"]
        assert "https://open.spotify.com/playlist/abc" in cmd


class TestDownload:
    def test_invokes_runner_once_with_built_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify"
        )
        runner = FakeRunner()
        z = ZotifyDownloader(runner=runner)
        rc = z.download("https://open.spotify.com/playlist/xyz", tmp_path / "wd")

        assert rc == 0
        assert len(runner.calls) == 1
        cmd = runner.calls[0]
        assert "https://open.spotify.com/playlist/xyz" in cmd
        assert "--library" in cmd

    def test_creates_workdir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify"
        )
        runner = FakeRunner()
        z = ZotifyDownloader(runner=runner)
        target = tmp_path / "deep" / "nested" / "wd"
        z.download("url", target)
        assert target.is_dir()

    def test_logs_warning_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify"
        )
        messages: list[str] = []
        runner = FakeRunner(returncode=2)
        z = ZotifyDownloader(runner=runner, log=messages.append)
        rc = z.download("url", tmp_path / "wd")

        assert rc == 2
        assert any("zotify exited with code 2" in m for m in messages)
