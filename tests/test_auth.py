"""Network-free tests for auth helpers."""

import random
from pathlib import Path

import pytest
from instagrapi import Client as UpstreamClient

import instascraper.auth as auth
import instascraper.fingerprint as fingerprint
from instascraper.auth import make_links_clickable
from instascraper.behavior import BehaviorProfile, Humanizer, Range


def test_auth_builds_the_fingerprinted_client():
    # The rest of these tests monkeypatch `auth.Client`, so they pass whichever
    # class it is. This one pins the wiring that makes fingerprint.py take effect.
    assert auth.Client is fingerprint.Client
    assert issubclass(auth.Client, UpstreamClient)
    client = auth._build_client()
    assert "IG-U-SHBID" not in client.base_headers


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


# --- per-request pacing comes from the behavior profile -------------------


def test_delay_range_falls_back_to_the_constant_without_a_humanizer():
    assert auth._delay_range(None) == auth.DELAY_RANGE


def test_delay_range_comes_from_the_profile_when_humanizing():
    hum = Humanizer(BehaviorProfile(request_delay=Range(2.0, 7.0)))
    assert auth._delay_range(hum) == [2.0, 7.0]


def test_unhumanized_profile_keeps_the_old_constant():
    hum = Humanizer(BehaviorProfile(enabled=False, request_delay=Range(2.0, 7.0)))
    assert auth._delay_range(hum) == auth.DELAY_RANGE


# --- device-identity continuity -------------------------------------------


ANDROID_UA = "Instagram 428.0.0.47.67 Android (34/14; 480dpi; Google/google; en_US)"


def test_device_family_reads_the_persisted_user_agent():
    assert auth.device_family({"user_agent": ANDROID_UA}) == "android"
    assert auth.device_family({"user_agent": auth.IOS_USER_AGENT}) == "ios"
    assert auth.device_family({}) == "android"  # unknown → instagrapi's default


class _RecordingProgress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def start(self, label): ...
    def ok(self, result="done"): ...
    def tick(self): ...
    def done(self): ...

    def stage(self, msg):
        self.messages.append(msg)


class _FakeClient:
    """The slice of instagrapi.Client that `get_client` actually touches."""

    instances: list["_FakeClient"] = []
    session_user_agent = ANDROID_UA

    def __init__(self) -> None:
        _FakeClient.instances.append(self)
        self.username = "tillg"
        self.settings: dict = {"uuids": {}}
        self.user_agent = ANDROID_UA
        self.device = None
        self.device_applied = False
        self.feed_calls = 0
        self.logins = 0
        self.uuids = None

    def load_settings(self, path):
        self.user_agent = _FakeClient.session_user_agent

    def get_settings(self):
        return {"uuids": {"uuid": "stable"}, "user_agent": self.user_agent}

    def get_timeline_feed(self):
        self.feed_calls += 1
        return {}

    def set_device(self, device=None):
        self.device = device or {}
        self.device_applied = True
        self.user_agent = ANDROID_UA  # set_device rebuilds the UA, Android-style

    def set_user_agent(self, user_agent=""):
        self.user_agent = user_agent

    def set_uuids(self, uuids):
        self.uuids = uuids
        self.settings["uuids"] = uuids

    def dump_settings(self, path):
        Path(path).write_text("{}", encoding="utf-8")

    def login(self, username, password, verification_code=None):
        self.logins += 1


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.instances = []
    _FakeClient.session_user_agent = ANDROID_UA
    monkeypatch.setattr(auth, "Client", _FakeClient)
    return _FakeClient


def _new_session_login(tmp_path, device_profile, humanizer=None):
    progress = _RecordingProgress()
    client, account = auth.get_client(
        session_file=str(tmp_path / "session-tillg.json"),
        username="tillg",
        password="pw",
        progress=progress,
        humanizer=humanizer,
        device_profile=device_profile,
    )
    return client, progress


def test_new_session_gets_the_configured_device_family(fake_client, tmp_path):
    client, progress = _new_session_login(tmp_path, "android")
    assert client.device_applied is True
    assert auth.device_family({"user_agent": client.user_agent}) == "android"
    assert client.logins == 1
    assert progress.messages == []  # nothing to warn about


def test_ios_device_profile_sets_an_ios_user_agent_and_warns(fake_client, tmp_path):
    client, progress = _new_session_login(tmp_path, "ios")
    assert client.device == auth.IOS_DEVICE
    assert client.user_agent == auth.IOS_USER_AGENT
    # Honest about the limitation: the envelope stays Android.
    assert any("user-agent only" in m for m in progress.messages)


def test_new_session_delay_range_reflects_the_profile(fake_client, tmp_path):
    hum = Humanizer(BehaviorProfile(request_delay=Range(2.0, 7.0), warmup_calls=Range(0, 0)))
    client, _ = _new_session_login(tmp_path, "android", humanizer=hum)
    assert client.delay_range == [2.0, 7.0]


def _reuse_session(tmp_path, device_profile, humanizer=None):
    spath = tmp_path / "session-tillg.json"
    spath.write_text("{}", encoding="utf-8")
    progress = _RecordingProgress()
    client, account = auth.get_client(
        session_file=str(spath),
        username="tillg",
        progress=progress,
        humanizer=humanizer,
        device_profile=device_profile,
    )
    return client, progress


def test_reused_session_keeps_its_device_and_never_relogins(fake_client, tmp_path):
    # Session was minted as Android; config now asks for iOS.
    client, progress = _reuse_session(tmp_path, "ios")
    assert client.device_applied is False       # session is authoritative
    assert client.user_agent == ANDROID_UA      # not re-fingerprinted
    assert client.logins == 0                   # and never speculatively re-logged in
    notice = " ".join(progress.messages)
    assert "'android'" in notice and "'ios'" in notice
    assert "delete" in notice and "new-device prompt" in notice


def test_reused_session_matching_the_config_says_nothing(fake_client, tmp_path):
    client, progress = _reuse_session(tmp_path, "android")
    assert progress.messages == []
    assert client.logins == 0


def test_reused_session_applies_profile_pacing(fake_client, tmp_path):
    hum = Humanizer(BehaviorProfile(request_delay=Range(3.0, 9.0), warmup_calls=Range(0, 0)))
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert client.delay_range == [3.0, 9.0]


def test_zero_warmup_calls_makes_no_extra_requests(fake_client, tmp_path):
    hum = Humanizer(BehaviorProfile(warmup_calls=Range(0, 0)))
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert client.feed_calls == 1  # only the session-validation call


def test_warmup_adds_app_open_calls(fake_client, tmp_path):
    slept: list[float] = []
    hum = Humanizer(
        BehaviorProfile(warmup_calls=Range(2, 2)),
        rng=random.Random(0),
        sleep=slept.append,
    )
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert client.feed_calls == 3  # validation + 2 warm-up calls
    assert len(slept) == 2         # each preceded by a sampled think-time


def test_no_humanizer_keeps_todays_behavior(fake_client, tmp_path):
    client, progress = _reuse_session(tmp_path, "android")
    assert client.delay_range == auth.DELAY_RANGE
    assert client.feed_calls == 1
