# spotify-video-combiner

Take a Spotify playlist, download every track's audio + high-resolution cover art, and combine the lot into a single MP4 — built specifically for **playing Spotify playlists in VRChat worlds** by uploading the video as an unlisted YouTube link.

The output is a 1080p H.264 + AAC video where each track is shown as a still slide (cover art + track title + artist), back-to-back with hard cuts, in playlist order.

```
[ Spotify playlist URL ]
        │
        ▼  spotipy (Web API)             ←  metadata, cover-art URLs, playlist order
[ playlist.json manifest ]
        │
        ▼  zotify  +  HTTP                ←  per-track audio & cover-art download
[ audio/<id>.ogg, covers/<id>.jpg ]
        │
        ▼  Pillow                         ←  one slide PNG per track
[ slides/<index>_<id>.png ]
        │
        ▼  ffmpeg (loop image + audio)    ←  per-track MP4 segments
[ segments/<index>_<id>.mp4 ]
        │
        ▼  ffmpeg concat demuxer (-c copy)
[ <Playlist>.mp4 ]                        ←  upload to YouTube unlisted, paste URL into VRChat
```

There are three ways to run this, in order of "least to do" → "most flexible":

1. **[Single .exe (Windows)](#1-single-exe-windows)** — double-click `svc-gui.exe` and you're done. Includes ffmpeg + zotify, no Python required.
2. **[Install script (Windows)](#2-install-script-windows-from-source)** — one PowerShell command sets up a venv with everything.
3. **[Manual install (any OS)](#3-manual-install-any-os)** — for developers, advanced users, or non-Windows.

You'll need a Spotify Premium account (zotify uses it to download audio) and free Spotify Web API credentials (read once on first run; see [Spotify credentials](#spotify-credentials)).

---

## 1. Single .exe (Windows)

Download `svc-gui.exe` from the [latest release](https://github.com/OstlerDev/spotify-video-combiner/releases) (or build it yourself with `build_exe.ps1` — see below). Double-click to launch.

The bundle contains:
- The Tkinter GUI
- A full Python runtime
- Every Python dependency (zotify, librespot, spotipy, Pillow, ...)
- A static `ffmpeg.exe`

So nothing else to install — just run.

**First run:** the GUI prompts you to paste your Spotify Web API Client ID + Secret (one-time setup, stored at `%APPDATA%\spotify-video-combiner\credentials.env`). When the first track download starts, zotify pops a console window asking for your Spotify Premium **username + password** (also one-time; cached afterward).

To build the `.exe` yourself:

```powershell
.\install.ps1            # create venv, install everything
.\build_exe.ps1          # downloads ffmpeg, runs PyInstaller
# → dist\svc-gui.exe (~64 MB)
```

## 2. Install script (Windows from source)

If you have Python 3.11+ installed and want the CLI **and** GUI without bundling everything into a single .exe:

```powershell
git clone https://github.com/OstlerDev/spotify-video-combiner
cd spotify-video-combiner

# Either double-click install.bat, or:
.\install.ps1
```

The script:
1. Finds your Python 3.11+ installation
2. Creates `.venv\` next to itself
3. Installs the package + zotify + all deps
4. Checks for ffmpeg, offering `winget install Gyan.FFmpeg` if missing

When it finishes, activate the venv and you have both interfaces:

```powershell
. .venv\Scripts\Activate.ps1
svc-gui                                                # GUI
svc all https://open.spotify.com/playlist/<id>         # CLI
```

## 3. Manual install (any OS)

For Linux, macOS, or anyone who prefers to manage their own environment.

**Prerequisites** (must be on `PATH`):

| Tool | Why |
|---|---|
| Python 3.11+ | Runtime. |
| ffmpeg | Encodes per-track segments + concat. https://ffmpeg.org/download.html |

**Install** (zotify gets pulled in automatically as a dependency):

```bash
git clone https://github.com/OstlerDev/spotify-video-combiner
cd spotify-video-combiner
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

You now have three executables on PATH:
- `svc` — the CLI (with `download`, `build`, `all` subcommands)
- `svc-gui` — the Tkinter GUI
- `zotify` — the underlying audio downloader (rarely called directly)

> **Heads-up on Python 3.14:** Pillow 12.0+ ships official wheels for Python 3.14, but if pip ever mis-resolves and installs mismatched wheels (common when both system Python and Anaconda are present), you'll see `ImportError: cannot import name '_imaging' from 'PIL'`. Fix it with `pip install --force-reinstall --no-cache-dir Pillow`.

---

## Spotify credentials

Two sets of credentials are needed, used for different things:

| Credential | What it's for | How to set it |
|---|---|---|
| **Spotify Web API app** (free) | Read playlist metadata, cover art URLs | Get from https://developer.spotify.com/dashboard, paste into the GUI's credentials dialog or `credentials.env` |
| **Spotify Premium account** | Download track audio (handled by zotify) | zotify prompts you on its first run |

For the Web API, the tool searches in this order:

1. Environment variables (`SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`)
2. `./credentials.env` in the current directory (project-local override)
3. `<user-config>/spotify-video-combiner/credentials.env`:
   - Windows: `%APPDATA%\spotify-video-combiner\credentials.env`
   - macOS: `~/Library/Application Support/spotify-video-combiner/credentials.env`
   - Linux: `~/.config/spotify-video-combiner/credentials.env`

If nothing is found, a blank template file is auto-created at the user-config location and the tool prints a clear error pointing you at it. The GUI shows a setup dialog instead.

> **Note on legality:** zotify uses your own Premium account credentials to stream and decrypt audio, which is in a grey area with respect to Spotify's Terms of Service. This project is for personal, archival, and accessibility use only. Don't redistribute the resulting MP4s.

---

## CLI reference

```bash
# All-in-one: download + build
svc all <playlist-url> [--workdir DIR] [--output FILE] [--font FILE] [--zotify-arg=ARG]...

# Just download audio + cover art
svc download <playlist-url> [--workdir DIR] [--zotify-arg=ARG]...

# Just build the MP4 from a previously-downloaded workdir
svc build <workdir> [--output FILE] [--font FILE]
```

`<playlist-url>` accepts any of: an `open.spotify.com` URL, a `spotify:playlist:` URI, or a bare 22-character playlist ID.

`--zotify-arg` is repeatable and passes flags through verbatim:

```bash
svc download <url> --zotify-arg=--download-format=mp3
svc download <url> --zotify-arg=--bulk-wait-time=30
```

### Working directory layout

```
output/<Playlist Name>/
├── playlist.json                         # manifest (playlist + tracks + relative paths)
├── audio/<spotify-id>.ogg                # one audio file per track (zotify output)
├── covers/<spotify-id>.jpg               # high-resolution cover art (Spotify CDN)
├── slides/<NNN>_<spotify-id>.png         # rendered 1920x1080 slide per track
├── segments/<NNN>_<spotify-id>.mp4       # encoded per-track MP4 segments
└── <Playlist Name>.mp4                   # final concatenated video
```

Every layer is **idempotent** — re-running after a partial failure only redoes what's missing.

---

## How it works

### Why this stack?

- **`spotipy`** for metadata — clean access to playlist ordering and the highest-resolution cover art URL via the Spotify Web API. Free developer app, no user login.
- **`zotify`** for audio — only practical way to download Spotify-quality audio. Uses your own Premium credentials via [`librespot`](https://github.com/librespot-org/librespot). Original `zotify-dev/zotify` is abandoned; this depends on the actively-maintained [`DraftKinner/zotify`](https://github.com/DraftKinner/zotify) fork.
- **`Pillow`** for slides — composes a 1080p frame per track: blurred dimmed cover-art background + centered cover + auto-sized title/artist text. Renders to a single PNG so ffmpeg only deals with images, never fonts (avoiding fontconfig hell on Windows).
- **`ffmpeg`** for encoding — `-loop 1 ... -shortest` produces a fixed-image segment matching each track's audio length. Every segment is encoded with identical codec parameters, so the final concat step uses `-c copy` (stream-copy, no re-encode).
- **PyInstaller** for the `.exe` — `--onefile` produces a single binary; ffmpeg is bundled under `binaries/`, zotify ships as a Python package, and the bundle re-enters itself with a `--zotify-mode` flag (allocating a console at runtime via `AllocConsole`) when zotify needs to run.

### Module map

| Module | Responsibility |
|---|---|
| `manifest` | The on-disk `playlist.json` data model and filename sanitisation. |
| `spotify` | spotipy wrapper that returns our domain `Playlist` object. |
| `audio` | Subprocess wrapper around zotify (auto-installs it via pip if missing). |
| `covers` | Plain HTTP cover-art downloader. |
| `slides` | Pillow-based slide renderer (blurred bg + cover + text). |
| `video` | ffmpeg subprocess wrapper: per-track segment encode + concat. |
| `pipeline` | Orchestration: `download_playlist` and `build_video` workflows. |
| `cli` | Click adapter (CLI). Also handles the `--zotify-mode` proxy in frozen builds. |
| `gui` | Tkinter GUI with threaded pipeline runner + credentials setup dialog. |
| `bundled` | Locate binaries that may live inside `sys._MEIPASS`. |
| `installer` | Self-bootstrap zotify via pip when missing. |
| `config` | Load `credentials.env` from project-local + user-config locations. |
| `errors` | `UserFacingError` hierarchy that the CLI renders cleanly (no traceback). |

External processes (`zotify`, `ffmpeg`) are wrapped behind injectable `runner` callables so the entire pipeline is unit-testable without spawning real subprocesses.

---

## Development

```bash
pip install -e ".[dev]"
pytest                                                            # full unit + ffmpeg integration suite (~2s)
ruff check src tests                                              # lint
pytest --cov=spotify_video_combiner --cov-report=term-missing     # coverage
```

The test suite (~125 tests, 92% coverage) covers manifest serialisation, URL parsing, command construction, file idempotency, slide rendering, frozen-mode dispatch, credentials file parsing, error translation, and a real ffmpeg integration test that produces a tiny end-to-end MP4. External services (Spotify API, zotify subprocess, network) are mocked.

### Building the .exe

```powershell
pip install -e ".[build]"
.\build_exe.ps1
```

This downloads a static ffmpeg build into `build/`, then runs PyInstaller against `svc.spec`. Output: `dist\svc-gui.exe` (~64 MB single file).

---

## YouTube + VRChat tips

- YouTube has a **12-hour video duration limit** for verified accounts (15 minutes for unverified). Most playlists fit well under this.
- Upload as **Unlisted** so anyone with the link can watch but it's not publicly searchable.
- In VRChat, paste the YouTube URL into a video player — most worlds use yt-dlp under the hood, which handles unlisted videos fine.
- For maximum compatibility, the encoder uses H.264 high@4.0, yuv420p, AAC stereo, and `+faststart` muxing. This plays in every VRChat video player implementation as well as YouTube/Twitch/Discord previews.

## License

[MIT](LICENSE)
