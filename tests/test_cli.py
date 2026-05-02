from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from spotify_video_combiner import auth
from spotify_video_combiner.cli import _bind_repl_exit_builtins, cli, main
from spotify_video_combiner.errors import AuthError


@pytest.fixture(autouse=True)
def signed_in_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most CLI tests assume the user is signed in. Individual tests can override."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    creds = auth.credentials_path()
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text('{"username": "tester"}', encoding="utf-8")
    auth.reset_cached_session()


class TestCliWiring:
    def test_help_lists_subcommands(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        for cmd in ("download", "build", "all", "signin", "signout"):
            assert cmd in result.output

    def test_version_flag(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "spotify-video-combiner" in result.output

    def test_download_invokes_pipeline_with_url(self, tmp_path: Path) -> None:
        with patch("spotify_video_combiner.cli.download_playlist") as fake:
            fake.return_value = tmp_path
            result = CliRunner().invoke(
                cli,
                ["download", "https://open.spotify.com/playlist/abc", "-w", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        fake.assert_called_once()
        kwargs = fake.call_args.kwargs
        assert kwargs["workdir"] == tmp_path
        assert kwargs["zotify_extra"] == ()

    def test_download_forwards_zotify_args(self, tmp_path: Path) -> None:
        with patch("spotify_video_combiner.cli.download_playlist") as fake:
            fake.return_value = tmp_path
            result = CliRunner().invoke(
                cli,
                [
                    "download", "url",
                    "-w", str(tmp_path),
                    "--zotify-arg=--download-format=mp3",
                    "--zotify-arg=--bulk-wait-time=2",
                ],
            )

        assert result.exit_code == 0, result.output
        assert fake.call_args.kwargs["zotify_extra"] == (
            "--download-format=mp3",
            "--bulk-wait-time=2",
        )

    def test_build_invokes_pipeline(self, tmp_path: Path) -> None:
        with patch("spotify_video_combiner.cli.build_video") as fake:
            result = CliRunner().invoke(cli, ["build", str(tmp_path)])

        assert result.exit_code == 0, result.output
        fake.assert_called_once()
        assert fake.call_args.kwargs["output"] is None

    def test_download_requires_sign_in(self, tmp_path: Path) -> None:
        auth.sign_out()  # remove the autouse-fixture credentials
        result = CliRunner().invoke(cli, ["download", "url", "-w", str(tmp_path)])
        assert result.exit_code != 0
        # Click in non-standalone mode propagates the AuthError up to main().
        assert isinstance(result.exception, AuthError)

    def test_all_requires_sign_in(self, tmp_path: Path) -> None:
        auth.sign_out()
        result = CliRunner().invoke(cli, ["all", "url"])
        assert result.exit_code != 0
        assert isinstance(result.exception, AuthError)

    def test_main_intercepts_zotify_proxy_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In a frozen .exe, ``svc.exe --zotify-mode <args>`` runs zotify in-process."""
        captured_argv: list[list[str]] = []

        def fake_zotify_main():
            captured_argv.append(list(sys.argv))

        import zotify.__main__ as zmod

        monkeypatch.setattr(zmod, "main", fake_zotify_main)
        monkeypatch.setattr(
            sys,
            "argv",
            ["svc.exe", "--zotify-mode", "https://open.spotify.com/track/abc", "--root-path", "."],
        )

        main()  # should return cleanly without raising/exiting

        assert captured_argv == [
            ["zotify", "https://open.spotify.com/track/abc", "--root-path", "."]
        ]

    def test_zotify_proxy_mode_binds_exit_builtin_before_invoking_zotify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zotify calls bare ``exit()`` (a REPL-only builtin); we must bind it.

        Without this shim, frozen builds raise ``NameError: name 'exit' is not
        defined`` at the end of every successful zotify run.
        """
        import builtins
        import contextlib

        had_exit_during_call: list[bool] = []

        def fake_zotify_main():
            had_exit_during_call.append(hasattr(builtins, "exit"))
            with contextlib.suppress(SystemExit):
                builtins.exit(0)

        import zotify.__main__ as zmod

        monkeypatch.setattr(zmod, "main", fake_zotify_main)
        monkeypatch.delattr(builtins, "exit", raising=False)
        monkeypatch.delattr(builtins, "quit", raising=False)
        monkeypatch.setattr(sys, "argv", ["svc.exe", "--zotify-mode", "x"])

        main()

        assert had_exit_during_call == [True]
        assert builtins.exit is sys.exit
        assert builtins.quit is sys.exit

    def test_bind_repl_exit_builtins_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        sentinel = lambda *_: None  # noqa: E731
        monkeypatch.setattr(builtins, "exit", sentinel, raising=False)
        monkeypatch.setattr(builtins, "quit", sentinel, raising=False)
        _bind_repl_exit_builtins()
        assert builtins.exit is sentinel
        assert builtins.quit is sentinel

    def test_main_renders_user_facing_errors_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(*args, **kwargs):
            raise AuthError("not signed in")

        monkeypatch.setattr("spotify_video_combiner.cli.download_playlist", boom)
        monkeypatch.setattr(sys, "argv", ["svc", "download", "https://x/playlist/abc"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error: not signed in" in captured.err
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_all_runs_download_then_build(self, tmp_path: Path) -> None:
        with (
            patch("spotify_video_combiner.cli.download_playlist") as fake_dl,
            patch("spotify_video_combiner.cli.build_video") as fake_build,
        ):
            fake_dl.return_value = tmp_path

            result = CliRunner().invoke(
                cli,
                ["all", "https://open.spotify.com/playlist/xyz"],
            )

        assert result.exit_code == 0, result.output
        fake_dl.assert_called_once()
        fake_build.assert_called_once()
        # `build_video` must run against the workdir resolved by `download_playlist`.
        assert fake_build.call_args.args[0] == tmp_path


class TestSignInOut:
    def test_signin_already_signed_in(self) -> None:
        result = CliRunner().invoke(cli, ["signin"])
        assert result.exit_code == 0
        assert "Already signed in" in result.output

    def test_signin_runs_oauth_flow_when_not_signed_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        auth.sign_out()
        seen_calls: list[tuple[str, list[str]]] = []

        def fake_sign_in(username: str, on_url: Any) -> None:
            urls: list[str] = []
            on_url("https://accounts.spotify.com/authorize?...")
            urls.append("called")
            seen_calls.append((username, urls))

        monkeypatch.setattr("spotify_video_combiner.cli.sign_in", fake_sign_in)
        result = CliRunner().invoke(cli, ["signin", "--username", "alice"])

        assert result.exit_code == 0, result.output
        assert "Signed in as alice" in result.output
        assert seen_calls == [("alice", ["called"])]
        assert "https://accounts.spotify.com/authorize?..." in result.output

    def test_signin_prompts_for_username_when_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        auth.sign_out()
        captured: list[str] = []

        def fake_sign_in(username: str, on_url: Any) -> None:
            captured.append(username)
            on_url("https://example/auth")

        monkeypatch.setattr("spotify_video_combiner.cli.sign_in", fake_sign_in)
        result = CliRunner().invoke(cli, ["signin"], input="bob\n")

        assert result.exit_code == 0, result.output
        assert captured == ["bob"]
        assert "Signed in as bob" in result.output

    def test_signout_removes_credentials(self) -> None:
        result = CliRunner().invoke(cli, ["signout"])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert auth.is_signed_in() is False

    def test_signout_when_not_signed_in(self) -> None:
        auth.sign_out()
        result = CliRunner().invoke(cli, ["signout"])
        assert result.exit_code == 0
        assert "nothing to do" in result.output
