"""Tkinter GUI: paste a Spotify URL, click Combine, watch the MP4 build itself.

Architecture:
- Main thread owns Tk and never blocks; user input + UI updates happen here.
- A worker thread runs the pipeline and pipes log lines into a thread-safe
  ``queue.Queue``.
- A periodic ``after()`` callback drains the queue into the log widget so
  output appears live without any cross-thread Tk calls.

If credentials are missing, a small modal dialog lets the user paste their
Spotify Web API client ID + secret and writes them straight to the on-disk
``credentials.env`` — no need for the user to know the file format or path.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .config import (
    CREDENTIALS_FILENAME,
    CREDENTIALS_TEMPLATE,
    load_credentials_files,
    user_config_dir,
)
from .errors import UserFacingError
from .pipeline import build_video, download_playlist
from .slides import SlideRenderer
from .video import EncodeSettings, FFmpegVideoBuilder

WINDOW_TITLE = "Spotify Video Combiner"
DEFAULT_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "1080p (1920x1080)": (1920, 1080),
    "1440p (2560x1440)": (2560, 1440),
    "720p (1280x720)": (1280, 720),
}
# Maps GUI labels to the zotify ``--audio-format`` flag values. Zotify supports
# {aac, fdk_aac, flac, mp3, opus, vorbis, wav, wavpack}; we expose the formats
# that are well-supported in browsers and YouTube uploads.
DEFAULT_AUDIO_FORMATS: dict[str, list[str]] = {
    "Default (OGG/Vorbis)": [],
    "MP3": ["--audio-format=mp3"],
    "AAC": ["--audio-format=aac"],
    "FLAC (lossless)": ["--audio-format=flac"],
    "Opus": ["--audio-format=opus"],
}

QUEUE_POLL_MS = 100  # how often the UI drains log messages from the worker


# --- Credentials setup dialog --------------------------------------------------


class CredentialsDialog(tk.Toplevel):
    """Modal dialog that captures + writes Spotify Web API credentials."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("Spotify API Credentials")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.saved = False

        prelude = (
            "spotify-video-combiner needs free Spotify Web API credentials\n"
            "to read playlist metadata. Create an app at the link below,\n"
            "then paste the Client ID and Client Secret here."
        )
        ttk.Label(self, text=prelude, justify="left").pack(padx=20, pady=(20, 5))

        link = ttk.Label(
            self,
            text="Open developer.spotify.com/dashboard",
            foreground="blue",
            cursor="hand2",
        )
        link.pack(padx=20, pady=(0, 15))
        link.bind("<Button-1>", lambda _: webbrowser.open("https://developer.spotify.com/dashboard"))

        form = ttk.Frame(self)
        form.pack(padx=20, pady=5, fill="x")

        ttk.Label(form, text="Client ID:").grid(row=0, column=0, sticky="w", pady=4)
        self.client_id = ttk.Entry(form, width=44)
        self.client_id.grid(row=0, column=1, padx=(10, 0))

        ttk.Label(form, text="Client Secret:").grid(row=1, column=0, sticky="w", pady=4)
        self.client_secret = ttk.Entry(form, width=44, show="*")
        self.client_secret.grid(row=1, column=1, padx=(10, 0))

        buttons = ttk.Frame(self)
        buttons.pack(padx=20, pady=15, fill="x")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")

        self.client_id.focus_set()
        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self._cancel())

    def _save(self) -> None:
        cid = self.client_id.get().strip()
        secret = self.client_secret.get().strip()
        if not cid or not secret:
            messagebox.showerror("Missing values", "Both Client ID and Secret are required.", parent=self)
            return
        target = user_config_dir() / CREDENTIALS_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            CREDENTIALS_TEMPLATE.replace(
                "SPOTIPY_CLIENT_ID=", f"SPOTIPY_CLIENT_ID={cid}"
            ).replace(
                "SPOTIPY_CLIENT_SECRET=", f"SPOTIPY_CLIENT_SECRET={secret}"
            ),
            encoding="utf-8",
        )
        self.saved = True
        self.destroy()

    def _cancel(self) -> None:
        self.saved = False
        self.destroy()


def credentials_present() -> bool:
    creds = load_credentials_files()
    return bool(creds.get("SPOTIPY_CLIENT_ID")) and bool(creds.get("SPOTIPY_CLIENT_SECRET"))


