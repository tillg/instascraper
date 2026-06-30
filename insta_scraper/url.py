"""Parse Instagram post/reel/tv URLs into shortcodes."""

import re

_SHORTCODE_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/(?P<shortcode>[A-Za-z0-9_-]+)"
)


def parse_shortcode(url: str) -> str:
    """Extract the Instagram shortcode from a post/reel/tv URL.

    Handles ``/p/``, ``/reel/`` and ``/tv/`` paths, optional ``www.``,
    http or https, a missing trailing slash, and trailing query strings
    or fragments.

    Raises:
        ValueError: if the URL is not a recognized Instagram post/reel/tv URL.
    """
    match = _SHORTCODE_RE.match(url.strip())
    if not match:
        raise ValueError(f"Not a recognized Instagram post/reel/tv URL: {url!r}")
    return match.group("shortcode")
