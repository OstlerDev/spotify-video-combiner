"""Lightweight GUI tests.

We deliberately avoid spinning up a real Tk root in headless test runs (it
would either fail outright or pop a window). Tests cover only the pure-Python
helpers; the rendering + threading logic is exercised manually.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spotify_video_combiner import config, gui


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    user_root = tmp_path / "userconfig"
    monkeypatch.setenv("APPDATA", str(user_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_root))


class TestCredentialsPresent:
    def test_false_when_no_creds_anywhere(self) -> None:
        assert gui.credentials_present() is False

    def test_true_when_user_config_has_creds(self) -> None:
        path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "SPOTIPY_CLIENT_ID=set\nSPOTIPY_CLIENT_SECRET=set\n", encoding="utf-8"
        )
        assert gui.credentials_present() is True

    def test_false_when_template_has_blank_values(self) -> None:
        path = config.user_config_dir() / config.CREDENTIALS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config.CREDENTIALS_TEMPLATE, encoding="utf-8")
        assert gui.credentials_present() is False


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
