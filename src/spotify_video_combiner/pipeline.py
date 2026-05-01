"""High-level workflows: ``download_playlist`` and ``build_video``.

Each Click subcommand is a thin adapter around one of these functions, which
keeps the CLI free of business logic and makes the workflow testable end-to-end
without spawning a subprocess.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from .audio import ZotifyDownloader
from .covers import download_covers
from .manifest import Playlist, manifest_path_for, safe_filename
from .slides import SlideRenderer
from .spotify import SpotifyMetadata
from .video import FFmpegVideoBuilder, Segment

# Optional logger callback so the CLI can render Click-styled output without
# this module depending on Click.
LogFn = Callable[[str], None]


def _noop(_: str) -> None:  # pragma: no cover - trivial
    pass


def default_workdir(playlist_name: str, root: Path = Path("output")) -> Path:
    return root / safe_filename(playlist_name)


def download_playlist(
    playlist_url: str,
    workdir: Path | None = None,
    *,
    metadata: SpotifyMetadata | None = None,
    downloader: ZotifyDownloader | None = None,
    cover_fetcher: Callable[..., dict[str, Path]] = download_covers,
    log: LogFn = _noop,
    zotify_extra: Iterable[str] | None = None,
) -> tuple[Playlist, Path]:
    """Fetch metadata, download cover art + audio, write the manifest.

    Returns ``(playlist, workdir)``. Idempotent — files already on disk are
    detected and not re-downloaded, so re-running after a partial failure picks
    up where it left off.
    """
    metadata = metadata or SpotifyMetadata()
    downloader = downloader or ZotifyDownloader(
        extra_args=list(zotify_extra or []), log=log
    )

    log(f"Fetching playlist metadata: {playlist_url}")
    playlist = metadata.fetch_playlist(playlist_url)
    workdir = workdir or default_workdir(playlist.name)
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"Playlist: {playlist.name} — {len(playlist.tracks)} tracks")
    log(f"Workdir:  {workdir}")

    cover_dir = workdir / "covers"
    audio_dir = workdir / "audio"

    log("Downloading cover art…")
    cover_paths = cover_fetcher(playlist.tracks, cover_dir)
    log(f"  cover art: {len(cover_paths)}/{len(playlist.tracks)}")

    log("Downloading audio via zotify…")
    audio_paths = downloader.download_tracks(playlist.tracks, audio_dir)
    log(f"  audio:     {len(audio_paths)}/{len(playlist.tracks)}")

    for track in playlist.tracks:
        if (audio := audio_paths.get(track.spotify_id)) is not None:
            track.audio_path = audio.relative_to(workdir).as_posix()
        if (cover := cover_paths.get(track.spotify_id)) is not None:
            track.cover_path = cover.relative_to(workdir).as_posix()

    manifest = manifest_path_for(workdir)
    playlist.write(manifest)
    log(f"Manifest written: {manifest}")

    missing = [t for t in playlist.tracks if not t.audio_path]
    if missing:
        log(f"WARNING: {len(missing)} tracks have no audio file. They will be skipped.")
        for track in missing:
            log(f"  - {track.slug}")

    return playlist, workdir


def build_video(
    workdir: Path,
    *,
    output: Path | None = None,
    renderer: SlideRenderer | None = None,
    builder: FFmpegVideoBuilder | None = None,
    log: LogFn = _noop,
) -> Path:
    """Render slides, encode per-track segments, and concat into a single MP4.

    Idempotent: slides and segments are skipped if they already exist on disk,
    so re-running after fixing a single bad track only re-encodes that track.
    """
    renderer = renderer or SlideRenderer()
    builder = builder or FFmpegVideoBuilder(log=log)

    playlist = Playlist.read(manifest_path_for(workdir))
    output = output or (workdir / f"{safe_filename(playlist.name)}.mp4")

    slides_dir = workdir / "slides"
    segments_dir = workdir / "segments"
    slides_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    segments: list[Path] = []
    skipped: list[str] = []

    for track in playlist.tracks:
        if not track.audio_path:
            skipped.append(track.slug)
            continue
        audio_path = workdir / track.audio_path
        if not audio_path.is_file():
            skipped.append(track.slug)
            continue
        cover_path = (workdir / track.cover_path) if track.cover_path else None

        stem = f"{track.index:03d}_{track.spotify_id}"
        slide_path = slides_dir / f"{stem}.png"
        segment_path = segments_dir / f"{stem}.mp4"

        if not slide_path.is_file():
            renderer.render_to_file(cover_path, track.name, track.artists_joined, slide_path)

        if not segment_path.is_file():
            log(f"Encoding {track.index:02d}/{len(playlist.tracks)}: {track.slug}")
            builder.encode_segment(Segment(slide_path, audio_path, segment_path))
        else:
            log(f"Cached    {track.index:02d}/{len(playlist.tracks)}: {track.slug}")

        segments.append(segment_path)

    if not segments:
        raise RuntimeError(
            "No tracks to combine — did `download` succeed? "
            "Check the manifest and audio/ folder."
        )
    if skipped:
        log(f"Skipped {len(skipped)} tracks without audio: {', '.join(skipped)}")

    log(f"Concatenating {len(segments)} segments → {output}")
    builder.concat(segments, output)
    log(f"Done: {output}")
    return output
