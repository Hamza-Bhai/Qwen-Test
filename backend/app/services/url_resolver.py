"""YouTube channel URL parsing.

Supported URL formats (Phase 1 requirement):
* https://www.youtube.com/@handle            -> handle  ("@handle")
* https://www.youtube.com/channel/UCxxxx...  -> channel id
* https://www.youtube.com/c/customname       -> custom URL name (legacy)
* https://www.youtube.com/user/username      -> legacy username

Rules:
* We never guess. Legacy ``/c/`` and ``/user/`` URLs are *detected* and
  reported as unsupported identifiers for the current backend flow; the API
  layer returns a clear, actionable error message instead of silently trying
  heuristics that could resolve to the wrong channel.
* Bare identifiers are accepted: "UC..." (channel id) or "@handle"/"handle".

Returns a ``ResolvedTarget`` describing what was found, or raises
``UnsupportedURLError`` / ``InvalidURLError`` with human-readable messages.
"""

import re
from dataclasses import dataclass

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")

_VALID_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtube.googleapis.com",
}


class ChannelURLValidationError(ValueError):
    """Base class for user-facing URL validation errors."""


class InvalidURLError(ChannelURLValidationError):
    """The input is not a recognisable YouTube channel reference."""


class UnsupportedURLError(ChannelURLValidationError):
    """A real YouTube URL format we deliberately refuse to guess about."""

    def __init__(self, url: str, kind: str, identifier: str) -> None:
        super().__init__(
            f"Unsupported legacy YouTube URL format ({kind}): {url!r}. "
            f"The backend resolves channels by handle (@name) or channel ID (UC...) only. "
            f"Open this channel in a browser and use its @handle or channel ID "
            f"(found in the channel 'About' page / /channel/UC... URL). "
            f"Detected legacy identifier: {identifier!r}."
        )
        self.url = url
        self.kind = kind
        self.identifier = identifier


@dataclass
class ResolvedTarget:
    kind: str          # "handle" | "channel_id"
    identifier: str    # "@mkbhd" or "UC..."
    source_url: str | None = None


def _strip_scheme(raw: str) -> str:
    s = raw.strip()
    for prefix in ("https://", "http://"):
        if s.lower().startswith(prefix):
            return s[len(prefix):]
    return s


def parse_channel_reference(raw: str) -> ResolvedTarget:
    """Parse a channel URL or bare identifier into a ResolvedTarget.

    Raises ChannelURLValidationError subclasses on bad input.
    """
    if not raw or not raw.strip():
        raise InvalidURLError("Channel URL or identifier is empty.")
    text = raw.strip()

    # --- Bare identifiers ---------------------------------------------------
    if _CHANNEL_ID_RE.match(text):
        return ResolvedTarget(kind="channel_id", identifier=text)
    if text.startswith("@") and len(text) > 1 and _HANDLE_RE.match(text):
        return ResolvedTarget(kind="handle", identifier=text)

    # Not a URL and not a bare handle/id? Could be a plain handle without '@'.
    rest = _strip_scheme(text)
    lowered = rest.lower()
    host_head = lowered.split("/", 1)[0]
    looks_like_url = (
        "/" in rest
        or any(host_head.endswith(d) for d in ("youtube.com", "youtu.be"))
        or "." in rest.replace("youtube.com", "").replace("youtu.be", "")
    )

    if not looks_like_url:
        if _HANDLE_RE.match(text):
            # e.g. "mkbhd" typed directly — treat as handle, normalise with '@'.
            return ResolvedTarget(kind="handle", identifier=f"@{text}")
        raise InvalidURLError(
            f"'{text}' is not a YouTube channel URL, @handle, or channel ID (UC...)."
        )

    path_part = rest.split("?", 1)[0].split("#", 1)[0]
    segments = [seg for seg in path_part.split("/") if seg]

    if not segments:
        raise InvalidURLError(f"'{raw}' does not point to a channel.")

    host = segments[0].lower()
    if host not in _VALID_HOSTS:
        raise InvalidURLError(
            f"Host '{host}' is not a YouTube domain. Paste a youtube.com channel URL."
        )

    route = segments[1:]
    if not route:
        raise InvalidURLError(f"'{raw}' points to the YouTube homepage, not a channel.")

    first, value = route[0], route[1] if len(route) > 1 else ""

    # youtube.com/@handle  (optionally /featured, /videos, /streams ...)
    if first.startswith("@"):
        handle = first
        if not _HANDLE_RE.match(handle):
            raise InvalidURLError(f"Handle '{handle}' in URL is malformed.")
        return ResolvedTarget(kind="handle", identifier=handle, source_url=raw)

    if first == "channel":
        if not value:
            raise InvalidURLError("URL contains /channel/ but no channel ID.")
        if not _CHANNEL_ID_RE.match(value):
            raise InvalidURLError(
                f"'{value}' is not a valid channel ID (expected UC followed by 22 characters)."
            )
        return ResolvedTarget(kind="channel_id", identifier=value, source_url=raw)

    if first == "c":
        if not value:
            raise InvalidURLError("URL contains /c/ but no custom name.")
        raise UnsupportedURLError(raw, "custom URL (/c/)", value)

    if first == "user":
        if not value:
            raise InvalidURLError("URL contains /user/ but no username.")
        raise UnsupportedURLError(raw, "legacy username (/user/)", value)

    raise InvalidURLError(
        f"Unrecognised YouTube URL path '/{'/'.join(route)}'. Supported formats: "
        "youtube.com/@handle, youtube.com/channel/UC..., "
        "(legacy /c/ and /user/ URLs are reported, not guessed)."
    )


__all__ = [
    "parse_channel_reference",
    "ResolvedTarget",
    "ChannelURLValidationError",
    "InvalidURLError",
    "UnsupportedURLError",
]
