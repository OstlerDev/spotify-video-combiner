from __future__ import annotations

from pathlib import Path

from PIL import Image

from spotify_video_combiner.slides import SlideRenderer, SlideStyle, find_default_font


class TestSlideRenderer:
    def test_render_with_cover_returns_correct_size(self, make_image) -> None:
        cover = make_image()
        renderer = SlideRenderer(style=SlideStyle(size=(640, 360)))

        img = renderer.render(cover, "Title", "Artist")

        assert isinstance(img, Image.Image)
        assert img.size == (640, 360)
        assert img.mode == "RGB"

    def test_render_without_cover_uses_solid_background(self, tmp_path: Path) -> None:
        renderer = SlideRenderer(
            style=SlideStyle(size=(320, 240), background_color=(10, 20, 30)),
            font_path=find_default_font(),
        )
        img = renderer.render(None, "Title", "Artist")
        assert img.size == (320, 240)
        # Top-left corner should be the solid background colour (text/cover are centred).
        assert img.getpixel((1, 1)) == (10, 20, 30)

    def test_missing_cover_path_treated_as_no_cover(self, tmp_path: Path) -> None:
        renderer = SlideRenderer(style=SlideStyle(size=(320, 240)))
        img = renderer.render(tmp_path / "does-not-exist.png", "Title", "Artist")
        assert img.size == (320, 240)

    def test_render_to_file_writes_png(self, make_image, tmp_path: Path) -> None:
        cover = make_image()
        out = tmp_path / "out.png"

        renderer = SlideRenderer(style=SlideStyle(size=(640, 360)))
        result = renderer.render_to_file(cover, "Title", "Artist", out)

        assert result == out
        assert out.is_file()
        with Image.open(out) as opened:
            assert opened.size == (640, 360)
            assert opened.format == "PNG"

    def test_long_title_renders_without_overflow(self, make_image) -> None:
        cover = make_image()
        style = SlideStyle(size=(800, 450), text_side_margin=40)
        renderer = SlideRenderer(style=style)
        # A pathologically long title used to crash the auto-fit loop; this
        # asserts it still produces a 1080p-style frame at the requested size.
        img = renderer.render(cover, "Q" * 400, "Artist Name")
        assert img.size == style.size

    def test_default_style_is_1080p(self) -> None:
        assert SlideStyle().size == (1920, 1080)


class TestFindDefaultFont:
    def test_returns_path_or_none(self) -> None:
        result = find_default_font()
        assert result is None or Path(result).is_file()
