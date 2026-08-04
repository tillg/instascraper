"""Network-free test of scrape() mapping, using a fake instagrapi client."""

import random
from datetime import datetime, timezone

import pytest

import instascraper.scraper as scraper
from instascraper.behavior import BehaviorProfile, Humanizer, Range
from instascraper.scraper import _scan_comments, scrape


def humanizer(seed: int = 0, **fields) -> Humanizer:
    """A seeded, sleep-free humanizer for pacing assertions."""
    return Humanizer(BehaviorProfile(**fields), rng=random.Random(seed), sleep=lambda s: None)


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


# --- humanized paging -----------------------------------------------------


class _EndlessComments:
    """A post with unlimited comment pages, 10 comments each."""

    def __init__(self) -> None:
        self.pages = 0

    def media_id(self, pk):
        return pk

    def media_pk_from_url(self, url):
        return _Media.pk

    def media_info(self, pk):
        return _Media()

    def private_request(self, path, params=None):
        self.pages += 1
        base = self.pages * 10
        return {
            "comments": [{"u": f"u{base + i}", "l": i, "t": "t"} for i in range(10)],
            "has_more_comments": True,
            "next_max_id": f"page{self.pages}",
        }


def test_early_stop_ends_paging_before_the_limit():
    client = _EndlessComments()
    out = _scan_comments(
        client, "pk", amount=1000, tick=lambda: None,
        humanizer=humanizer(early_stop_prob=1.0),
    )
    assert client.pages == 1  # gave up after the first screenful
    assert len(out) == 10


def test_early_stop_prob_zero_pages_to_the_limit():
    client = _EndlessComments()
    out = _scan_comments(
        client, "pk", amount=50, tick=lambda: None,
        humanizer=humanizer(early_stop_prob=0.0),
    )
    assert len(out) == 50
    assert client.pages == 5


def test_paging_costs_a_sampled_think_time_per_page():
    slept: list[float] = []
    hum = Humanizer(
        BehaviorProfile(early_stop_prob=0.0, page_delay=Range(2.0, 8.0), long_pause_prob=0.0),
        rng=random.Random(3),
        sleep=slept.append,
    )
    _scan_comments(_EndlessComments(), "pk", amount=50, tick=lambda: None, humanizer=hum)
    assert len(slept) == 4  # one per *extra* page fetched
    assert all(2.0 <= s <= 8.0 for s in slept)


def test_paged_requests_are_recorded_against_the_rate_ceilings():
    hum = humanizer(early_stop_prob=0.0)
    _scan_comments(_EndlessComments(), "pk", amount=30, tick=lambda: None, humanizer=hum)
    assert hum.requests == 3


def test_scan_limit_zero_is_clamped_under_humanization():
    client = _EndlessComments()
    _, result = scrape(
        client, "DXOCAyzEX8i", "https://www.instagram.com/reel/DXOCAyzEX8i/",
        account="tillg", scan_limit=0,
        humanizer=humanizer(early_stop_prob=0.0, scan_depth_clamp=200),
    )
    assert result.provenance.comments_scanned == 200  # not "all"
    assert result.provenance.comment_scan_limit == 0  # the configured value is kept


def test_no_humanizer_still_means_scan_everything():
    class Finite:
        pages = 0

        def media_id(self, pk):
            return pk

        def media_pk_from_url(self, url):
            return _Media.pk

        def media_info(self, pk):
            return _Media()

        def private_request(self, path, params=None):
            Finite.pages += 1
            more = Finite.pages < 3
            return {
                "comments": [{"u": f"u{Finite.pages}", "l": 1, "t": "t"}],
                "has_more_comments": more,
                "next_max_id": "x" if more else None,
            }

    _, result = scrape(
        Finite(), "DXOCAyzEX8i", "https://www.instagram.com/reel/DXOCAyzEX8i/",
        account="tillg", scan_limit=0,
    )
    assert result.provenance.comments_scanned == 3
    assert result.provenance.humanization == "off"


def test_provenance_records_the_effective_pacing():
    _, result = scrape(
        _FakeClient(), "DXOCAyzEX8i", "https://www.instagram.com/reel/DXOCAyzEX8i/",
        account="tillg", humanizer=humanizer(post_delay=Range(20, 90)),
    )
    prov = result.provenance
    assert prov.humanization.startswith("on ·")
    assert "post 20–90s" in prov.humanization
    assert prov.comments_scanned == 2  # the single page the fake returns


def test_media_info_counts_toward_the_rate_ceilings():
    hum = humanizer()
    scrape(
        _FakeClient(), "DXOCAyzEX8i", "https://www.instagram.com/reel/DXOCAyzEX8i/",
        account="tillg", humanizer=hum,
    )
    assert hum.requests == 2  # media_info + one comment page
