"""Download a Spotify playlist's audio + cover art and assemble it into a single MP4."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spotify-video-combiner")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
