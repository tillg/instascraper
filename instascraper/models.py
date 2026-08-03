"""Internal data carriers, decoupled from Instaloader and the network.

These dataclasses are the contract between `scraper.py` (produces them) and
`writer.py` (renders them). Media files are deliberately NOT held here:
`download_post()` writes them to disk and `writer.py` globs the directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Comment:
    """A single comment on a post."""

    username: str
    likes: int
    text: str
    created_at: datetime | None = None


@dataclass
class Provenance:
    """How and when an export was produced — the methods header."""

    fetched_at: str  # ISO-8601 UTC timestamp
    backend: str     # fetch backend + version, e.g. "instagrapi 2.16.26"
    account: str
    comment_sort: str          # "likes" | "instagram"
    comment_scan_limit: int    # 0 == scanned all
    tool: str = "instascraper"


@dataclass
class ScrapeResult:
    """Everything scraped for one post/reel, ready to render."""

    shortcode: str
    source_url: str
    owner: str
    typename: str
    taken_at: datetime | None
    likes: int
    is_video: bool
    caption: str
    comments: list[Comment] = field(default_factory=list)
    provenance: Provenance | None = None
