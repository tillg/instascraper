"""HTTP identity: one place that owns what a request looks like on the wire.

`behavior.py` decides *when* to send a request; this module decides *what the
request is*. They are separate problems, and pacing cannot fix any of the ones
here: a request that presents another account's signed tokens is identifiable no
matter how long you waited before sending it.

instagrapi's `base_headers` (`instagrapi/mixins/private.py:207`) is mostly a
faithful Android envelope. Four things in it are not:

- **Forged per-user signed tokens.** `IG-U-SHBID`, `IG-U-SHBTS`, `IG-U-RUR` and
  `IG-U-IG-DIRECT-REGION-HINT` are filled from HMAC blobs *hardcoded in the
  library* — captured from someone else's session years ago. Instagram mints
  these per account, so presenting them is a mismatch on values a client cannot
  forge. We drop them; sending nothing is what a client that hasn't been issued
  one does, and `IG-U-RUR` returns as soon as Instagram issues a real one.
- **A telemetry session that changes every request.** `base_headers` is a
  `@property`, so `X-Pigeon-Session-Id` is rebuilt on every call. A real app
  holds one pigeon session for as long as it is in the foreground — so we hold
  one per `Client`, which is one per run.
- **A WWW-claim frozen at `0` forever.** Instagram answers with
  `x-ig-set-www-claim` and expects it echoed on subsequent requests. instagrapi
  only ever reads that header in its bloks flow (`mixins/bloks.py:697`), never in
  `private_request`, so every request for the lifetime of the session keeps
  claiming `0`. `_absorb_www_claim` closes the loop.
- **Media bytes that aren't Instagram at all** — the loudest of the four.
  `photo_download_by_url` / `video_download_by_url` call *module-level*
  `requests.get` (`mixins/photo.py:121`, `mixins/video.py:105`), so CDN fetches
  leave as `python-requests/x.y` with no app headers, seconds after an "Instagram
  Android" API call from the same IP. The two correlate perfectly on IP and
  timing. `_download_to_path` sends them as the app instead.

What this module deliberately does **not** attempt is making the device look
unique. The device/app-version/bloks triple in `instagrapi/config.py:15-31` is
shared by every user of the library, but rotating it on a live session is itself
the new-device event `auth.py` works to avoid — so it stays the session's
business, minted once (`auth._apply_device`) and never touched again.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests
from instagrapi import Client as _UpstreamClient

# Headers instagrapi populates with HMAC blobs hardcoded in the library. Unlike
# `IG-U-RUR` there is no path by which we could ever learn the real values, so
# these are dropped unconditionally.
FORGED_HEADERS = (
    "IG-U-SHBID",
    "IG-U-SHBTS",
    "IG-U-IG-DIRECT-REGION-HINT",
)

# Every request this tool makes is "followed a post link, then read its
# comments", so the entry point is a deep link. instagrapi instead sends a
# constant `9MV:self_profile:2,…,9Xf:self_following:4` on every request,
# including cold media fetches, which contradicts the request it rides on.
#
# Inferred from the app's fragment naming, not captured from a live client — so
# it is a better-shaped guess rather than ground truth. It is per-client
# settable (`client.nav_chain`) for when a capture is available.
NAV_CHAIN = "ContextualFeedFragment:feed_contextual_post:2:deep_link::"

CDN_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


class Client(_UpstreamClient):
    """An `instagrapi.Client` whose requests don't announce instagrapi.

    Drop-in: overrides only the header assembly and the CDN transport, so every
    call site (`auth.py`, `scraper.py`, `writer.py`) is unchanged.
    """

    # Class-level defaults: `base_headers` can be read during `__init__`, before
    # any instance attribute of ours exists.
    _pigeon_session_id = ""
    _cdn: requests.Session | None = None
    nav_chain = NAV_CHAIN

    # --- header assembly -------------------------------------------------

    @property
    def pigeon_session_id(self) -> str:
        """One telemetry session per client — a real app holds one per foreground."""
        if not self._pigeon_session_id:
            self._pigeon_session_id = self.generate_uuid("UFS-", "-1")
        return self._pigeon_session_id

    @property
    def base_headers(self) -> dict:
        headers = super().base_headers
        for name in FORGED_HEADERS:
            headers.pop(name, None)
        if not self.ig_u_rur:
            # Upstream fills this with its forged blob whenever `user_id` is set,
            # and only overwrites it if a real one was captured.
            headers.pop("IG-U-RUR", None)
        headers["X-Pigeon-Session-Id"] = self.pigeon_session_id
        headers["X-IG-Nav-Chain"] = self.nav_chain
        return headers

    # --- the claim loop --------------------------------------------------

    def private_request(self, *args, **kwargs):
        try:
            return super().private_request(*args, **kwargs)
        finally:
            # Also on failure: a rate-limit or challenge response carries a claim
            # too, and the retry should present it.
            self._absorb_www_claim()

    def _absorb_www_claim(self) -> None:
        """Adopt the WWW-claim Instagram issued, so later requests echo it."""
        headers = getattr(getattr(self, "last_response", None), "headers", None) or {}
        claim = headers.get("x-ig-set-www-claim") or headers.get("X-IG-Set-WWW-Claim")
        if claim and claim != self.ig_www_claim:
            self.set_ig_www_claim(claim)

    # --- media bytes -----------------------------------------------------

    @property
    def cdn(self) -> requests.Session:
        """Connection-reusing session for media bytes."""
        if self._cdn is None:
            session = requests.Session()
            session.headers.update(CDN_HEADERS)
            self._cdn = session
        return self._cdn

    def _cdn_get(self, url: str):
        # The user-agent is read per request, not cached on the session: it is
        # only final once `auth.get_client` has loaded or minted the session.
        response = self.cdn.get(
            str(url),
            stream=True,
            timeout=self.request_timeout,
            headers={"User-Agent": self.user_agent},
        )
        response.raise_for_status()
        return response

    def _download_to_path(self, url: str, filename: str, folder, overwrite: bool) -> Path:
        """Upstream's by-url download, minus the module-level `requests.get`.

        Mirrors `photo_download_by_url` / `video_download_by_url` exactly —
        same filename derivation, same overwrite semantics, same completeness
        check — so `writer.py` keeps its contract (see CLAUDE.md: download by
        URL, never through the metadata-refetching helpers).
        """
        url = str(url)
        fname = urlparse(url).path.rsplit("/", 1)[1]
        filename = f"{filename}.{fname.rsplit('.', 1)[1]}" if filename else fname
        path = Path(folder) / filename
        if path.exists() and not overwrite:
            return path.resolve()
        return self._download_response_to_path(self._cdn_get(url), path)

    def _download_bytes(self, url: str) -> bytes:
        return self._download_response_bytes(self._cdn_get(url), str(url))

    def photo_download_by_url(self, url, filename="", folder="", overwrite=True) -> Path:
        return self._download_to_path(url, filename, folder, overwrite)

    def video_download_by_url(self, url, filename="", folder="", overwrite=True) -> Path:
        return self._download_to_path(url, filename, folder, overwrite)

    def photo_download_by_url_origin(self, url: str) -> bytes:
        return self._download_bytes(url)

    def video_download_by_url_origin(self, url: str) -> bytes:
        return self._download_bytes(url)
