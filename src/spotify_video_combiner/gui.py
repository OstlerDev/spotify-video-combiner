"""Tkinter GUI: paste a Spotify URL, click Combine, watch the MP4 build itself.

Architecture:
- Main thread owns Tk and never blocks; user input + UI updates happen here.
- A worker thread runs the pipeline and pipes log lines into a thread-safe
  ``queue.Queue``.
- A periodic ``after()`` callback drains the queue into the log widget so
  output appears live without any cross-thread Tk calls.

Sign-in is a one-button flow (Sign In / Sign Out) that drives zotify's OAuth
in-process: the browser handles the actual Spotify login, our localhost
callback captures the token, librespot writes ``credentials.json``, and the
same session is then reused for both metadata reads *and* audio download --
no developer credentials, no console pop-up, no second auth step.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .auth import current_username, is_signed_in, sign_in, sign_out
from .errors import UserFacingError
from .pipeline import build_video, download_playlist
from .processes import LogChannels
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
SUBPROCESS_LOG_MAX_LINES = 500  # ring-buffer cap for the verbose subprocess pane

# Tag for log queue items. ``"P"`` => pipeline (top pane), ``"S"`` => subprocess
# (bottom pane), ``None`` => sentinel meaning "pipeline finished".
_LogItem = tuple[str, str] | None


# --- Sign-in dialog ----------------------------------------------------------


class SignInDialog(tk.Toplevel):
    """Modal that drives zotify's OAuth flow.

    The flow mirrors zotify's own ``input("Username: ") -> auth_interactive
    -> from_oauth`` exactly; we just present a Tk text field and a
    "Sign In" button instead of a console prompt. The blocking part
    (waiting on librespot's localhost callback at port 4381) runs on a
    worker thread and reports back through a queue so Tk stays responsive.
    """

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("Sign in to Spotify")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.username: str | None = None
        self._result_queue: queue.Queue[tuple[bool, str]] = queue.Queue()
        self._signed_in_as: str | None = None

        ttk.Label(
            self,
            text=(
                "Sign in with your Spotify Premium account. Enter your username\n"
                "(usually your email), then click Sign In to open Spotify in\n"
                "your browser. Once you approve, you'll be brought back here."
            ),
            justify="left",
        ).pack(padx=24, pady=(20, 12))

        form = ttk.Frame(self)
        form.pack(padx=24, fill="x")
        ttk.Label(form, text="Username:").grid(row=0, column=0, sticky="w", pady=4)
        self._username_var = tk.StringVar()
        self._username_entry = ttk.Entry(form, textvariable=self._username_var, width=36)
        self._username_entry.grid(row=0, column=1, padx=(8, 0), pady=4)

        self._status = ttk.Label(self, text="", foreground="gray")
        self._status.pack(padx=24, pady=(8, 12))

        buttons = ttk.Frame(self)
        buttons.pack(padx=24, pady=(0, 20), fill="x")
        self._cancel_btn = ttk.Button(buttons, text="Cancel", command=self._cancel)
        self._cancel_btn.pack(side="right", padx=(8, 0))
        self._submit_btn = ttk.Button(buttons, text="Sign In", command=self._launch)
        self._submit_btn.pack(side="right")

        self._username_entry.focus_set()
        self.bind("<Return>", lambda _: self._launch())
        self.bind("<Escape>", lambda _: self._cancel())
        self.after(QUEUE_POLL_MS, self._drain_result)

    def _launch(self) -> None:
        username = self._username_var.get().strip()
        if not username:
            messagebox.showerror("Missing username", "Enter your Spotify username first.", parent=self)
            return
        self._submit_btn.configure(state="disabled")
        self._username_entry.configure(state="disabled")
        self._signed_in_as = username
        self._status.configure(text="Waiting for Spotify... finish the login in your browser.")
        threading.Thread(target=self._run_oauth, args=(username,), daemon=True).start()

    def _run_oauth(self, username: str) -> None:
        try:
            sign_in(username, webbrowser.open)
            self._result_queue.put((True, username))
        except Exception as exc:
            self._result_queue.put((False, str(exc)))

    def _drain_result(self) -> None:
        try:
            ok, payload = self._result_queue.get_nowait()
        except queue.Empty:
            self.after(QUEUE_POLL_MS, self._drain_result)
            return
        if ok:
            self.username = payload
            self.destroy()
        else:
            messagebox.showerror("Sign-in failed", payload, parent=self)
            self._cancel()

    def _cancel(self) -> None:
        # zotify's OAuth callback server is on a daemon thread; we just
        # stop caring about it. (No clean cancel API in zotify upstream.)
        self.username = None
        self.destroy()


# --- Main window ---------------------------------------------------------------


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{WINDOW_TITLE}  v{__version__}")
        self.geometry("780x620")
        self.minsize(680, 540)

        self._log_queue: queue.Queue[_LogItem] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_result: tuple[bool, str] | None = None  # (success, message-or-output-path)

        self._build_layout()
        self._refresh_auth_button()
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
        self.auth_button = ttk.Button(action, text="Sign In", command=self._toggle_auth)
        self.auth_button.pack(side="right")
        self.auth_status = ttk.Label(action, text="", foreground="gray")
        self.auth_status.pack(side="right", padx=(0, 8))

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(outer, textvariable=self.status_var, foreground="gray").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )
        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        # Two log panes split by a draggable divider:
        # - top: high-level pipeline progress (sparse, important).
        # - bottom: live tail of zotify/ffmpeg subprocess output (chatty,
        #   ring-buffered so it never grows without bound).
        log_pane = ttk.PanedWindow(outer, orient="vertical")
        log_pane.grid(row=9, column=0, columnspan=3, sticky="nsew")

        self.pipeline_log = self._make_log_widget(log_pane, height=12)
        log_pane.add(self.pipeline_log, weight=3)
        self.subprocess_log = self._make_log_widget(
            log_pane, height=6, foreground="#9aa0a6"
        )
        log_pane.add(self.subprocess_log, weight=2)

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(9, weight=1)

    @staticmethod
    def _make_log_widget(parent: tk.Misc, *, height: int, foreground: str = "#dcdcdc") -> scrolledtext.ScrolledText:
        widget = scrolledtext.ScrolledText(
            parent,
            wrap="word",
            height=height,
            font=("Consolas", 9),
            background="#101218",
            foreground=foreground,
            insertbackground=foreground,
        )
        widget.configure(state="disabled")
        return widget

    # --- helpers ---------------------------------------------------------

    def _pick_workdir(self) -> None:
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.workdir_var.set(chosen)

    def _refresh_auth_button(self) -> None:
        if is_signed_in():
            user = current_username() or "Spotify"
            self.auth_button.configure(text="Sign Out")
            self.auth_status.configure(text=f"Signed in as {user}")
        else:
            self.auth_button.configure(text="Sign In")
            self.auth_status.configure(text="Not signed in")

    def _toggle_auth(self) -> None:
        if is_signed_in():
            if not messagebox.askyesno(
                "Sign out?",
                "Sign out of Spotify? You'll need to sign in again before downloading.",
                parent=self,
            ):
                return
            sign_out()
            self._append_pipeline("Signed out.\n")
        else:
            self._open_sign_in()
        self._refresh_auth_button()

    def _open_sign_in(self) -> bool:
        """Run the sign-in dialog. Returns True if the user is now signed in."""
        dialog = SignInDialog(self)
        self.wait_window(dialog)
        if dialog.username:
            self._append_pipeline(f"Signed in as {dialog.username}.\n")
            return True
        return False

    def _append_pipeline(self, message: str) -> None:
        self._append(self.pipeline_log, message)

    def _append_subprocess(self, message: str) -> None:
        self._append(self.subprocess_log, message, max_lines=SUBPROCESS_LOG_MAX_LINES)

    @staticmethod
    def _append(widget: scrolledtext.ScrolledText, message: str, *, max_lines: int | None = None) -> None:
        widget.configure(state="normal")
        widget.insert("end", message if message.endswith("\n") else message + "\n")
        if max_lines is not None:
            # ``index('end-1c')`` is the last visible character; its line number
            # tells us total lines. Trim from the top so the view stays tail-pinned.
            line_count = int(widget.index("end-1c").split(".", 1)[0])
            if line_count > max_lines:
                widget.delete("1.0", f"{line_count - max_lines + 1}.0")
        widget.see("end")
        widget.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item is None:
                    self._on_pipeline_done()
                    continue
                channel, message = item
                if channel == "P":
                    self._append_pipeline(message)
                else:
                    self._append_subprocess(message)
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

        if not is_signed_in() and not self._open_sign_in():
            return
        self._refresh_auth_button()

        workdir_text = self.workdir_var.get().strip()
        workdir = Path(workdir_text) if workdir_text else None

        width, height = DEFAULT_RESOLUTIONS[self.resolution_var.get()]
        zotify_extra = DEFAULT_AUDIO_FORMATS[self.audio_format_var.get()]

        self.combine_button.configure(state="disabled")
        self.status_var.set("Working...")
        self.progress.start(10)
        self._append_pipeline("Starting up app...\n")
        self._append_pipeline("=" * 64 + "\n")

        def push_pipeline(msg: str) -> None:
            self._log_queue.put(("P", msg))

        def push_subprocess(msg: str) -> None:
            self._log_queue.put(("S", msg))

        channels = LogChannels(pipeline=push_pipeline, subprocess=push_subprocess)

        def worker() -> None:
            try:
                push_pipeline("Downloading playlist... (this may take a while)\n")
                resolved_workdir = download_playlist(
                    url,
                    workdir=workdir,
                    zotify_extra=zotify_extra,
                    channels=channels,
                )
                push_pipeline("Done downloading playlist.\n")

                # set the progress to 50%
                self.progress.configure(value=50)
                push_pipeline("Rendering cover art slides...\n")
                renderer = SlideRenderer()
                # set the progress to 60%
                self.progress.configure(value=60)
                push_pipeline("Encoding video segments...\n")
                builder = FFmpegVideoBuilder(
                    settings=EncodeSettings(width=width, height=height),
                    log=channels.subprocess,
                )
                # set the progress to 60%
                self.progress.configure(value=90)
                output = build_video(
                    resolved_workdir,
                    renderer=renderer,
                    builder=builder,
                    channels=channels,
                )
                # set the progress to 100%
                self.progress.configure(value=100)
                push_pipeline(f"Done -> {output}")
                self._last_result = (True, str(output))
            except UserFacingError as exc:
                push_pipeline(f"Error: {exc}")
                self._last_result = (False, str(exc))
            except Exception as exc:
                push_pipeline(f"Unexpected error: {exc}")
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
            # open the folder containing the output file
            os.startfile(os.path.dirname(payload))
        else:
            self.status_var.set("Failed.")
            messagebox.showerror("Combine failed", payload, parent=self)


def main() -> None:
    """Console-script entry point for ``svc-gui``."""
    app = App()
    if not is_signed_in():
        # First-run nudge, but don't block startup.
        app.after(200, lambda: messagebox.showinfo(
            "Welcome",
            "Welcome! Click 'Sign In' to authorise this app with your Spotify\n"
            "account. You'll only need to do this once.",
            parent=app,
        ))
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
