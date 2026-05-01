"""Read playlist metadata + cover art URLs from the Spotify Web API.

Audio download is delegated to ``zotify`` (which uses Premium credentials), but
the Web API gives us cleaner control over playlist ordering, names, and
high-resolution cover art. The Web API only requires a free developer app
(client credentials flow); no user login is needed for public playlists.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass

import requests
import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOauthError

from .config import ensure_template_exists, load_credentials_files
from .errors import CredentialsError, SpotifyApiError
from .manifest import Playlist, Track

_PLAYLIST_ID_RE = re.compile(
    r"(?:spotify:playlist:|open\.spotify\.com/(?:intl-[a-z]+/)?playlist/)([A-Za-z0-9]+)"
)


@dataclass
class SpotifyCredentials:
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> SpotifyCredentials:
        """Resolve credentials from env vars, then from ``credentials.env`` files.

        Priority (highest first): real environment variables, then a project-local
        ``./credentials.env``, then ``<user-config>/spotify-video-combiner/credentials.env``.
        If nothing is found, a blank template is auto-written to the user-config
        location and a helpful error pointing the user there is raised.
        """
        file_values = load_credentials_files()

        def _pick(*keys: str) -> str | None:
            for key in keys:
                if value := os.environ.get(key):
                    return value
            for key in keys:
                if value := file_values.get(key):
                    return value
            return None

        cid = _pick("SPOTIPY_CLIENT_ID", "SPOTIFY_CLIENT_ID")
        secret = _pick("SPOTIPY_CLIENT_SECRET", "SPOTIFY_CLIENT_SECRET")

        if not cid or not secret:
            template_path = ensure_template_exists()
            raise CredentialsError(
                "Spotify Web API credentials not found.\n\n"
                f"  -> A blank template has been created at:\n       {template_path}\n\n"
                "  -> Edit that file to add your client ID and secret, then re-run.\n"
                "     (Or place a `credentials.env` next to where you run `svc`,\n"
                "     or export SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET.)\n\n"
                "  -> Get free credentials at https://developer.spotify.com/dashboard."
            )
        return cls(client_id=cid, client_secret=secret)


def parse_playlist_id(value: str) -> str:
    """Accept a raw playlist ID, ``spotify:playlist:...`` URI, or open.spotify URL."""
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9]{22}", value):
        return value
    match = _PLAYLIST_ID_RE.search(value)
    if not match:
        raise SpotifyApiError(
            f"Could not extract a Spotify playlist ID from {value!r}.\n"
            "Expected one of:\n"
            "  - https://open.spotify.com/playlist/<22-char-id>\n"
            "  - spotify:playlist:<22-char-id>\n"
            "  - <22-char-id>"
        )
    return match.group(1)


def _best_cover_url(images: list[dict]) -> str | None:
    """Pick the highest-resolution image. Spotify usually returns largest first."""
    if not images:
        return None
    sized = [img for img in images if img.get("width") and img.get("height")]
    if sized:
        return max(sized, key=lambda i: i["width"] * i["height"])["url"]
    return images[0].get("url")


class SpotifyMetadata:
    """Thin spotipy wrapper that returns our domain ``Playlist`` object."""

    def __init__(self, credentials: SpotifyCredentials | None = None) -> None:
        creds = credentials or SpotifyCredentials.from_env()
        auth = SpotifyClientCredentials(
            client_id=creds.client_id, client_secret=creds.client_secret
        )
        self._sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=3)

    def fetch_playlist(self, url_or_id: str) -> Playlist:
        playlist_id = parse_playlist_id(url_or_id)
        with _api_errors_as_user_facing(playlist_id):
            meta = self._sp.playlist(
                playlist_id, fields="id,name,owner.display_name,description,external_urls.spotify"
            )

            tracks: list[Track] = []
            index = 1
            page = self._sp.playlist_items(
                playlist_id,
                additional_types=("track",),
                fields=(
                    "items(track(id,name,duration_ms,external_urls.spotify,"
                    "artists(name),album(name,images))),next"
                ),
                limit=100,
            )
            while page is not None:
                for item in page["items"]:
                    track = (item or {}).get("track")
                    if not track or not track.get("id"):
                        continue  # local files, removed tracks, podcasts
                    album = track.get("album") or {}
                    tracks.append(
                        Track(
                            index=index,
                            spotify_id=track["id"],
                            spotify_url=track["external_urls"]["spotify"],
                            name=track["name"],
                            artists=[a["name"] for a in track["artists"]],
                            album=album.get("name", ""),
                            duration_ms=track["duration_ms"],
                            cover_url=_best_cover_url(album.get("images", [])),
                        )
                    )
                    index += 1
                page = self._sp.next(page) if page.get("next") else None

        return Playlist(
            spotify_id=meta["id"],
            spotify_url=meta["external_urls"]["spotify"],
            name=meta["name"],
            owner=(meta.get("owner") or {}).get("display_name", "unknown"),
            description=meta.get("description") or "",
            tracks=tracks,
        )


@contextmanager
def _api_errors_as_user_facing(playlist_id: str):
    """Translate spotipy/requests errors into :class:`SpotifyApiError` for clean CLI output."""
    try:
        yield
    except SpotifyOauthError as exc:
        raise SpotifyApiError(
            "Spotify rejected your Web API credentials. Double-check SPOTIPY_CLIENT_ID and "
            "SPOTIPY_CLIENT_SECRET in your credentials.env, then re-run.\n"
            f"(Spotify said: {exc})"
        ) from exc
    except SpotifyException as exc:
        if exc.http_status == 404:
            raise SpotifyApiError(
                f"Spotify could not find playlist `{playlist_id}`. "
                "Make sure the URL is correct and the playlist is public."
            ) from exc
        if exc.http_status == 429:
            raise SpotifyApiError(
                "Spotify rate limit hit. Wait a minute and try again."
            ) from exc
        raise SpotifyApiError(
            f"Spotify Web API error (HTTP {exc.http_status}): {exc.msg or exc}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise SpotifyApiError(
            f"Network error talking to Spotify: {exc}"
        ) from exc
