from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spotify_video_combiner import auth
from spotify_video_combiner.errors import AuthError


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``credentials_path()`` into a per-test temp dir."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    auth.reset_cached_session()
    yield tmp_path
    auth.reset_cached_session()


class TestSignInState:
    def test_not_signed_in_when_file_missing(self) -> None:
        assert auth.is_signed_in() is False
        assert auth.current_username() is None

    def test_signed_in_when_file_present(self) -> None:
        path = auth.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"username": "alice"}), encoding="utf-8")
        assert auth.is_signed_in() is True
        assert auth.current_username() == "alice"

    def test_unreadable_credentials_returns_none_username(self) -> None:
        path = auth.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json", encoding="utf-8")
        assert auth.is_signed_in() is True  # file exists
        assert auth.current_username() is None  # but unreadable

    def test_sign_out_removes_file(self) -> None:
        path = auth.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        assert auth.sign_out() is True
        assert not path.exists()
        # Idempotent.
        assert auth.sign_out() is False


class TestSignIn:
    def test_requires_non_empty_username(self) -> None:
        with pytest.raises(AuthError, match="username is required"):
            auth.sign_in("", lambda _url: None)

    def test_drives_zotify_oauth_then_session_from_oauth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = MagicMock()
        oauth.auth_interactive.return_value = "https://accounts.spotify.com/authorize?..."

        oauth_factory = MagicMock(return_value=oauth)
        session_from_oauth = MagicMock()

        # Patch the lazy imports inside ``sign_in``.
        import zotify

        monkeypatch.setattr(zotify, "OAuth", oauth_factory)
        monkeypatch.setattr(
            zotify.Session, "from_oauth", staticmethod(session_from_oauth)
        )

        urls_seen: list[str] = []
        auth.sign_in("alice", urls_seen.append)

        oauth_factory.assert_called_once_with("alice")
        oauth.auth_interactive.assert_called_once()
        assert urls_seen == ["https://accounts.spotify.com/authorize?..."]
        # ``Session.from_oauth`` is what blocks on the OAuth callback and
        # writes ``credentials.json``; we verify we're delegating to it.
        session_from_oauth.assert_called_once()
        called_oauth, called_path = session_from_oauth.call_args.args
        assert called_oauth is oauth
        assert called_path == auth.credentials_path()


class TestGetSession:
    def test_raises_when_not_signed_in(self) -> None:
        with pytest.raises(AuthError, match="Not signed in"):
            auth.get_session()
