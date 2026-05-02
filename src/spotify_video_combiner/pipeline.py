"""High-level workflows: ``download_playlist`` and ``build_video``.

Each Click subcommand is a thin adapter around one of these functions, which
keeps the CLI free of business logic and makes the workflow testable end-to-end
without spawning a subprocess.

The flow is intentionally minimal:

1. ``download_playlist`` shells out to ``zotify``. Zotify handles sign-in
   (re-using our cached credentials), playlist scanning, audio download,
   and embeds title/artist/album/cover-art into each output file. We do
   not make a single Spotify Web API call ourselves.

2. ``build_video`` walks the workdir, reads tags via ``music_tag``,
   renders one slide per track, encodes each slide+audio into an MP4
   segment, and concatenates them into the final video.

There is no JSON manifest: the audio files on disk are the source of truth
for both phases, which means re-running the build phase against an existing
workdir works without any extra metadata file.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from .audio import ZotifyDownloader
from .auth import lookup_playlist_name
from .errors import UserFacingError
from .processes import LogChannels
from .slides import SlideRenderer
from .tracks import Track, read_tracks, safe_filename
from .video import FFmpegVideoBuilder, Segment

# Optional logger callback so the CLI can render Click-styled output without
# this module depending on Click.
LogFn = Callable[[str], None]

_PLAYLIST_ID_RE = re.compile(
    r"(?:spotify:playlist:|open\.spotify\.com/(?:intl-[a-z]+/)?playlist/)([A-Za-z0-9]{22})"
)


class PipelineError(UserFacingError):
    """Raised for pipeline-level problems (no tracks, malformed URL, etc.)."""


def parse_playlist_id(value: str) -> str:
    """Accept a raw playlist ID, ``spotify:playlist:...`` URI, or open.spotify URL."""
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9]{22}", value):
        return value
    match = _PLAYLIST_ID_RE.search(value)
    if not match:
        raise PipelineError(
            f"Could not extract a Spotify playlist ID from {value!r}.\n"
            "Expected one of:\n"
            "  - https://open.spotify.com/playlist/<22-char-id>\n"
            "  - spotify:playlist:<22-char-id>\n"
            "  - <22-char-id>"
        )
    return match.group(1)


def default_workdir(playlist_name: str, root: Path = Path("output")) -> Path:
    return root / safe_filename(playlist_name)


def download_playlist(
    playlist_url: str,
    workdir: Path | None = None,
    *,
    downloader: ZotifyDownloader | None = None,
    channels: LogChannels | None = None,
    zotify_extra: Iterable[str] | None = None,
) -> Path:
    """Hand ``playlist_url`` to zotify and return the workdir it filled.

    If ``workdir`` is ``None`` we ask librespot mercury for the playlist
    name (one cheap call, not subject to Web API rate limits) and default
    to ``./output/<safe-playlist-name>/``. If mercury fails for any
    reason (network, signed-out, weird account state) we fall back to
    the bare playlist ID so the download still proceeds.

    ``channels`` splits high-level progress (``channels.pipeline``) from
    verbose zotify subprocess output (``channels.subprocess``) so the GUI
    can show them in separate panes.
    """
    channels = channels or LogChannels.silent()
    downloader = downloader or ZotifyDownloader(
        extra_args=list(zotify_extra or []), log=channels.subprocess
    )

    if workdir is None:
        playlist_id = parse_playlist_id(playlist_url)
        name = lookup_playlist_name(playlist_id) or playlist_id
        workdir = default_workdir(name)

    channels.pipeline(f"Downloading playlist via zotify -> {workdir}")
    channels.pipeline(f"Note: There is a ~30s pause between song downloads to stay within Spotify rate limits. You may see repeated 'Fetching Track...' logs during api limit pauses.")
    downloader.download(playlist_url, workdir)
    return workdir


def build_video(
    workdir: Path,
    *,
    output: Path | None = None,
    renderer: SlideRenderer | None = None,
    builder: FFmpegVideoBuilder | None = None,
    channels: LogChannels | None = None,
) -> Path:
    """Render slides, encode per-track segments, and concat into a single MP4.

    Idempotent: slides and segments are skipped if they already exist on disk,
    so re-running after fixing a single bad track only re-encodes that track.
    """
    channels = channels or LogChannels.silent()
    renderer = renderer or SlideRenderer()
    builder = builder or FFmpegVideoBuilder(log=channels.subprocess)

    tracks = read_tracks(workdir)
    if not tracks:
        raise PipelineError(
            f"No audio files found in {workdir}. "
            "Run the download step first (or check zotify's output above)."
        )

    output = output or (workdir / f"{safe_filename(workdir.name)}.mp4")

    slides_dir = workdir / "slides"
    segments_dir = workdir / "segments"
    slides_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    channels.pipeline(f"Found {len(tracks)} tracks in {workdir}")
    segments = [
        _encode_track(t, len(tracks), slides_dir, segments_dir, renderer, builder, channels)
        for t in tracks
    ]

    channels.pipeline(f"Concatenating {len(segments)} segments -> {output}")
    builder.concat(segments, output)
    channels.pipeline(f"Done: {output}")
    return output


def _encode_track(
    track: Track,
    total: int,
    slides_dir: Path,
    segments_dir: Path,
    renderer: SlideRenderer,
    builder: FFmpegVideoBuilder,
    channels: LogChannels,
) -> Path:
    stem = f"{track.index:03d}_{track.spotify_id}"
    slide_path = slides_dir / f"{stem}.png"
    segment_path = segments_dir / f"{stem}.mp4"

    if not slide_path.is_file():
        renderer.render_to_file(track.cover_path, track.title, track.artists_joined, slide_path)

    if segment_path.is_file():
        channels.pipeline(f"Cached    {track.index:02d}/{total}: {track.slug}")
    else:
        channels.pipeline(f"Encoding  {track.index:02d}/{total}: {track.slug}")
        builder.encode_segment(
            Segment(slide_path, track.audio_path, segment_path, audio_duration=track.duration)
        )

    return segment_path
