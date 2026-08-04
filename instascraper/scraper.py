"""Scraping logic for Instagram posts.

Holds the pure comment-selection function plus the instagrapi-backed fetch that
turns a shortcode into a `ScrapeResult`. (Instaloader's web-GraphQL fetch no
longer works against current Instagram, so the fetch backend is instagrapi's
private mobile API — see specs/changes/initial_scraper/architecture.md.)
Media is downloaded by `writer.write_result()`.
"""

from __future__ import annotations

import importlib.metadata as _md
from datetime import datetime, timezone

from instagrapi.extractors import extract_comment

from instascraper.models import Comment, Provenance, ScrapeResult


class NullProgress:
    """No-op progress sink (used when no UI is attached, e.g. in tests)."""

    def start(self, label: str) -> None: ...
    def ok(self, result: str = "done") -> None: ...
    def tick(self) -> None: ...
    def stage(self, msg: str) -> None: ...
    def done(self) -> None: ...


_NULL = NullProgress()


def _scan_comments(client, pk, amount: int, tick, humanizer=None) -> list:
    """Page through a post's comments one request at a time, calling `tick()`
    once per fetched page. Because instagrapi sleeps its `delay_range` before
    each request, dots appear at the real fetch cadence — a few seconds apart.
    `amount` 0 means scan all. Mirrors instagrapi's own paging.

    With a `humanizer`, each extra page costs a sampled think-time and may be
    the last: a person reads a screenful and moves on rather than paging to an
    exact count. Without one, paging is exhaustive exactly as before."""
    media_id = client.media_id(pk)
    comments: list = []
    params = None

    def collect(result) -> None:
        page = result.get("comments") or []
        comments.extend(extract_comment(c) for c in page)
        if page:
            tick()  # one dot per fetched page (≈ one paced request)

    result = client.private_request(f"media/{media_id}/comments/", params)
    if humanizer is not None:
        humanizer.record("request")
    collect(result)
    while (result.get("has_more_comments") and result.get("next_max_id")) or (
        result.get("has_more_headload_comments") and result.get("next_min_id")
    ):
        if humanizer is not None and humanizer.should_stop_early():
            break  # read enough — a human wouldn't page on
        if result.get("has_more_comments"):
            params = {"max_id": result["next_max_id"]}
        else:
            params = {"min_id": result["next_min_id"]}
        if not (result.get("next_max_id") or result.get("next_min_id") or result.get("comments")):
            break
        if humanizer is not None:
            humanizer.delay("page")
        result = client.private_request(f"media/{media_id}/comments/", params)
        if humanizer is not None:
            humanizer.record("request")
        collect(result)
        if amount and len(comments) >= amount:
            break
    return comments[:amount] if amount else comments


def select_top_comments(
    comments: list[Comment], n: int = 10, sort: str = "likes"
) -> list[Comment]:
    """Return up to `n` comments according to `sort`, without mutating input.

    - sort == "likes": rank by likes descending, tie-break by recency
      (newer created_at first; None treated as oldest).
    - sort == "instagram": preserve input order (latest-first as returned by
      Instagram).
    """
    if sort == "instagram":
        return list(comments[:n])

    _OLDEST = datetime.min

    def key(c: Comment) -> tuple[int, datetime]:
        return (c.likes, c.created_at if c.created_at is not None else _OLDEST)

    ordered = sorted(comments, key=key, reverse=True)
    return ordered[:n]


def _to_comment(raw) -> Comment:
    """Map an instagrapi Comment to our model."""
    username = getattr(getattr(raw, "user", None), "username", None) or "unknown"
    return Comment(
        username=username,
        likes=int(getattr(raw, "like_count", 0) or 0),
        text=getattr(raw, "text", "") or "",
        created_at=getattr(raw, "created_at_utc", None),
    )


def _typename(media) -> str:
    if media.product_type:
        return media.product_type  # e.g. "clips", "feed", "igtv"
    return {1: "image", 2: "video", 8: "album"}.get(media.media_type, "unknown")


def scrape(
    client,
    shortcode: str,
    source_url: str,
    account: str,
    sort: str = "likes",
    scan_limit: int = 200,
    progress=None,
    humanizer=None,
) -> tuple[object, ScrapeResult]:
    """Fetch a post/reel via instagrapi and build a `ScrapeResult`.

    Returns `(media, result)` where `media` is the instagrapi Media object
    (passed to `writer.write_result` for downloading). No media is downloaded
    here. instagrapi exceptions propagate to the caller for classification.
    `progress` is an optional sink with stage()/scan_start()/tick() methods.
    `humanizer` is an optional `behavior.Humanizer` that paces the comment
    paging; `None` (the library path) keeps today's exhaustive behavior.
    """
    progress = progress or _NULL

    progress.start("fetching metadata")
    pk = client.media_pk_from_url(source_url)  # local: decodes the shortcode
    # `media_info_v1`, not `media_info`: the latter falls back to web GraphQL,
    # which is dead against current Instagram (the same reason the backend is
    # instagrapi and not instaloader). That fallback answered 200 with a ~600KB
    # HTML login wall, so any private-API failure — including a dead session —
    # surfaced as an opaque ClientJSONDecodeError and got skipped as transient.
    # Going straight to the private API lets MediaNotFound / LoginRequired reach
    # `cli.main`, which already classifies them correctly.
    media = client.media_info_v1(pk)
    if humanizer is not None:
        humanizer.record("request")
    progress.ok(f"{_typename(media)} by @{getattr(media.user, 'username', '?')} · "
                f"{media.comment_count} comments · ❤️ {media.like_count}")

    amount = scan_limit if scan_limit and scan_limit > 0 else 0  # 0 == all
    if humanizer is not None:
        # Paging every comment of a post is one of the loudest bot signals
        # there is, so `0` (= all) becomes a human-scale depth under humanization.
        amount = humanizer.clamp_scan_limit(amount)
        if scan_limit <= 0 < amount:
            progress.stage(
                f"humanized: scanning ~{amount} comments, not all "
                "(pass --no-humanize to scan everything)"
            )
    progress.start(
        f"scanning {'all' if amount == 0 else f'up to {amount}'} comments (1 dot/page)"
    )
    raw_comments = _scan_comments(client, pk, amount, progress.tick, humanizer)
    progress.ok(f"{len(raw_comments)} comments → top 10 by {sort}")
    top = select_top_comments([_to_comment(c) for c in raw_comments], n=10, sort=sort)

    provenance = Provenance(
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        backend=f"instagrapi {_md.version('instagrapi')}",
        account=account,
        comment_sort=sort,
        comment_scan_limit=scan_limit,
        comments_scanned=len(raw_comments),
        humanization=humanizer.profile.summary() if humanizer is not None else "off",
    )

    result = ScrapeResult(
        shortcode=shortcode,
        source_url=source_url,
        owner=getattr(getattr(media, "user", None), "username", None) or "unknown",
        typename=_typename(media),
        taken_at=media.taken_at,
        likes=int(media.like_count or 0),
        is_video=(media.media_type == 2),
        caption=media.caption_text or "",
        comments=top,
        provenance=provenance,
    )
    return media, result
