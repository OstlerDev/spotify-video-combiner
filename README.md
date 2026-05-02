# spotify-video-combiner

Take a Spotify playlist, download every track's audio + high-resolution cover art, and combine the lot into a single MP4 — built specifically for **playing Spotify playlists in VRChat worlds** by uploading the video as an unlisted YouTube link.

The output is a 1080p H.264 + AAC video where each track is shown as a still slide (cover art + track title + artist), back-to-back with hard cuts, in playlist order.

```
[ Spotify playlist URL ]
        │
        ▼  zotify (one subprocess call)   ←  sign-in + scan + audio + embedded cover-art + tags
[ <NN>.<spotify_id>.ogg  (title/artist/album/cover all embedded) ]
        │
        ▼  music_tag                      ←  read tags + extract cover to <NN>.<spotify_id>.cover.jpg
        │
        ▼  Pillow                         ←  one slide PNG per track
[ slides/<NNN>_<id>.png ]
        │
        ▼  ffmpeg (loop image + audio)    ←  per-track MP4 segments
[ segments/<NNN>_<id>.mp4 ]
        │
        ▼  ffmpeg concat demuxer (-c copy)
[ <Playlist>.mp4 ]                        ←  upload to YouTube unlisted, paste URL into VRChat
```

We make zero Spotify Web API calls of our own: zotify handles the entire download (auth, playlist scan, audio + cover art, metadata) in one subprocess invocation, and we just read the resulting files back. There is no JSON manifest — the audio files on disk are the source of truth for both the download and build phases.

There are three ways to run this, in order of "least to do" → "most flexible":

