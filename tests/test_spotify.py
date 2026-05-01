from __future__ import annotations

from pathlib import Path

import pytest
import requests
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOauthError

from spotify_video_combiner import config
from spotify_video_combiner.errors import SpotifyApiError
from spotify_video_combiner.spotify import (
    SpotifyCredentials,
    _api_errors_as_user_facing,
    _best_cover_url,
    parse_playlist_id,
)


@pytest.fixture
def isolated_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox cwd + user config dir + env vars so credential-loading tests can't leak."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    user_root = tmp_path / "userconfig"
    monkeypatch.setenv("APPDATA", str(user_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_root))

    for var in (
        "SPOTIPY_CLIENT_ID",
        "SPOTIPY_CLIENT_SECRET",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    return cwd


class TestParsePlaylistId:
    @pytest.mark.parametrize(
        "raw",
        [
            "37i9dQZF1DXcBWIGoYBM5M",
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc",
            "https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "  https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M  ",
        ],
    )
    def test_extracts_id(self, raw: str) -> None:
        assert parse_playlist_id(raw) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_invalid_input_raises_user_facing(self) -> None:
        # SpotifyApiError so the CLI shows a clean message, not a Python traceback.
        with pytest.raises(SpotifyApiError, match="Could not extract"):
            parse_playlist_id("not a playlist URL")


class TestBestCoverUrl:
    def test_picks_largest(self) -> None:
        images = [
            {"url": "small.jpg", "width": 64, "height": 64},
            {"url": "big.jpg", "width": 640, "height": 640},
            {"url": "medium.jpg", "width": 300, "height": 300},
        ]
        assert _best_cover_url(images) == "big.jpg"

    def test_handles_missing_dimensions(self) -> None:
        assert _best_cover_url([{"url": "only.jpg"}]) == "only.jpg"

    def test_empty_returns_none(self) -> None:
        assert _best_cover_url([]) is None


class TestSpotifyCredentials:
    def test_loads_from_canonical_env(self, isolated_credentials: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPOTIPY_CLIENT_ID", "cid")
        monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "secret")

        creds = SpotifyCredentials.from_env()
        assert creds.client_id == "cid"
        assert creds.client_secret == "secret"

    def test_loads_from_alias_env(self, isolated_credentials: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid2")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret2")

        creds = SpotifyCredentials.from_env()
        assert creds.client_id == "cid2"
        assert creds.client_secret == "secret2"

    def test_loads_from_cwd_credentials_file(self, isolated_credentials: Path) -> None:
        (isolated_credentials / config.CREDENTIALS_FILENAME).write_text(
            "SPOTIPY_CLIENT_ID=from-file\nSPOTIPY_CLIENT_SECRET=secret-from-file\n",
            encoding="utf-8",
        )

        creds = SpotifyCredentials.from_env()
        assert creds.client_id == "from-file"
        assert creds.client_secret == "secret-from-file"

    def test_loads_from_user_config_file(self, isolated_credentials: Path) -> None:
        path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "SPOTIPY_CLIENT_ID=user-id\nSPOTIPY_CLIENT_SECRET=user-secret\n",
            encoding="utf-8",
        )

        creds = SpotifyCredentials.from_env()
        assert creds.client_id == "user-id"
        assert creds.client_secret == "user-secret"

    def test_env_overrides_file(self, isolated_credentials: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (isolated_credentials / config.CREDENTIALS_FILENAME).write_text(
            "SPOTIPY_CLIENT_ID=from-file\nSPOTIPY_CLIENT_SECRET=secret-from-file\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SPOTIPY_CLIENT_ID", "from-env")
        monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "env-secret")

        creds = SpotifyCredentials.from_env()
        assert creds.client_id == "from-env"
        assert creds.client_secret == "env-secret"

    def test_missing_credentials_creates_template_and_raises(
        self, isolated_credentials: Path
    ) -> None:
        with pytest.raises(RuntimeError, match="credentials not found") as exc_info:
            SpotifyCredentials.from_env()

        template_path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        assert template_path.is_file()
        # The error message must point the user at the template they need to edit.
        assert str(template_path) in str(exc_info.value)
        assert "developer.spotify.com/dashboard" in str(exc_info.value)


class TestApiErrorTranslation:
    """Confirm spotipy/requests errors get re-raised as SpotifyApiError for clean CLI display."""

    def test_oauth_error_translates(self) -> None:
        with pytest.raises(SpotifyApiError, match="rejected your Web API credentials"), _api_errors_as_user_facing("pid"):
            raise SpotifyOauthError("invalid_client")

    def test_404_translates_to_playlist_not_found(self) -> None:
        with pytest.raises(SpotifyApiError, match="could not find playlist"), _api_errors_as_user_facing("missing_playlist"):
            raise SpotifyException(http_status=404, code=-1, msg="not found")

    def test_429_translates_to_rate_limit(self) -> None:
        with pytest.raises(SpotifyApiError, match="rate limit"), _api_errors_as_user_facing("pid"):
            raise SpotifyException(http_status=429, code=-1, msg="too many")

    def test_other_spotify_errors_translate_with_status(self) -> None:
        with pytest.raises(SpotifyApiError, match="HTTP 500"), _api_errors_as_user_facing("pid"):
            raise SpotifyException(http_status=500, code=-1, msg="boom")

    def test_network_errors_translate(self) -> None:
        with pytest.raises(SpotifyApiError, match="Network error"), _api_errors_as_user_facing("pid"):
            raise requests.exceptions.ConnectionError("DNS failed")

    def test_unrelated_errors_propagate_unchanged(self) -> None:
        with pytest.raises(ValueError, match="bug"), _api_errors_as_user_facing("pid"):
            raise ValueError("bug")
