from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from spotify_video_combiner.cli import _bind_repl_exit_builtins, cli, main
from spotify_video_combiner.errors import CredentialsError


class TestCliWiring:
    def test_help_lists_subcommands(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        for cmd in ("download", "build", "all"):
            assert cmd in result.output

    def test_version_flag(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "spotify-video-combiner" in result.output

    def test_download_invokes_pipeline_with_url(self, tmp_path: Path) -> None:
        with patch("spotify_video_combiner.cli.download_playlist") as fake:
            fake.return_value = (object(), tmp_path)
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
            fake.return_value = (object(), tmp_path)
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

    def test_main_intercepts_zotify_proxy_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In a frozen .exe, ``svc.exe --zotify-mode <args>`` runs zotify in-process."""
        captured_argv: list[list[str]] = []

        def fake_zotify_main():
            captured_argv.append(list(sys.argv))

        # Patch zotify's main so we don't actually try to log into Spotify.
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
            # Mirror zotify's actual behaviour: call exit(0), which raises
            # SystemExit when properly bound. We swallow it so the test can
            # observe the post-call state.
            with contextlib.suppress(SystemExit):
                builtins.exit(0)

        import zotify.__main__ as zmod

        monkeypatch.setattr(zmod, "main", fake_zotify_main)
        # Simulate the PyInstaller stub-site state where ``exit`` is absent.
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
        """Calling the shim twice (or with the builtins already present) is safe."""
        import builtins

        sentinel = lambda *_: None  # noqa: E731
        monkeypatch.setattr(builtins, "exit", sentinel, raising=False)
        monkeypatch.setattr(builtins, "quit", sentinel, raising=False)
        _bind_repl_exit_builtins()
        # Existing bindings should be left alone.
        assert builtins.exit is sentinel
        assert builtins.quit is sentinel

    def test_main_renders_user_facing_errors_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(*args, **kwargs):
            raise CredentialsError("nope, missing credentials")

        monkeypatch.setattr("spotify_video_combiner.cli.download_playlist", boom)
        monkeypatch.setattr(sys, "argv", ["svc", "download", "https://x/playlist/abc"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error: nope, missing credentials" in captured.err
        # No "Traceback" in either stream — the whole point of UserFacingError.
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_all_runs_download_then_build(self, tmp_path: Path) -> None:
        with (
            patch("spotify_video_combiner.cli.download_playlist") as fake_dl,
            patch("spotify_video_combiner.cli.build_video") as fake_build,
        ):
            fake_dl.return_value = (object(), tmp_path)

            result = CliRunner().invoke(
                cli,
                ["all", "https://open.spotify.com/playlist/xyz"],
            )

        assert result.exit_code == 0, result.output
        fake_dl.assert_called_once()
        fake_build.assert_called_once()
        # `build_video` must run against the workdir resolved by `download_playlist`.
        assert fake_build.call_args.args[0] == tmp_path
