from __future__ import annotations

from pathlib import Path

from spotify_video_combiner.covers import _ext_from_url, download_covers
from spotify_video_combiner.manifest import Track


class TestExtFromUrl:
    def test_jpg(self) -> None:
        assert _ext_from_url("https://example.com/foo.jpg") == "jpg"

    def test_png_with_query(self) -> None:
        assert _ext_from_url("https://example.com/foo.png?cache=1") == "png"

    def test_unknown_falls_back_to_jpg(self) -> None:
        assert _ext_from_url("https://example.com/foo") == "jpg"
        assert _ext_from_url("https://example.com/foo.bin") == "jpg"

    def test_uppercase_normalised(self) -> None:
        assert _ext_from_url("https://example.com/foo.PNG") == "png"


class TestDownloadCovers:
    def test_downloads_each_track(self, sample_tracks: list[Track], tmp_path: Path) -> None:
        calls: list[str] = []

        def fake_fetch(url: str) -> bytes:
            calls.append(url)
            return f"bytes-for-{url}".encode()

        result = download_covers(sample_tracks, tmp_path, fetcher=fake_fetch)

        assert set(result) == {"track1", "track2"}
        assert (tmp_path / "track1.jpg").read_bytes() == b"bytes-for-https://i.scdn.co/image/abc.jpg"
        assert (tmp_path / "track2.png").read_bytes() == b"bytes-for-https://i.scdn.co/image/def.png"
        assert len(calls) == 2

    def test_skips_existing(self, sample_tracks: list[Track], tmp_path: Path) -> None:
        (tmp_path).mkdir(exist_ok=True)
        (tmp_path / "track1.jpg").write_bytes(b"already")

        calls: list[str] = []

        def fake_fetch(url: str) -> bytes:
            calls.append(url)
            return b"new"

        result = download_covers(sample_tracks, tmp_path, fetcher=fake_fetch)

        assert calls == ["https://i.scdn.co/image/def.png"]
        assert (tmp_path / "track1.jpg").read_bytes() == b"already"
        assert result["track1"] == tmp_path / "track1.jpg"

    def test_skips_tracks_without_cover_url(self, tmp_path: Path) -> None:
        track = Track(
            index=1,
            spotify_id="x",
            spotify_url="u",
            name="n",
            artists=["a"],
            album="al",
            duration_ms=1000,
            cover_url=None,
        )
        result = download_covers([track], tmp_path, fetcher=lambda _: b"unused")
        assert result == {}
