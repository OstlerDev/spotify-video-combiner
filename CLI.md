# Command Line Interface (CLI) & Advanced Usage

This document covers the Command Line Interface (CLI) for `spotify-video-combiner`, as well as manual installation instructions, how the system works under the hood, and development details.

## Manual Installation

For Linux, macOS, or anyone who prefers to manage their own Python environment without using the bundled `.exe`.

**Prerequisites** (must be on `PATH`):
- Python 3.11+
- ffmpeg (https://ffmpeg.org/download.html)

**Install**:

```bash
git clone https://github.com/OstlerDev/spotify-video-combiner
cd spotify-video-combiner
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

You now have three executables on your PATH:
- `svc` — the CLI
- `svc-gui` — the Tkinter GUI
- `zotify` — the underlying audio downloader

*(Note on Python 3.14: If pip mis-resolves Pillow, fix it with `pip install --force-reinstall --no-cache-dir Pillow`.)*

### Windows Install Script (From Source)

If you have Python 3.11+ and want the CLI **and** GUI without downloading the single `.exe`:

```powershell
git clone https://github.com/OstlerDev/spotify-video-combiner
cd spotify-video-combiner
.\install.ps1
```

This creates a virtual environment, installs dependencies, and checks for ffmpeg, offering to install it via winget if missing.

---

## CLI Reference

The `svc` command provides several subcommands for downloading and building videos.

```bash
# Authorise this app to read your Spotify (one-time)
svc signin

# All-in-one: download + build
svc all <playlist-url> [--workdir DIR] [--output FILE] [--font FILE] [--zotify-arg=ARG]...

# Just download audio + cover art
svc download <playlist-url> [--workdir DIR] [--zotify-arg=ARG]...

# Just build the MP4 from a previously-downloaded workdir
svc build <workdir> [--output FILE] [--font FILE]

# Forget the cached session
svc signout
```

`<playlist-url>` accepts any of: 
- an `open.spotify.com` URL
- a `spotify:playlist:` URI
- a bare 22-character playlist ID.

`--zotify-arg` is repeatable and passes flags through verbatim to the underlying zotify downloader:

```bash
svc download <url> --zotify-arg=--download-format=mp3
svc download <url> --zotify-arg=--bulk-wait-time=30
```

### Working Directory Layout

By default, the tool creates an output folder structure. The audio files themselves are the source of truth — there is no separate manifest file.

```
output/<Playlist Name>/
├── <NN>.<spotify-id>.ogg                 # zotify output: audio with embedded metadata
├── <NN>.<spotify-id>.cover.jpg           # cover art extracted from the audio file
├── slides/<NNN>_<spotify-id>.png         # rendered 1920x1080 slide per track
├── segments/<NNN>_<spotify-id>.mp4       # encoded per-track MP4 segments
└── <Playlist Name>.mp4                   # final concatenated video
```

Re-running the tool picks up where it left off. A re-run after a partial failure only redoes what's missing, avoiding redundant downloads and encoding.

---

## How It Works

### Architecture Stack

- **`zotify`** — Handles playlist scanning, per-track audio download, and metadata embedding. We make zero Spotify Web API calls ourselves, dodging rate limits entirely.
- **`music_tag`** — Reads tags + cover art back out of zotify's downloads format-independently.
- **`Pillow`** — Composes a 1080p slide per track (blurred background, centered cover, auto-sized text).
- **`ffmpeg`** — Encodes fixed-image segments. We run libx264 with `-preset veryfast -r 2 -tune stillimage` for extreme speed. The final concat step uses `-c copy` (stream-copy, no re-encode).
- **`PyInstaller`** — Used for the Windows `.exe` bundle, packaging ffmpeg, zotify, and a Python runtime into a single binary.

### Module Map

| Module | Responsibility |
|---|---|
| `auth` | Sign-in / sign-out via zotify's `OAuth` + librespot `Session`. |
| `audio` | Subprocess wrapper around zotify. |
| `tracks` | Reads metadata + extracts cover art. |
| `slides` | Pillow-based slide renderer. |
| `video` | ffmpeg subprocess wrapper. |
| `pipeline` | Orchestration (`download_playlist` and `build_video`). |
| `processes` | Subprocess runners + stream-to-log helpers. |
| `cli` | Click adapter. |
| `gui` | Tkinter GUI. |
| `bundled` | Locates binaries inside frozen builds. |
| `installer` | Self-bootstrap zotify. |

---

## Development

To set up the development environment:

```bash
pip install -e ".[dev]"
pytest                                                            # run test suite
ruff check src tests                                              # lint code
pytest --cov=spotify_video_combiner --cov-report=term-missing     # check coverage
```

The test suite covers all major components, including file idempotency, slide rendering, and a real ffmpeg integration test.

### Building the Windows `.exe`

```powershell
pip install -e ".[build]"
.\build_exe.ps1
```

This downloads a static ffmpeg build and runs PyInstaller against `svc.spec`, outputting `dist\svc-gui.exe`.
