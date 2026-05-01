from __future__ import annotations

from pathlib import Path

import pytest

from spotify_video_combiner import bundled


class TestIsFrozen:
    def test_returns_false_in_normal_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr("sys.frozen", raising=False)
        assert bundled.is_frozen() is False

    def test_returns_true_when_sys_frozen_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert bundled.is_frozen() is True


class TestBundledResourceRoot:
    def test_returns_none_when_not_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr("sys._MEIPASS", raising=False)
        assert bundled.bundled_resource_root() is None

    def test_returns_meipass_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
        assert bundled.bundled_resource_root() == tmp_path


class TestFindBundledBinary:
    def test_returns_none_when_not_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr("sys._MEIPASS", raising=False)
        assert bundled.find_bundled_binary("ffmpeg") is None

    def test_finds_exe_in_bundle(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bin_dir = tmp_path / bundled.BUNDLED_BIN_SUBDIR
        bin_dir.mkdir()
        (bin_dir / "ffmpeg.exe").write_bytes(b"")
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

        result = bundled.find_bundled_binary("ffmpeg")
        assert result == str(bin_dir / "ffmpeg.exe")

    def test_finds_extensionless_binary(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bin_dir = tmp_path / bundled.BUNDLED_BIN_SUBDIR
        bin_dir.mkdir()
        (bin_dir / "ffmpeg").write_bytes(b"")
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

        assert bundled.find_bundled_binary("ffmpeg") == str(bin_dir / "ffmpeg")

    def test_returns_none_when_bin_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
        assert bundled.find_bundled_binary("nope") is None


class TestResolveBinary:
    def test_prefers_bundled_over_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bin_dir = tmp_path / bundled.BUNDLED_BIN_SUBDIR
        bin_dir.mkdir()
        bundled_path = bin_dir / "ffmpeg.exe"
        bundled_path.write_bytes(b"")
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setattr("spotify_video_combiner.bundled.shutil.which", lambda _: "/path/ffmpeg")

        assert bundled.resolve_binary("ffmpeg") == str(bundled_path)

    def test_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr("sys._MEIPASS", raising=False)
        monkeypatch.setattr("spotify_video_combiner.bundled.shutil.which", lambda n: f"/path/{n}")

        assert bundled.resolve_binary("ffmpeg") == "/path/ffmpeg"

    def test_returns_none_when_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr("sys._MEIPASS", raising=False)
        monkeypatch.setattr("spotify_video_combiner.bundled.shutil.which", lambda _: None)

        assert bundled.resolve_binary("ffmpeg") is None
