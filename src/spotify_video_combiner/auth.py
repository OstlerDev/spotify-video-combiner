"""Sign-in / sign-out around zotify's own OAuth + librespot session.

This module is deliberately a thin wrapper. The actual OAuth flow, token
exchange, credential file format, and session construction all live in
zotify (which itself wraps librespot); we call straight into those classes
rather than reimplementing any HTTP, headers, or token logic. The only
things we add on top are:

- A path helper that mirrors zotify's ``%APPDATA%/Zotify/credentials.json``
  default, so signing in here is bit-compatible with running ``zotify``
  directly.
- A small wrapper that drives :meth:`zotify.OAuth.auth_interactive` and
  :meth:`zotify.Session.from_oauth` so the GUI/CLI can show their own
  prompts instead of zotify's ``input("Username: ")`` console flow.
- A process-wide cached :class:`zotify.Session` so repeated metadata calls
  don't re-auth librespot from disk every time.

zotify imports librespot, which is slow; we defer those imports into
function bodies so simply ``import``-ing this module stays cheap.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import AuthError


def credentials_path() -> Path:
    """Path zotify caches its librespot credentials at after sign-in.

    Mirrors ``zotify.config.SYSTEM_PATHS`` so signing in here is
    interchangeable with a direct ``zotify`` invocation.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Zotify" / "credentials.json"


def is_signed_in() -> bool:
    """True once zotify has written its credentials file."""
    return credentials_path().is_file()


def current_username() -> str | None:
    """Read the cached username librespot wrote, for display purposes."""
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("username") or None
    except (OSError, ValueError):
        return None


def sign_out() -> bool:
    """Delete the cached credentials. Returns True if anything was removed."""
    path = credentials_path()
    if not path.is_file():
        return False
    path.unlink()
    reset_cached_session()
    return True


def sign_in(username: str, on_url: Callable[[str], None]) -> None:
    """Run zotify's own OAuth flow, blocking until the user approves in their browser.

    This is the same three-line dance ``zotify.app.App.__init__`` performs:
    construct an :class:`OAuth` for the username, start its local callback
    server (``auth_interactive`` returns the URL), then hand the OAuth
    object to :meth:`Session.from_oauth` which blocks on the callback,
    builds a librespot session, and writes ``credentials.json``.

    The caller drives presentation via ``on_url`` (e.g. open the URL in a
    browser, or print it to the console). All HTTP, headers, token
    exchange, and credential persistence are zotify's responsibility. The
    resulting :class:`Session` is stashed for reuse so the next caller
    of :func:`get_session` doesn't have to re-load librespot from disk.
    """
    from zotify import OAuth, Session

    if not username:
        raise AuthError("A Spotify username is required to sign in.")

    oauth = OAuth(username)
    on_url(oauth.auth_interactive())
    creds_path = credentials_path()
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    session = Session.from_oauth(oauth, creds_path)
    _set_cached_session(session)


# --- session reuse ----------------------------------------------------------


_SESSION_LOCK = threading.Lock()
_CACHED_SESSION: Any = None  # zotify.Session, but typed loosely to avoid the import


def get_session() -> Any:
    """Return a process-wide cached zotify ``Session``.

    Loading librespot is slow (several hundred ms) and not free against
    Spotify's anti-abuse counters, so we keep exactly one session per
    process. The session created at sign-in is reused here directly; only
    on a cold start (e.g. CLI ``svc all``) do we re-load it from
    ``credentials.json``.
    """
    global _CACHED_SESSION
    if not is_signed_in():
        raise AuthError(
            "Not signed in to Spotify. Click 'Sign In' in the GUI, "
            "or run `svc signin` from the CLI."
        )
    with _SESSION_LOCK:
        if _CACHED_SESSION is None:
            from zotify import Session

            _CACHED_SESSION = Session.from_file(credentials_path())
        return _CACHED_SESSION


def _set_cached_session(session: Any) -> None:
    global _CACHED_SESSION
    with _SESSION_LOCK:
        _CACHED_SESSION = session


def reset_cached_session() -> None:
    """Drop the cached session (call after sign-out so a fresh sign-in re-loads)."""
    _set_cached_session(None)


def lookup_playlist_name(playlist_id: str) -> str | None:
    """Best-effort fetch of a playlist's display name via librespot mercury.

    Uses ``api.get_playlist(PlaylistId(...))`` -- librespot's protobuf
    protocol over its persistent TCP connection -- *not* the Spotify Web
    API. Mercury sits on a different rate-limit bucket than the Web API
    and is what ``zotify`` itself uses for playlist scanning, so this is
    cheap and unaffected by the Web API anti-abuse layer that throttles
    librespot-OAuth tokens against ``/v1/playlists/{id}``.

    Returns ``None`` on any error rather than raising, because we only
    use the result to pick a default workdir name.
    """
    try:
        from librespot.metadata import PlaylistId

        api = get_session().api()
        playlist = api.get_playlist(PlaylistId(playlist_id))
        return playlist.attributes.name or None
    except Exception:
        return None
