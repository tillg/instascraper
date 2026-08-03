from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from instascraper.models import Comment, Provenance, ScrapeResult
from instascraper.writer import render_markdown, render_metadata


@pytest.fixture
def result() -> ScrapeResult:
    return ScrapeResult(
        shortcode="DXOCAyzEX8i",
        source_url="https://www.instagram.com/reel/DXOCAyzEX8i/",
        owner="owner_username",
        typename="GraphVideo",
        taken_at=datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc),
        likes=12345,
        is_video=True,
        caption="A great reel caption.",
        comments=[
            Comment(
                username="alice",
                likes=320,
                text="Great edit!",
                created_at=datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc),
            ),
            Comment(username="bob", likes=198, text="Where is this?"),
        ],
        provenance=Provenance(
            fetched_at="2026-06-26T14:05Z",
            backend="instagrapi 2.16.26",
            account="your_account",
            comment_sort="likes",
            comment_scan_limit=200,
        ),
    )


MEDIA = ["DXOCAyzEX8i.jpg", "DXOCAyzEX8i.mp4"]


def test_provenance_fetched_header_present(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "> Fetched 2026-06-26T14:05Z" in md
    assert "instagrapi 2.16.26" in md
    assert "as @your_account" in md


def test_ranking_caveat_present(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "Comment ranking:" in md
    assert 'not Instagram\'s in-app "top comments"' in md


def test_image_embed_present(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "![DXOCAyzEX8i.jpg](DXOCAyzEX8i.jpg)" in md


def test_video_link_present(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "[▶ Play video — DXOCAyzEX8i.mp4](DXOCAyzEX8i.mp4)" in md


def test_comments_numbered_with_likes(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "## Top 2 comments" in md
    assert "1. **@alice** (❤️ 320) — Great edit!" in md
    assert "2. **@bob** (❤️ 198) — Where is this?" in md


def test_title_reel_for_video(result: ScrapeResult) -> None:
    assert render_markdown(result, MEDIA).startswith("# @owner_username — Reel")


def test_instagram_sort_caveat(result: ScrapeResult) -> None:
    assert result.provenance is not None
    result.provenance.comment_sort = "instagram"
    md = render_markdown(result, MEDIA)
    assert "first 2 returned by Instagram" in md
    assert 'not the app\'s "top comments"' in md


def test_no_media_and_no_comments() -> None:
    r = ScrapeResult(
        shortcode="ABC",
        source_url="https://example.com/p/ABC/",
        owner="x",
        typename="GraphImage",
        taken_at=None,
        likes=0,
        is_video=False,
        caption="",
        comments=[],
        provenance=Provenance(
            fetched_at="2026-06-26T14:05Z",
            backend="instagrapi 2.16.26",
            account="acct",
            comment_sort="likes",
            comment_scan_limit=0,
        ),
    )
    md = render_markdown(r, [])
    assert md.startswith("# @x — Post")
    assert "_No caption._" in md
    assert "_No media files._" in md
    assert "_No comments returned._" in md
    assert "all scanned" in md


def test_metadata_roundtrips_through_json(result: ScrapeResult) -> None:
    meta = render_metadata(result, MEDIA)
    dumped = json.dumps(meta, ensure_ascii=False)
    reloaded = json.loads(dumped)
    assert reloaded["shortcode"] == "DXOCAyzEX8i"
    assert reloaded["media_files"] == MEDIA
    assert reloaded["taken_at"] == "2026-06-20T09:30:00+00:00"
    assert reloaded["comments"][0] == {
        "username": "alice",
        "likes": 320,
        "created_at": "2026-06-20T10:00:00+00:00",
        "text": "Great edit!",
    }
    assert reloaded["comments"][1]["created_at"] is None
    assert reloaded["provenance"]["comment_sort"] == "likes"
    assert reloaded["provenance"]["tool"] == "instascraper"
