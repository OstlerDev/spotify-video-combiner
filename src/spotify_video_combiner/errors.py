"""Exception types meant for end-user display (no traceback needed).

Anything that subclasses :class:`UserFacingError` is caught at the CLI boundary
and rendered as a clean error message via Click rather than dumping a Python
stack trace. Subclasses also inherit from :class:`RuntimeError` so existing
``except RuntimeError`` callers keep working.
"""

from __future__ import annotations


class UserFacingError(RuntimeError):
    """Base for expected, actionable errors (missing tools, bad config, etc.)."""


class CredentialsError(UserFacingError):
    """Raised when Spotify Web API credentials cannot be resolved."""


class SpotifyApiError(UserFacingError):
    """Raised when the Spotify Web API rejects a request (bad creds, missing playlist, etc.)."""
