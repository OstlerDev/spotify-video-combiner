from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from spotify_video_combiner.audio import (
    KNOWN_AUDIO_EXTS,
    ZotifyDownloader,
    ZotifyError,
    _interactive_console_runner,
    _make_default_runner,
    find_existing_audio,
    zotify_credentials_path,
    zotify_is_authenticated,
)
from spotify_video_combiner.manifest import Track


class FakeRunner:
    """Records every command and optionally creates files for the IDs it sees."""

    def __init__(self, fake_ext: str | None = "ogg") -> None:
        self.calls: list[list[str]] = []
        self._fake_ext = fake_ext

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess:
        cmd_list = list(cmd)
        self.calls.append(cmd_list)
        if self._fake_ext is not None:
            root_idx = cmd_list.index("--library")
            root = Path(cmd_list[root_idx + 1])
            root.mkdir(parents=True, exist_ok=True)
            for arg in cmd_list:
                if arg.startswith("https://open.spotify.com/track/"):
                    track_id = arg.rsplit("/", 1)[-1]
                    (root / f"{track_id}.{self._fake_ext}").write_bytes(b"fake-audio")
        return subprocess.CompletedProcess(cmd_list, returncode=0)


class TestFindExistingAudio:
    @pytest.mark.parametrize("ext", KNOWN_AUDIO_EXTS)
    def test_finds_each_known_extension(self, tmp_path: Path, ext: str) -> None:
        (tmp_path / f"abc.{ext}").write_bytes(b"x")
        assert find_existing_audio(tmp_path, "abc") == tmp_path / f"abc.{ext}"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_existing_audio(tmp_path, "nope") is None

    def test_ignores_unrelated_files(self, tmp_path: Path) -> None:
        (tmp_path / "abc.txt").write_bytes(b"x")
        assert find_existing_audio(tmp_path, "abc") is None


class TestZotifyDownloader:
    def test_ensure_available_raises_when_missing_and_no_pip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: None)
        monkeypatch.setattr("spotify_video_combiner.audio.can_auto_install", lambda: False)
        with pytest.raises(ZotifyError, match="could not be auto-installed"):
            ZotifyDownloader().ensure_available()

    def test_ensure_available_auto_installs_when_possible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First lookup fails (triggering install), second succeeds (after install).
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

    def test_ensure_available_returns_frozen_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: True)
        result = ZotifyDownloader().ensure_available()
        assert result.startswith("frozen://")

    def test_build_command_shape(self, tmp_path: Path) -> None:
        z = ZotifyDownloader(extra_args=["--audio-format=mp3"])
        cmd = z.build_command(["https://open.spotify.com/track/abc"], tmp_path)

        assert cmd[0] == "zotify"
        assert "https://open.spotify.com/track/abc" in cmd
        # DraftKinner zotify uses --library for the destination root,
        # and the {spotid} template variable for the Spotify track ID.
        # The file extension is appended automatically based on --audio-format.
        assert "--library" in cmd
        assert str(tmp_path) in cmd
        assert "--output" in cmd
        assert "{spotid}" in cmd
        assert "{ext}" not in " ".join(cmd), "zotify auto-appends .ext; do not template it"
        assert cmd[-1] == "--audio-format=mp3"

    def test_build_command_in_frozen_mode_reenters_self(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: True)
        monkeypatch.setattr("spotify_video_combiner.audio.sys.executable", "C:/dist/svc.exe")
        z = ZotifyDownloader()
        cmd = z.build_command(["https://open.spotify.com/track/abc"], tmp_path)

        assert cmd[:2] == ["C:/dist/svc.exe", "--zotify-mode"]
        assert "https://open.spotify.com/track/abc" in cmd

    def test_download_skips_existing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_tracks: list[Track],
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify")
        # Pre-create one of the audio files.
        dest = tmp_path / "audio"
        dest.mkdir()
        (dest / "track1.ogg").write_bytes(b"already here")

        runner = FakeRunner()
        z = ZotifyDownloader(runner=runner)
        result = z.download_tracks(sample_tracks, dest)

        # Runner should be called only once, with only the missing track's URL.
        assert len(runner.calls) == 1
        urls_in_call = [a for a in runner.calls[0] if a.startswith("https://")]
        assert urls_in_call == ["https://open.spotify.com/track/track2"]
        assert set(result) == {"track1", "track2"}

    def test_download_no_pending_skips_runner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_tracks: list[Track],
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify")
        dest = tmp_path / "audio"
        dest.mkdir()
        (dest / "track1.ogg").write_bytes(b"x")
        (dest / "track2.mp3").write_bytes(b"x")

        runner = FakeRunner()
        z = ZotifyDownloader(runner=runner)
        result = z.download_tracks(sample_tracks, dest)

        assert runner.calls == []  # nothing to download
        assert set(result) == {"track1", "track2"}

    def test_missing_downloads_excluded_from_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_tracks: list[Track],
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.resolve_binary", lambda _: "/fake/zotify")
        # FakeRunner with fake_ext=None means files are never created.
        runner = FakeRunner(fake_ext=None)
        z = ZotifyDownloader(runner=runner)
        result = z.download_tracks(sample_tracks, tmp_path / "audio")

        assert result == {}  # nothing on disk afterwards


