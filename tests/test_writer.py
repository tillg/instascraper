from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from instascraper.models import Comment, Provenance, ScrapeResult
from instascraper.writer import render_markdown, render_metadata, write_result


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
            comments_scanned=137,
            humanization="on · post 20–90s · early-stop p=0.3",
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


def test_caveat_states_what_was_actually_scanned_not_the_limit(result: ScrapeResult) -> None:
    # Early-stop / the depth clamp make these differ; don't overstate depth.
    md = render_markdown(result, MEDIA)
    assert "among 137 comments scanned (limit 200)" in md


def test_pacing_line_reports_the_humanization_summary(result: ScrapeResult) -> None:
    md = render_markdown(result, MEDIA)
    assert "> Pacing: humanization on · post 20–90s · early-stop p=0.3" in md


def test_metadata_carries_scanned_count_and_humanization(result: ScrapeResult) -> None:
    prov = render_metadata(result, MEDIA)["provenance"]
    assert prov["comments_scanned"] == 137
    assert prov["comment_scan_limit"] == 200
    assert prov["humanization"].startswith("on ·")


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
            comments_scanned=0,
        ),
    )
    md = render_markdown(r, [])
    assert md.startswith("# @x — Post")
    assert "_No caption._" in md
    assert "_No media files._" in md
    assert "_No comments returned._" in md
    assert "0 comments scanned (no limit)" in md


# --- downloads go through the private API only -----------------------------


class _Resource:
    def __init__(self, pk, media_type, thumbnail_url=None, video_url=None):
        self.pk = pk
        self.media_type = media_type
        self.thumbnail_url = thumbnail_url
        self.video_url = video_url


class _Album:
    pk = "3877045179849473826"
    media_type = 8
    thumbnail_url = None
    resources = [
        _Resource("r1", 1, thumbnail_url="https://cdn.example.com/one.jpg"),
        _Resource("r2", 2, video_url="https://cdn.example.com/two.mp4"),
        _Resource("r3", 1, thumbnail_url="https://cdn.example.com/three.jpg"),
    ]


class _Video:
    pk = "3877045179849473826"
    media_type = 2
    video_url = "https://cdn.example.com/reel.mp4"
    thumbnail_url = "https://cdn.example.com/cover.jpg"


class _Photo:
    pk = "3877045179849473826"
    media_type = 1
    thumbnail_url = "https://cdn.example.com/pic.jpg"
    video_url = None


class _DownloadClient:
    """Records by-URL downloads and creates the files, like instagrapi does.

    The metadata-refetching helpers are booby-trapped: each one reaches
    instagrapi's dead web-GraphQL fallback (`photo_download` tries it *first*),
    which answers 200 with an HTML login wall. `write_result` already holds the
    `media` object, so it must never call them.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _write(self, url, filename, folder):
        path = Path(folder) / f"{filename}.{url.rsplit('.', 1)[1]}"
        path.write_bytes(b"fake-media")
        return path

    def photo_download_by_url(self, url, filename="", folder="", overwrite=True):
        self.calls.append(("photo", url))
        return self._write(url, filename, folder)

    def video_download_by_url(self, url, filename="", folder="", overwrite=True):
        self.calls.append(("video", url))
        return self._write(url, filename, folder)

    def album_download(self, *a, **k):
        raise AssertionError("album_download re-fetches metadata via media_info")

    def photo_download(self, *a, **k):
        raise AssertionError("photo_download tries web GraphQL first")

    def video_download(self, *a, **k):
        raise AssertionError("video_download re-fetches metadata")


@pytest.fixture
def no_cover_network(monkeypatch):
    """Stub the cover fetch at the network boundary (urllib), not in our code."""
    def fake_urlretrieve(url, dest):
        Path(dest).write_bytes(b"fake-cover")
        return dest, None

    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)


def test_album_downloads_every_resource_in_carousel_order(result, tmp_path, no_cover_network):
    client = _DownloadClient()
    out = write_result(client, _Album(), result, str(tmp_path))
    assert client.calls == [
        ("photo", "https://cdn.example.com/one.jpg"),
        ("video", "https://cdn.example.com/two.mp4"),
        ("photo", "https://cdn.example.com/three.jpg"),
    ]
    assert sorted(p.name for p in out.glob("DXOCAyzEX8i*")) == [
        "DXOCAyzEX8i_1.jpg", "DXOCAyzEX8i_2.mp4", "DXOCAyzEX8i_3.jpg",
    ]


def test_single_video_downloads_by_url_and_still_gets_a_cover(result, tmp_path, no_cover_network):
    client = _DownloadClient()
    out = write_result(client, _Video(), result, str(tmp_path))
    assert client.calls == [("video", "https://cdn.example.com/reel.mp4")]
    names = sorted(p.name for p in out.glob("DXOCAyzEX8i.*"))
    assert names == ["DXOCAyzEX8i.jpg", "DXOCAyzEX8i.mp4"]  # video + cover


def test_single_photo_downloads_its_thumbnail(result, tmp_path, no_cover_network):
    client = _DownloadClient()
    out = write_result(client, _Photo(), result, str(tmp_path))
    assert client.calls == [("photo", "https://cdn.example.com/pic.jpg")]
    assert (out / "DXOCAyzEX8i.jpg").exists()


def test_unknown_carousel_item_is_loud_not_silently_dropped(result, tmp_path, no_cover_network):
    class Weird:
        pk = "x"
        media_type = 8
        thumbnail_url = None
        resources = [_Resource("r1", 99)]

    with pytest.raises(ValueError, match="media_type"):
        write_result(_DownloadClient(), Weird(), result, str(tmp_path))


def test_unsupported_top_level_media_writes_files_without_download(result, tmp_path, no_cover_network):
    class Story:
        pk = "x"
        media_type = 7  # not image/video/album
        thumbnail_url = None

    out = write_result(_DownloadClient(), Story(), result, str(tmp_path))
    assert (out / "post.md").exists()
    assert "_No media files._" in (out / "post.md").read_text(encoding="utf-8")


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
