"""Render the per-track still image displayed during the song.

Layout (at 1920x1080):

    +------------------------------------------------------+
    |  [ cover art scaled to fill, blurred, dimmed ]       |
    |                                                      |
    |              +----------------------+                |
    |              |                      |                |
    |              |     cover art        |                |
    |              |     (square)         |                |
    |              |                      |                |
    |              +----------------------+                |
    |                                                      |
    |              Track Title (auto-fit)                  |
    |              Artist Name(s)                          |
    |                                                      |
    +------------------------------------------------------+

The single rendered PNG is then handed to ffmpeg with ``-loop 1`` to produce
a video segment matching the audio's duration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# System font search order. First hit wins. None of these are bundled; if
# nothing is found we fall back to PIL's default bitmap font.
_FONT_CANDIDATES: tuple[str, ...] = (
    # Windows
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    # macOS
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    # Linux (common Debian/Ubuntu/Fedora paths)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def find_default_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


@dataclass(frozen=True)
class SlideStyle:
    """Visual parameters for a slide. Defaults are tuned for 1080p."""

    size: tuple[int, int] = (1920, 1080)
    cover_size: int = 720          # square cover art edge length
    cover_top: int = 90            # y of the cover top edge
    title_y: int = 860             # baseline-ish y for the title
    artist_y: int = 960            # baseline-ish y for the artist line
    title_max_size: int = 76
    artist_max_size: int = 48
    text_side_margin: int = 80     # min horizontal margin for text
    bg_blur_radius: int = 40
    bg_dim: float = 0.35           # 0.0 = black, 1.0 = no dim
    title_color: tuple[int, int, int] = (255, 255, 255)
    artist_color: tuple[int, int, int] = (210, 210, 210)
    background_color: tuple[int, int, int] = (0, 0, 0)


class SlideRenderer:
    """Render a slide PIL image for a single track."""

    def __init__(self, style: SlideStyle | None = None, font_path: str | None = None) -> None:
        self.style = style or SlideStyle()
        self._font_path = font_path or find_default_font()

    # --- public API -----------------------------------------------------

    def render(self, cover_path: Path | None, title: str, artist: str) -> Image.Image:
        cover = self._load_cover(cover_path)
        canvas = self._build_background(cover)
        if cover is not None:
            self._paste_cover(canvas, cover)
        self._draw_text(canvas, title, artist)
        return canvas

    def render_to_file(
        self, cover_path: Path | None, title: str, artist: str, out_path: Path
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image = self.render(cover_path, title, artist)
        image.save(out_path, format="PNG", optimize=True)
        return out_path

    # --- internals ------------------------------------------------------

    def _load_cover(self, cover_path: Path | None) -> Image.Image | None:
        if cover_path is None or not Path(cover_path).is_file():
            return None
        return Image.open(cover_path).convert("RGB")

    def _build_background(self, cover: Image.Image | None) -> Image.Image:
        w, h = self.style.size
        if cover is None:
            return Image.new("RGB", (w, h), self.style.background_color)

        # Fill the canvas while preserving aspect ratio (centre-crop).
        cover_w, cover_h = cover.size
        scale = max(w / cover_w, h / cover_h)
        scaled_size = (max(1, int(cover_w * scale)), max(1, int(cover_h * scale)))
        background = cover.resize(scaled_size, Image.LANCZOS)
        left = (background.width - w) // 2
        top = (background.height - h) // 2
        background = background.crop((left, top, left + w, top + h))
        background = background.filter(ImageFilter.GaussianBlur(self.style.bg_blur_radius))

        # Dim the background by alpha-blending toward black.
        dim = Image.new("RGB", background.size, self.style.background_color)
        return Image.blend(dim, background, self.style.bg_dim)

    def _paste_cover(self, canvas: Image.Image, cover: Image.Image) -> None:
        size = self.style.cover_size
        # Square-crop the cover before scaling so non-square inputs still look right.
        cw, ch = cover.size
        edge = min(cw, ch)
        cover = cover.crop(((cw - edge) // 2, (ch - edge) // 2, (cw + edge) // 2, (ch + edge) // 2))
        cover = cover.resize((size, size), Image.LANCZOS)
        x = (canvas.width - size) // 2
        canvas.paste(cover, (x, self.style.cover_top))

    def _draw_text(self, canvas: Image.Image, title: str, artist: str) -> None:
        draw = ImageDraw.Draw(canvas)
        max_text_width = canvas.width - 2 * self.style.text_side_margin

        title_font = self._fit_font(draw, title, max_text_width, self.style.title_max_size)
        artist_font = self._fit_font(draw, artist, max_text_width, self.style.artist_max_size)

        self._draw_centered(draw, title, title_font, self.style.title_y, self.style.title_color)
        self._draw_centered(draw, artist, artist_font, self.style.artist_y, self.style.artist_color)

    def _draw_centered(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        y: int,
        color: tuple[int, int, int],
    ) -> None:
        if not text:
            return
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        x = (self.style.size[0] - width) // 2 - bbox[0]
        draw.text((x, y), text, font=font, fill=color)

    def _fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        max_width: int,
        max_size: int,
    ) -> ImageFont.ImageFont:
        """Return the largest font (down to size 12) that fits ``text`` in ``max_width``."""
        if not text:
            return self._load_font(max_size)
        size = max_size
        while size >= 12:
            font = self._load_font(size)
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return font
            size -= 4
        return self._load_font(12)

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        if self._font_path:
            try:
                return ImageFont.truetype(self._font_path, size=size)
            except OSError:
                pass
        # Fallback bitmap font ignores `size` but at least produces output.
        return ImageFont.load_default()