# --- Main window ---------------------------------------------------------------


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{WINDOW_TITLE}  v{__version__}")
        self.geometry("780x620")
        self.minsize(680, 540)

        self._log_queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_result: tuple[bool, str] | None = None  # (success, message-or-output-path)

        self._build_layout()
        self.after(QUEUE_POLL_MS, self._drain_log_queue)

    # --- layout ----------------------------------------------------------

    def _build_layout(self) -> None:
        pad = {"padx": 12, "pady": 6}
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, **pad)

        ttk.Label(outer, text="Spotify Playlist URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(outer, textvariable=self.url_var)
        url_entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 10))

        ttk.Label(outer, text="Output folder").grid(row=2, column=0, sticky="w")
        self.workdir_var = tk.StringVar(value="")
        ttk.Entry(outer, textvariable=self.workdir_var).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(2, 10)
        )
        ttk.Button(outer, text="Browse...", command=self._pick_workdir).grid(
            row=3, column=2, sticky="ew", padx=(8, 0), pady=(2, 10)
        )
        ttk.Label(
            outer,
            text="(Leave empty to default to ./output/<playlist-name>/)",
            foreground="gray",
        ).grid(row=4, column=0, columnspan=3, sticky="w")

        opts = ttk.Frame(outer)
        opts.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 4))

        ttk.Label(opts, text="Resolution:").grid(row=0, column=0, sticky="w")
        self.resolution_var = tk.StringVar(value=next(iter(DEFAULT_RESOLUTIONS)))
        ttk.Combobox(
            opts,
            textvariable=self.resolution_var,
            values=list(DEFAULT_RESOLUTIONS),
            state="readonly",
            width=24,
        ).grid(row=0, column=1, sticky="w", padx=(8, 24))

        ttk.Label(opts, text="Audio format:").grid(row=0, column=2, sticky="w")
        self.audio_format_var = tk.StringVar(value=next(iter(DEFAULT_AUDIO_FORMATS)))
        ttk.Combobox(
            opts,
            textvariable=self.audio_format_var,
            values=list(DEFAULT_AUDIO_FORMATS),
            state="readonly",
            width=18,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))

        action = ttk.Frame(outer)
        action.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        self.combine_button = ttk.Button(action, text="Combine playlist into MP4", command=self._start_pipeline)
        self.combine_button.pack(side="left")
        self.creds_button = ttk.Button(action, text="Spotify credentials...", command=self._open_credentials_dialog)
        self.creds_button.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(outer, textvariable=self.status_var, foreground="gray").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        self.log_widget = scrolledtext.ScrolledText(
            outer,
            wrap="word",
            height=18,
            font=("Consolas", 9),
            background="#101218",
            foreground="#dcdcdc",
            insertbackground="#dcdcdc",
        )
        self.log_widget.grid(row=9, column=0, columnspan=3, sticky="nsew")
        self.log_widget.configure(state="disabled")

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(9, weight=1)

    # --- helpers ---------------------------------------------------------

    def _pick_workdir(self) -> None:
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.workdir_var.set(chosen)

    def _open_credentials_dialog(self) -> None:
        dialog = CredentialsDialog(self)
        self.wait_window(dialog)
        if dialog.saved:
            self._append_log("Credentials saved.\n")

    def _append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item is None:
                    self._on_pipeline_done()
                    continue
                self._append_log(item if item.endswith("\n") else item + "\n")
        except queue.Empty:
            pass
        self.after(QUEUE_POLL_MS, self._drain_log_queue)

    # --- pipeline driver -------------------------------------------------

    def _start_pipeline(self) -> None:
        if self._worker and self._worker.is_alive():
            return  # already running

        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Missing URL", "Paste a Spotify playlist URL first.", parent=self)
            return

        if not credentials_present():
            messagebox.showinfo(
                "Credentials needed",
                "Spotify Web API credentials are required to read playlist metadata. "
                "The next dialog will let you paste them in.",
                parent=self,
            )
            self._open_credentials_dialog()
            if not credentials_present():
                return

        workdir_text = self.workdir_var.get().strip()
        workdir = Path(workdir_text) if workdir_text else None

        width, height = DEFAULT_RESOLUTIONS[self.resolution_var.get()]
        zotify_extra = DEFAULT_AUDIO_FORMATS[self.audio_format_var.get()]

        self.combine_button.configure(state="disabled")
        self.status_var.set("Working...")
        self.progress.start(10)
        self._append_log("=" * 64 + "\n")

        def log(msg: str) -> None:
            self._log_queue.put(msg)

        def worker() -> None:
            try:
                _, resolved_workdir = download_playlist(
                    url,
                    workdir=workdir,
                    zotify_extra=zotify_extra,
                    log=log,
                )
                renderer = SlideRenderer()
                builder = FFmpegVideoBuilder(
                    settings=EncodeSettings(width=width, height=height)
                )
                output = build_video(
                    resolved_workdir,
                    renderer=renderer,
                    builder=builder,
                    log=log,
                )
                log(f"Done -> {output}")
                self._last_result = (True, str(output))
            except UserFacingError as exc:
                log(f"Error: {exc}")
                self._last_result = (False, str(exc))
            except Exception as exc:
                log(f"Unexpected error: {exc}")
                self._last_result = (False, str(exc))
            finally:
                self._log_queue.put(None)  # sentinel: pipeline is done

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_pipeline_done(self) -> None:
        self.progress.stop()
        self.combine_button.configure(state="normal")
        result = self._last_result
        self._last_result = None
        if result is None:
            self.status_var.set("Idle.")
            return
        success, payload = result
        if success:
            self.status_var.set(f"Done: {payload}")
            messagebox.showinfo("Combine complete", f"Wrote:\n{payload}", parent=self)
        else:
            self.status_var.set("Failed.")
            messagebox.showerror("Combine failed", payload, parent=self)


def main() -> None:
    """Console-script entry point for ``svc-gui``."""
    app = App()
    if not credentials_present():
        # First-run nudge, but don't block startup.
        app.after(200, lambda: messagebox.showinfo(
            "Welcome",
            "Welcome! On first run you'll need to paste your Spotify Web API\n"
            "credentials. Click 'Spotify credentials...' or just hit Combine\n"
            "and the dialog will appear.",
            parent=app,
        ))
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
