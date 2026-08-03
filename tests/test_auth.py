"""Network-free tests for auth helpers."""

import pytest

import instascraper.auth as auth
from instascraper.auth import make_links_clickable


def test_relative_checkpoint_url_becomes_absolute():
    msg = (
        "Login: Checkpoint required. Point your browser to "
        "/auth_platform/?apc=Adrc5m7jKWAw_abc-XYZ_123 - follow the instructions, "
        "then retry."
    )
    out = make_links_clickable(msg)
    assert "https://www.instagram.com/auth_platform/?apc=Adrc5m7jKWAw_abc-XYZ_123" in out
    assert out.endswith("then retry.")


def test_message_without_relative_url_is_unchanged():
    msg = "Login error: bad password."
    assert make_links_clickable(msg) == msg


class _FakeCookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


def test_sessionid_returned_when_present(monkeypatch):
    import browser_cookie3

    monkeypatch.setattr(
        browser_cookie3, "chrome",
        lambda *a, **k: [_FakeCookie("csrftoken", "x"), _FakeCookie("sessionid", "abc123")],
        raising=False,
    )
    assert auth._sessionid_from_browser("chrome") == "abc123"


def test_no_session_cookie_reports_login_needed(monkeypatch):
    import browser_cookie3

    monkeypatch.setattr(
        browser_cookie3, "chrome",
        lambda *a, **k: [_FakeCookie("csrftoken", "x")],  # no sessionid
        raising=False,
    )
    with pytest.raises(SystemExit) as ei:
        auth._sessionid_from_browser("chrome")
    assert "log into" in str(ei.value).lower()


def test_permission_error_explains_full_disk_access(monkeypatch):
    import browser_cookie3

    def boom(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(browser_cookie3, "safari", boom, raising=False)
    with pytest.raises(SystemExit) as ei:
        auth._sessionid_from_browser("safari")
    assert "Full Disk Access" in str(ei.value)


def test_unsupported_browser_rejected():
    with pytest.raises(SystemExit):
        auth._sessionid_from_browser("netscape")
