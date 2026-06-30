"""Network-free test of scrape() mapping, using a fake instagrapi client."""

from datetime import datetime, timezone

import pytest

import insta_scraper.scraper as scraper
from insta_scraper.scraper import _scan_comments, scrape


class _User:
    def __init__(self, username):
        self.username = username


class _Media:
    pk = "3877045179849473826"
    user = _User("clerkofcinema_")
    product_type = "clips"
    media_type = 2
    taken_at = datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc)
    like_count = 3
    comment_count = 139
    caption_text = "A reel caption"
    thumbnail_url = "https://example.com/cover.jpg"
    video_url = "https://example.com/v.mp4"


class _Comment:
    def __init__(self, username, like_count, text, created=None):
        self.user = _User(username)
        self.like_count = like_count
        self.text = text
        self.created_at_utc = created


@pytest.fixture(autouse=True)
def _patch_extract(monkeypatch):
    # Convert our tiny raw-comment dicts into _Comment objects.
    monkeypatch.setattr(
        scraper, "extract_comment", lambda d: _Comment(d["u"], d["l"], d["t"])
    )


class _FakeClient:
    def media_pk_from_url(self, url):
        return _Media.pk

    def media_info(self, pk):
        return _Media()

    def media_id(self, pk):
        return pk

    def private_request(self, path, params=None):
        # one page, latest-first (lower-liked first) so ranking must reorder
        return {"comments": [
            {"u": "bob", "l": 5, "t": "second"},
            {"u": "alice", "l": 320, "t": "top!"},
        ]}


def test_scrape_maps_media_and_ranks_comments():
    media, result = scrape(
        _FakeClient(), "DXOCAyzEX8i",
        "https://www.instagram.com/reel/DXOCAyzEX8i/", account="tillg",
    )
    assert result.owner == "clerkofcinema_"
    assert result.is_video is True
    assert result.typename == "clips"
    assert result.likes == 3
    assert result.caption == "A reel caption"
    assert [c.username for c in result.comments] == ["alice", "bob"]  # ranked by likes
    assert result.provenance.backend.startswith("instagrapi ")
    assert media is not None


def test_scrape_instagram_sort_preserves_order():
    _, result = scrape(
        _FakeClient(), "DXOCAyzEX8i",
        "https://www.instagram.com/reel/DXOCAyzEX8i/", account="tillg",
        sort="instagram",
    )
    assert [c.username for c in result.comments] == ["bob", "alice"]


def test_scan_comments_ticks_once_per_page():
    pages = iter([
        {"comments": [{"u": "a", "l": 1, "t": "x"}, {"u": "b", "l": 2, "t": "y"}],
         "has_more_headload_comments": True, "next_min_id": "c1"},
        {"comments": [{"u": "c", "l": 3, "t": "z"}]},  # no more
    ])

    class C:
        def media_id(self, pk):
            return pk

        def private_request(self, path, params=None):
            return next(pages)

    ticks = []
    out = _scan_comments(C(), "pk", amount=0, tick=lambda: ticks.append(1))
    assert len(out) == 3
    assert len(ticks) == 2  # one dot per page, not per comment


def test_scan_comments_respects_amount_cap():
    class C:
        def media_id(self, pk):
            return pk

        def private_request(self, path, params=None):
            return {"comments": [{"u": str(i), "l": i, "t": "t"} for i in range(5)]}

    out = _scan_comments(C(), "pk", amount=3, tick=lambda: None)
    assert len(out) == 3