class TestZotifyAuthDetection:
    """Pure path-based detection of zotify's first-run credential cache."""

    def test_path_lives_under_appdata_on_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.sys.platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
        path = zotify_credentials_path()
        assert path == tmp_path / "AppData" / "Zotify" / "credentials.json"

    def test_path_lives_under_xdg_on_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("spotify_video_combiner.audio.sys.platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        path = zotify_credentials_path()
        assert path == tmp_path / "xdg" / "Zotify" / "credentials.json"

    def test_is_authenticated_only_when_file_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        creds = tmp_path / "creds.json"
        monkeypatch.setattr(
            "spotify_video_combiner.audio.zotify_credentials_path", lambda: creds
        )
        assert zotify_is_authenticated() is False
        creds.write_text("{}", encoding="utf-8")
        assert zotify_is_authenticated() is True


class TestMakeDefaultRunner:
    """Pick the right runner for the current platform/auth state."""

    def test_authenticated_uses_streaming_runner_when_log_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.zotify_is_authenticated", lambda: True
        )
        sentinel = object()
        monkeypatch.setattr(
            "spotify_video_combiner.audio.make_runner",
            lambda log, check: sentinel,
        )
        runner = _make_default_runner(lambda _: None)
        assert runner is sentinel

    def test_authenticated_uses_inherit_runner_when_no_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.zotify_is_authenticated", lambda: True
        )
        seen: dict = {}

        def fake_make_runner(log, check):
            seen["log"] = log
            seen["check"] = check
            return "stub"

        monkeypatch.setattr("spotify_video_combiner.audio.make_runner", fake_make_runner)
        runner = _make_default_runner(None)
        assert runner == "stub"
        assert seen == {"log": None, "check": False}

    def test_first_run_falls_back_to_visible_console_when_frozen_on_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "spotify_video_combiner.audio.zotify_is_authenticated", lambda: False
        )
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: True)
        monkeypatch.setattr("spotify_video_combiner.audio.sys.platform", "win32")
        runner = _make_default_runner(None)
        assert runner is _interactive_console_runner

    def test_first_run_uses_normal_runner_outside_windows_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # On non-frozen / non-Windows, even an unauthenticated zotify uses the
        # normal runner because there's already a parent terminal for it to
        # prompt on.
        monkeypatch.setattr(
            "spotify_video_combiner.audio.zotify_is_authenticated", lambda: False
        )
        monkeypatch.setattr("spotify_video_combiner.audio.is_frozen", lambda: False)
        monkeypatch.setattr(
            "spotify_video_combiner.audio.make_runner", lambda log, check: "stub"
        )
        assert _make_default_runner(None) == "stub"
