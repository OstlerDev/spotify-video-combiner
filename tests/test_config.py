from __future__ import annotations

from pathlib import Path

import pytest

from spotify_video_combiner import config


@pytest.fixture(autouse=True)
def isolated_config_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect both candidate paths into a tmp dir for every test in this module."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    user_root = tmp_path / "userconfig"
    monkeypatch.setenv("APPDATA", str(user_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_root))


class TestUserConfigDir:
    def test_appends_app_name(self) -> None:
        assert config.user_config_dir().name == "spotify-video-combiner"


class TestParseEnvFile:
    def test_parses_simple_kv(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        assert config.parse_env_file(path) == {"FOO": "bar", "BAZ": "qux"}

    def test_ignores_comments_and_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text(
            "# leading comment\n"
            "\n"
            "FOO=bar\n"
            "  # indented comment\n"
            "BAZ=qux\n",
            encoding="utf-8",
        )
        assert config.parse_env_file(path) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_quotes(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text(
            'FOO="quoted"\n'
            "BAR='single'\n"
            'BAZ="mismatched\'\n',
            encoding="utf-8",
        )
        parsed = config.parse_env_file(path)
        assert parsed["FOO"] == "quoted"
        assert parsed["BAR"] == "single"
        # Mismatched quotes are left untouched (not magic-stripped).
        assert parsed["BAZ"] == '"mismatched\''

    def test_ignores_lines_without_equals(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text("noequals\nFOO=bar\n", encoding="utf-8")
        assert config.parse_env_file(path) == {"FOO": "bar"}

    def test_ignores_empty_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text("=value\nFOO=bar\n", encoding="utf-8")
        assert config.parse_env_file(path) == {"FOO": "bar"}

    def test_handles_equals_in_values(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.env"
        path.write_text("URL=https://example.com/?x=1&y=2\n", encoding="utf-8")
        assert config.parse_env_file(path) == {"URL": "https://example.com/?x=1&y=2"}


class TestLoadCredentialsFiles:
    def test_returns_empty_when_no_files(self) -> None:
        assert config.load_credentials_files() == {}

    def test_loads_from_user_config(self) -> None:
        path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SPOTIPY_CLIENT_ID=user-id\nSPOTIPY_CLIENT_SECRET=user-secret\n", encoding="utf-8")

        loaded = config.load_credentials_files()
        assert loaded == {
            "SPOTIPY_CLIENT_ID": "user-id",
            "SPOTIPY_CLIENT_SECRET": "user-secret",
        }

    def test_cwd_file_takes_priority_over_user_config(self) -> None:
        user_path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        user_path.parent.mkdir(parents=True, exist_ok=True)
        user_path.write_text("SPOTIPY_CLIENT_ID=user\nSPOTIPY_CLIENT_SECRET=user\n", encoding="utf-8")

        cwd_path = Path.cwd() / config.CREDENTIALS_FILENAME
        cwd_path.write_text("SPOTIPY_CLIENT_ID=local\n", encoding="utf-8")

        loaded = config.load_credentials_files()
        # CWD wins for the conflicting key, user config fills in the rest.
        assert loaded["SPOTIPY_CLIENT_ID"] == "local"
        assert loaded["SPOTIPY_CLIENT_SECRET"] == "user"


class TestEnsureTemplateExists:
    def test_creates_at_user_config_when_no_path_given(self) -> None:
        path = config.ensure_template_exists()
        assert path.is_file()
        assert path == config.user_config_dir() / config.CREDENTIALS_FILENAME
        assert "SPOTIPY_CLIENT_ID=" in path.read_text(encoding="utf-8")

    def test_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "creds.env"
        target.write_text("preserved", encoding="utf-8")
        result = config.ensure_template_exists(target)
        assert result == target
        assert target.read_text(encoding="utf-8") == "preserved"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "creds.env"
        config.ensure_template_exists(target)
        assert target.is_file()