1. **[Single .exe (Windows)](#1-single-exe-windows)** — double-click `svc-gui.exe` and you're done. Includes ffmpeg + zotify, no Python required.
2. **[Install script (Windows)](#2-install-script-windows-from-source)** — one PowerShell command sets up a venv with everything.
3. **[Manual install (any OS)](#3-manual-install-any-os)** — for developers, advanced users, or non-Windows.

You'll need a Spotify Premium account. The first time you run the app it pops a browser tab so you can authorise it; the resulting session is cached locally and reused on every subsequent run (see [Signing in](#signing-in)).

---

## 1. Single .exe (Windows)

Download `svc-gui.exe` from the [latest release](https://github.com/OstlerDev/spotify-video-combiner/releases) (built and attached automatically by [`.github/workflows/release.yml`](.github/workflows/release.yml) every time a release is published — or build it yourself with `build_exe.ps1`, see below). Double-click to launch.

The bundle contains:
- The Tkinter GUI
- A full Python runtime
- Every Python dependency (zotify, librespot, music_tag, Pillow, ...)
- A static `ffmpeg.exe`

So nothing else to install — just run.

**First run:** click **Sign In** in the GUI. A browser tab opens at `accounts.spotify.com`; sign in and approve the app, and a localhost callback returns control to the GUI. The resulting session is cached at `%APPDATA%\Zotify\credentials.json` and reused for both metadata reads and audio downloads. No developer credentials, no console pop-ups — one click.

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
svc signin                                             # one-time: authorise this app
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
- `svc` — the CLI (with `signin`, `signout`, `download`, `build`, `all` subcommands)
- `svc-gui` — the Tkinter GUI
- `zotify` — the underlying audio downloader (rarely called directly)

> **Heads-up on Python 3.14:** Pillow 12.0+ ships official wheels for Python 3.14, but if pip ever mis-resolves and installs mismatched wheels (common when both system Python and Anaconda are present), you'll see `ImportError: cannot import name '_imaging' from 'PIL'`. Fix it with `pip install --force-reinstall --no-cache-dir Pillow`.

---

## Signing in

A single sign-in covers everything — both metadata reads and audio downloads use the same OAuth session, so there are no developer credentials to manage and no separate username prompt.

| Interface | Sign in | Sign out |
|---|---|---|
| GUI | Click **Sign In** | Click **Sign Out** |
| CLI | `svc signin` | `svc signout` |

Either way, your default browser opens at `accounts.spotify.com/authorize`, and after you approve a localhost callback (`http://127.0.0.1:4381/login`) writes a librespot credentials file at:

- Windows: `%APPDATA%\Zotify\credentials.json`
- macOS: `~/Library/Application Support/Zotify/credentials.json`
- Linux: `$XDG_CONFIG_HOME/zotify/credentials.json` or `~/.config/zotify/credentials.json`

That same file is what the underlying [`zotify`](https://github.com/DraftKinner/zotify) tool reads, so signing in here works for direct `zotify` invocations too (and vice-versa).

> **Note on legality:** the audio downloader uses your own Premium account credentials to stream and decrypt audio, which is in a grey area with respect to Spotify's Terms of Service. This project is for personal, archival, and accessibility use only. Don't redistribute the resulting MP4s.

---

## CLI reference

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

`<playlist-url>` accepts any of: an `open.spotify.com` URL, a `spotify:playlist:` URI, or a bare 22-character playlist ID.

`--zotify-arg` is repeatable and passes flags through verbatim:

```bash
svc download <url> --zotify-arg=--download-format=mp3
svc download <url> --zotify-arg=--bulk-wait-time=30
```

### Working directory layout

```
output/<Playlist Name>/
├── <NN>.<spotify-id>.ogg                 # zotify output: audio with embedded title/artist/album/cover-art
├── <NN>.<spotify-id>.cover.jpg           # cover art extracted from the audio file (lazy, on first build)
├── slides/<NNN>_<spotify-id>.png         # rendered 1920x1080 slide per track
├── segments/<NNN>_<spotify-id>.mp4       # encoded per-track MP4 segments
└── <Playlist Name>.mp4                   # final concatenated video
```

The audio files themselves are the source of truth — no separate `playlist.json` manifest. Re-running picks up where it left off: zotify's own `--skip-previous` skips already-downloaded tracks (it scans embedded `spotid` metadata), and the slide / segment / concat steps each skip outputs that already exist on disk. A re-run after a partial failure only redoes what's missing.

---

## How it works

### Why this stack?

- **`zotify` (one-stop downloader)** — its `OAuth` + `Session` classes drive the PKCE flow against `accounts.spotify.com` for sign-in (we call those classes directly so the GUI button works), and a single `zotify` subprocess invocation then handles the entire playlist: scanning, per-track audio download, and embedding `title` / `artist` / `album` / cover-art into each output file as standard metadata. We make zero Spotify Web API calls ourselves, which dodges the librespot-OAuth Web-API rate limits entirely. Original `zotify-dev/zotify` is abandoned; this depends on the actively-maintained [`DraftKinner/zotify`](https://github.com/DraftKinner/zotify) fork.
- **`music_tag`** for reading back the tags + cover art zotify embedded — a thin, format-independent wrapper over mutagen so we don't have to care whether the user picked OGG, MP3, M4A, or FLAC.
- **`Pillow`** for slides — composes a 1080p frame per track: blurred dimmed cover-art background + centered cover + auto-sized title/artist text. Renders to a single PNG so ffmpeg only deals with images, never fonts (avoiding fontconfig hell on Windows).
- **`ffmpeg`** for encoding — `-loop 1 ... -shortest` produces a fixed-image segment matching each track's audio length. Because every frame within a segment is byte-identical, we run libx264 with `-preset veryfast -r 2 -tune stillimage`: a 4-minute track becomes 480 frames instead of 7,200, and the encoder spends no time on motion estimation it would only confirm is zero. Real-world segments encode in 1-3 s on a modern CPU. Every segment is produced with identical codec parameters, so the final concat step uses `-c copy` (stream-copy, no re-encode).
- **PyInstaller** for the `.exe` — `--onefile` produces a single binary; ffmpeg is bundled under `binaries/`, zotify ships as a Python package, and the bundle re-enters itself with a `--zotify-mode` flag when zotify needs to run.

### Module map

| Module | Responsibility |
|---|---|
| `auth` | Sign-in / sign-out via zotify's `OAuth` + librespot `Session`. Caches the session for in-process reuse. |
| `audio` | Subprocess wrapper around zotify; one call downloads the whole playlist. Auto-installs zotify via pip if missing. |
| `tracks` | Reads `music_tag` metadata back out of zotify's downloads + extracts cover art to sibling `.jpg/.png` files. |
| `slides` | Pillow-based slide renderer (blurred bg + cover + title/artist text). |
| `video` | ffmpeg subprocess wrapper: per-track segment encode + concat. |
| `pipeline` | Orchestration: `download_playlist` (zotify) and `build_video` (slides + ffmpeg). |
| `processes` | Window-suppressing subprocess runners + the `LogChannels` split (pipeline progress vs. subprocess output). |
| `cli` | Click adapter (CLI). Also handles the `--zotify-mode` proxy in frozen builds. |
| `gui` | Tkinter GUI with threaded pipeline runner, sign-in dialog, and a paned dual-log widget. |
| `bundled` | Locate binaries that may live inside `sys._MEIPASS`. |
| `installer` | Self-bootstrap zotify via pip when missing. |
| `errors` | `UserFacingError` hierarchy that the CLI renders cleanly (no traceback). |

External processes (`zotify`, `ffmpeg`) are wrapped behind injectable `runner` callables so the entire pipeline is unit-testable without spawning real subprocesses. From the GUI, those runners stream child output line-by-line into the bottom log pane (capped to ~500 lines so it never grows unbounded), while the top pane shows only high-level pipeline progress.

---

## Development

```bash
pip install -e ".[dev]"
pytest                                                            # full unit + ffmpeg integration suite (~2s)
ruff check src tests                                              # lint
pytest --cov=spotify_video_combiner --cov-report=term-missing     # coverage
```

The test suite (~140 tests) covers manifest serialisation, URL parsing, command construction, file idempotency, slide rendering, frozen-mode dispatch, sign-in state, error translation, the subprocess window-suppression + stream-to-log helpers, and a real ffmpeg integration test that produces a tiny end-to-end MP4. External services (Spotify API, zotify subprocess, network) are mocked.

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
