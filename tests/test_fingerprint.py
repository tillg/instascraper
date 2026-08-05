"""Network-free tests for the HTTP fingerprint layer.

Each test names the leak it closes and, where it matters, asserts that upstream
instagrapi really does leak it — otherwise a future library fix would leave a
test that passes for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from instagrapi import Client as UpstreamClient

from instascraper.fingerprint import FORGED_HEADERS, Client

AUTHED = {"ds_user_id": "1234567890", "sessionid": "IGT:2:whatever"}


@pytest.fixture
def client() -> Client:
    c = Client()
    c.authorization_data = dict(AUTHED)  # populates the IG-U-* header block
    return c


# --- forged per-user signed tokens ---------------------------------------


def test_upstream_really_does_send_the_forged_tokens() -> None:
    # Guards the premise of the next test: these are hardcoded HMAC blobs in
    # instagrapi, not values Instagram issued for this account.
    upstream = UpstreamClient()
    upstream.authorization_data = dict(AUTHED)
    headers = upstream.base_headers
    for name in FORGED_HEADERS:
        assert name in headers, f"upstream no longer sends {name}"
    assert headers["IG-U-RUR"].endswith("01e544c")  # the library's copied blob


def test_forged_per_user_tokens_are_not_sent(client: Client) -> None:
    headers = client.base_headers
    for name in FORGED_HEADERS:
        assert name not in headers


def test_ig_u_rur_is_dropped_while_it_is_the_libraries_forgery(client: Client) -> None:
    assert "IG-U-RUR" not in client.base_headers


def test_ig_u_rur_is_sent_once_instagram_issues_a_real_one(client: Client) -> None:
    client.set_ig_u_rur("RVA,1234567890,1799999999:01f7realvalue")
    assert client.base_headers["IG-U-RUR"] == "RVA,1234567890,1799999999:01f7realvalue"


def test_authenticated_identity_headers_are_still_sent(client: Client) -> None:
    # Only the unforgeable blobs go; the honest ones must stay or the request
    # stops looking like a logged-in client at all.
    headers = client.base_headers
    assert headers["IG-U-DS-USER-ID"] == "1234567890"
    assert headers["IG-INTENDED-USER-ID"] == "1234567890"


# --- one telemetry session per foreground, not per request ----------------


def test_pigeon_session_id_is_stable_across_requests(client: Client) -> None:
    assert client.base_headers["X-Pigeon-Session-Id"] == client.base_headers[
        "X-Pigeon-Session-Id"
    ]


def test_upstream_regenerates_the_pigeon_session_every_request() -> None:
    upstream = UpstreamClient()
    assert (
        upstream.base_headers["X-Pigeon-Session-Id"]
        != upstream.base_headers["X-Pigeon-Session-Id"]
    )


def test_each_client_gets_its_own_pigeon_session() -> None:
    assert (
        Client().base_headers["X-Pigeon-Session-Id"]
        != Client().base_headers["X-Pigeon-Session-Id"]
    )


def test_pigeon_session_id_keeps_the_upstream_shape(client: Client) -> None:
    assert client.base_headers["X-Pigeon-Session-Id"].startswith("UFS-")
    assert client.base_headers["X-Pigeon-Session-Id"].endswith("-1")


# --- the WWW-claim Instagram issues gets echoed back ----------------------


class _Response:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


def test_claim_starts_at_zero_before_instagram_issues_one(client: Client) -> None:
    assert client.base_headers["X-IG-WWW-Claim"] == "0"


def test_issued_claim_is_absorbed_and_echoed(client: Client) -> None:
    client.last_response = _Response({"x-ig-set-www-claim": "hmac.AR3zClaimValue"})
    client._absorb_www_claim()
    assert client.ig_www_claim == "hmac.AR3zClaimValue"
    assert client.base_headers["X-IG-WWW-Claim"] == "hmac.AR3zClaimValue"


def test_absorbed_claim_is_persisted_into_settings(client: Client) -> None:
    client.last_response = _Response({"X-IG-Set-WWW-Claim": "hmac.MixedCaseHeader"})
    client._absorb_www_claim()
    assert client.settings["ig_www_claim"] == "hmac.MixedCaseHeader"


def test_absorbing_survives_a_response_without_the_header(client: Client) -> None:
    client.last_response = _Response({})
    client._absorb_www_claim()
    assert client.base_headers["X-IG-WWW-Claim"] == "0"


def test_absorbing_survives_no_response_at_all(client: Client) -> None:
    client.last_response = None
    client._absorb_www_claim()  # must not raise


def test_private_request_absorbs_the_claim(monkeypatch, client: Client) -> None:
    def fake_private_request(self, *a, **k):
        self.last_response = _Response({"x-ig-set-www-claim": "hmac.FromRequest"})
        return {"status": "ok"}

    monkeypatch.setattr(UpstreamClient, "private_request", fake_private_request)
    assert client.private_request("media/1/info/") == {"status": "ok"}
    assert client.ig_www_claim == "hmac.FromRequest"


def test_claim_is_absorbed_even_when_the_request_raises(monkeypatch, client: Client) -> None:
    def boom(self, *a, **k):
        self.last_response = _Response({"x-ig-set-www-claim": "hmac.FromError"})
        raise ValueError("rate limited")

    monkeypatch.setattr(UpstreamClient, "private_request", boom)
    with pytest.raises(ValueError):
        client.private_request("media/1/info/")
    assert client.ig_www_claim == "hmac.FromError"


# --- the nav chain matches the request it rides on ------------------------


def test_nav_chain_is_not_the_self_profile_constant(client: Client) -> None:
    assert "self_following" not in client.base_headers["X-IG-Nav-Chain"]
    assert "self_profile" not in client.base_headers["X-IG-Nav-Chain"]


def test_nav_chain_is_overridable_per_client(client: Client) -> None:
    client.nav_chain = "MainFeedFragment:feed_timeline:1:cold_start::"
    assert client.base_headers["X-IG-Nav-Chain"] == (
        "MainFeedFragment:feed_timeline:1:cold_start::"
    )


# --- media bytes carry the same identity as the API calls -----------------


class _CdnResponse:
    """Enough of a streamed `requests` response for the download helpers."""

    def __init__(self, body: bytes = b"fake-media") -> None:
        self.body = body
        self.headers = {"Content-Length": str(len(body))}
        self.raw = _Raw(body)

    @property
    def content(self) -> bytes:
        return self.body

    def raise_for_status(self) -> None: ...


class _Raw:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.decode_content = False

    def read(self, size: int = -1) -> bytes:
        body, self._body = self._body, b""
        return body


@pytest.fixture
def cdn(monkeypatch, client: Client) -> list[dict]:
    """Capture CDN requests instead of making them."""
    seen: list[dict] = []

    def fake_get(url, **kwargs):
        seen.append({"url": url, **kwargs})
        return _CdnResponse()

    monkeypatch.setattr(client.cdn, "get", fake_get)
    return seen


def test_photo_download_sends_the_app_user_agent(client: Client, cdn, tmp_path) -> None:
    client.photo_download_by_url(
        "https://scontent.cdninstagram.com/v/one.jpg", "stem", tmp_path
    )
    assert cdn[0]["headers"]["User-Agent"] == client.user_agent
    assert "python-requests" not in cdn[0]["headers"]["User-Agent"]
    assert cdn[0]["headers"]["User-Agent"].startswith("Instagram ")


def test_video_download_sends_the_app_user_agent(client: Client, cdn, tmp_path) -> None:
    client.video_download_by_url(
        "https://scontent.cdninstagram.com/v/one.mp4", "stem", tmp_path
    )
    assert cdn[0]["headers"]["User-Agent"] == client.user_agent


def test_download_keeps_upstream_filename_derivation(client: Client, cdn, tmp_path) -> None:
    path = client.photo_download_by_url(
        "https://scontent.cdninstagram.com/v/abc.jpg", "stem", tmp_path
    )
    assert Path(path).name == "stem.jpg"
    assert Path(path).read_bytes() == b"fake-media"


def test_download_without_a_filename_uses_the_url_basename(client: Client, cdn, tmp_path) -> None:
    path = client.photo_download_by_url(
        "https://scontent.cdninstagram.com/v/abc.jpg", folder=tmp_path
    )
    assert Path(path).name == "abc.jpg"


def test_download_honours_overwrite_false(client: Client, cdn, tmp_path) -> None:
    existing = tmp_path / "stem.jpg"
    existing.write_bytes(b"already here")
    path = client.photo_download_by_url(
        "https://scontent.cdninstagram.com/v/abc.jpg", "stem", tmp_path, overwrite=False
    )
    assert Path(path).read_bytes() == b"already here"
    assert cdn == []  # no request at all


def test_by_url_origin_returns_bytes_with_the_app_user_agent(client: Client, cdn) -> None:
    assert client.photo_download_by_url_origin(
        "https://scontent.cdninstagram.com/v/cover.jpg"
    ) == b"fake-media"
    assert cdn[0]["headers"]["User-Agent"] == client.user_agent


def test_downloads_never_use_module_level_requests(monkeypatch, client: Client, tmp_path) -> None:
    # instagrapi's own by-url helpers call `requests.get` directly, which sends
    # `python-requests/x.y` to the CDN. Prove we no longer reach that call.
    import instagrapi.mixins.photo as photo_mixin
    import instagrapi.mixins.video as video_mixin

    def forbidden(*a, **k):
        raise AssertionError("CDN fetch escaped through module-level requests.get")

    monkeypatch.setattr(photo_mixin.requests, "get", forbidden)
    monkeypatch.setattr(video_mixin.requests, "get", forbidden)
    monkeypatch.setattr(client.cdn, "get", lambda url, **k: _CdnResponse())

    client.photo_download_by_url("https://cdn.example.com/a.jpg", "p", tmp_path)
    client.video_download_by_url("https://cdn.example.com/a.mp4", "v", tmp_path)
    client.photo_download_by_url_origin("https://cdn.example.com/c.jpg")


def test_cdn_session_is_reused_across_downloads(client: Client) -> None:
    assert client.cdn is client.cdn
