"""Lightweight GUI tests.

We deliberately avoid spinning up a real Tk root in headless test runs (it
would either fail outright or pop a window). Tests cover only the pure-Python
helpers; the rendering + threading logic is exercised manually.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from spotify_video_combiner import auth, gui


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    auth.reset_cached_session()


class TestAuthHelpers:
    def test_is_signed_in_false_by_default(self) -> None:
        assert auth.is_signed_in() is False

    def test_is_signed_in_true_when_credentials_exist(self) -> None:
        path = auth.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"username": "alice"}), encoding="utf-8")
        assert auth.is_signed_in() is True
        assert auth.current_username() == "alice"


class TestStaticChoices:
    def test_resolutions_have_valid_dimensions(self) -> None:
        for label, (w, h) in gui.DEFAULT_RESOLUTIONS.items():
            assert w > 0 and h > 0, label
            assert w >= h, f"{label} should be landscape (w >= h)"

    def test_audio_format_choices_round_trip_to_zotify_args(self) -> None:
        # The default (no flag) inherits zotify's vorbis/OGG output.
        assert gui.DEFAULT_AUDIO_FORMATS["Default (OGG/Vorbis)"] == []
        # Other labels must map to valid DraftKinner-zotify --audio-format values.
        valid_formats = {"aac", "fdk_aac", "flac", "mp3", "opus", "vorbis", "wav", "wavpack"}
        for label, args in gui.DEFAULT_AUDIO_FORMATS.items():
            if not args:
                continue
            joined = " ".join(args)
            assert "--audio-format=" in joined, f"{label!r} should use --audio-format=<value>"
            value = joined.split("--audio-format=", 1)[1].split()[0]
            assert value in valid_formats, f"{label!r} maps to invalid format {value!r}"


class TestToggleAuth:
    """Drive ``App._toggle_auth`` without a real Tk root via a mock ``self``."""

    def test_sign_out_refreshes_button(self) -> None:
        path = auth.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"username": "alice"}), encoding="utf-8")

        app = MagicMock()
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            gui.App._toggle_auth(app)

        assert not auth.is_signed_in()
        app._append_pipeline.assert_called_once_with("Signed out.\n")
        app._refresh_auth_button.assert_called_once()
