"""Network-free tests for auth helpers."""

import random
from pathlib import Path

import pytest
from instagrapi import Client as UpstreamClient

import instascraper.auth as auth
import instascraper.fingerprint as fingerprint
from instascraper.auth import make_links_clickable
from instascraper.activity import ActivityLedger
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
        self.locale = "en_US"
        self.country = None
        self.country_code = None
        self.timezone_offset = None

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

    # geo setters (mirror instagrapi: set_locale rewrites the UA locale)
    def set_locale(self, locale="en_US"):
        self.user_agent = (self.user_agent or "").replace(self.locale, locale)
        self.locale = locale

    def set_country(self, country="US"):
        self.country = country

    def set_country_code(self, country_code=1):
        self.country_code = country_code

    def set_timezone_offset(self, seconds=0, timezone_name=None):
        self.timezone_offset = seconds

    def set_uuids(self, uuids):
        self.uuids = uuids
        self.settings["uuids"] = uuids

    def dump_settings(self, path):
        Path(path).write_text("{}", encoding="utf-8")

    def login(self, username, password, verification_code=None):
        self.logins += 1
        self.login_password = password


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
    # A specific device, deliberately NOT instagrapi's shared default.
    assert client.device == auth.ANDROID_DEVICE
    assert client.device.get("manufacturer") == "samsung"
    assert auth.device_family({"user_agent": client.user_agent}) == "android"
    assert client.logins == 1
    assert progress.messages == []  # nothing to warn about


def test_ios_device_profile_sets_an_ios_user_agent_and_warns(fake_client, tmp_path):
    client, progress = _new_session_login(tmp_path, "ios")
    assert client.device == auth.IOS_DEVICE
    assert "iPhone15,3" in client.user_agent   # still an iOS user-agent…
    assert client.locale == "de_DE"            # …but geo rewrote the locale coherently
    # Honest about the limitation: the envelope stays Android.
    assert any("user-agent only" in m for m in progress.messages)


def test_new_session_applies_the_configured_geo(fake_client, tmp_path):
    progress = _RecordingProgress()
    client, _ = auth.get_client(
        session_file=str(tmp_path / "session-tillg.json"),
        username="tillg", password="pw", progress=progress,
        device_profile="android", geo_profile="de",
    )
    assert (client.locale, client.country, client.country_code) == ("de_DE", "DE", 49)
    assert client.timezone_offset in (3600, 7200)  # Berlin: CET / CEST
    assert "de_DE" in client.user_agent            # locale is coherent on the wire
    assert progress.messages == []                 # "de" is a known profile


def test_unknown_geo_profile_warns_and_falls_back(fake_client, tmp_path):
    progress = _RecordingProgress()
    client, _ = auth.get_client(
        session_file=str(tmp_path / "session-tillg.json"),
        username="tillg", password="pw", progress=progress,
        device_profile="android", geo_profile="mars",
    )
    assert client.country == "DE"  # fell back to the default
    assert any("unknown geo profile" in m for m in progress.messages)


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
    assert client.country is None               # geo not re-stamped on a live session
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


# --- cold opens: the validation request is counted, warm-up is not repeated ---


def _humanizer(tmp_path, *, gap=None, warmup=2, clock_start=10_000.0):
    """A humanizer over a real ledger whose `last_action` is `gap` seconds ago."""
    clock = _LedgerClock(clock_start)
    ledger = ActivityLedger(
        tmp_path / "activity-tillg.json",
        window_seconds=3600, now=clock.now, sleep=clock.sleep,
    ).__enter__()
    if gap is not None:
        ledger.activity.last_action = clock.t - gap
    hum = Humanizer(
        BehaviorProfile(warmup_calls=Range(warmup, warmup)),
        rng=random.Random(0),
        sleep=lambda s: None,
        now=clock.now,
        ledger=ledger,
    )
    return hum, ledger


class _LedgerClock:
    def __init__(self, start: float) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_validating_a_reused_session_counts_as_one_request(fake_client, tmp_path):
    hum, _ = _humanizer(tmp_path, gap=30, warmup=0)
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert client.feed_calls == 1
    assert hum.requests == 1              # the one request no counter used to see
    assert len(hum._window) == 1


def test_a_warm_app_skips_the_warmup(fake_client, tmp_path):
    hum, _ = _humanizer(tmp_path, gap=90)  # inside foreground_idle
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert not hum.is_cold_open()
    assert client.feed_calls == 1          # validation only — no app-open burst


def test_a_cold_open_still_warms_up(fake_client, tmp_path):
    hum, _ = _humanizer(tmp_path, gap=26 * 60)  # cold open, but not a new session
    client, _ = _reuse_session(tmp_path, "android", humanizer=hum)
    assert hum.is_cold_open() and not hum.is_new_session()
    assert client.feed_calls == 3           # validation + 2 warm-up calls


def test_a_fresh_login_warms_up_whatever_the_gap(fake_client, tmp_path):
    """Minting a session is an app-open; gating it on the gap models a script."""
    hum, _ = _humanizer(tmp_path, gap=90)
    assert not hum.is_cold_open()
    client, _ = _new_session_login(tmp_path, "android", humanizer=hum)
    assert client.logins == 1
    assert client.feed_calls == 2           # the 2 warm-up calls, no validation


# --- a login prompt needs a terminal to prompt on -------------------------


def test_headless_login_explains_itself_instead_of_crashing(fake_client, tmp_path, monkeypatch):
    """No session, no password, no TTY — cron and background runs land here."""
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: False, raising=False)

    def unreachable(prompt=""):
        raise AssertionError("must not prompt when there is no terminal")

    monkeypatch.setattr(auth.getpass, "getpass", unreachable)
    with pytest.raises(SystemExit) as ei:
        auth.get_client(
            session_file=str(tmp_path / "session-tillg.json"),  # no session yet
            username="tillg",
            progress=_RecordingProgress(),
        )
    message = str(ei.value)
    assert "no terminal" in message
    assert "IG_PASSWORD" in message          # the headless way out
    assert "interactively" in message        # …and the interactive one


def test_expired_session_is_not_labeled_as_no_session(fake_client, tmp_path, monkeypatch):
    """A saved session rejected by IG must read 'expired', not 'no session'.

    Both used to collapse into the same 'no usable session' message, which the
    ingest wrapper reported as `no-session` — hiding that a re-login (not a
    first-time login) is what's actually needed.
    """
    from instagrapi.exceptions import LoginRequired

    monkeypatch.delenv("IG_PASSWORD", raising=False)
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(
        _FakeClient, "get_timeline_feed",
        lambda self: (_ for _ in ()).throw(LoginRequired("login_required")),
    )
    spath = tmp_path / "session-tillg.json"
    spath.write_text("{}", encoding="utf-8")  # a session that exists but is dead

    with pytest.raises(SystemExit) as ei:
        auth.get_client(
            session_file=str(spath),
            username="tillg",
            progress=_RecordingProgress(),
        )
    message = str(ei.value)
    assert "expired" in message.lower()          # names the real state
    assert "no usable session" not in message    # …not the first-login wording
    assert "--browser safari" in message         # the interactive repair
    assert "IG_PASSWORD" in message              # the headless way out


def test_a_terminal_still_gets_the_password_prompt(fake_client, tmp_path, monkeypatch):
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt="": "typed-in-pw")
    client, account = auth.get_client(
        session_file=str(tmp_path / "session-tillg.json"),
        username="tillg",
        progress=_RecordingProgress(),
    )
    assert client.logins == 1
    assert client.login_password == "typed-in-pw"


# --- non-interactive login (a server supplies the codes) -------------------

from instagrapi.exceptions import BadPassword as IgBadPassword
from instagrapi.exceptions import TwoFactorRequired


class _ScriptedClient(_FakeClient):
    """`login` follows a script: 'bad', '2fa-sms', '2fa-totp', 'challenge', 'boom'."""

    script = "ok"
    instances: list = []

    def __init__(self) -> None:
        super().__init__()
        _ScriptedClient.instances.append(self)
        self.last_json: dict = {}
        self.challenge_code_handler = None
        self.codes_used: list = []

    def login(self, username, password, verification_code=None):
        self.logins += 1
        s = _ScriptedClient.script
        if s == "bad":
            raise IgBadPassword("bad creds for " + password)
        if s == "boom":
            raise RuntimeError("kaput " + password)
        if s.startswith("2fa") and not verification_code:
            self.last_json = {"two_factor_info": {
                "sms_two_factor_on": s == "2fa-sms", "totp_two_factor_on": s == "2fa-totp"}}
            raise TwoFactorRequired("2fa")
        if s == "challenge":
            from instagrapi.mixins.challenge import ChallengeChoice
            verification_code = self.challenge_code_handler(username, ChallengeChoice.EMAIL)
        self.codes_used.append(verification_code)
        self.username = username


@pytest.fixture
def scripted(monkeypatch):
    _ScriptedClient.instances = []
    _ScriptedClient.script = "ok"
    monkeypatch.setattr(auth, "Client", _ScriptedClient)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("input() called")))
    return _ScriptedClient


def _no_code(via):
    raise AssertionError("no code expected")


def test_login_without_codes_saves_session_and_returns_username(scripted, tmp_path):
    out = auth.login_interactive_free("tillg", "s3cret", _no_code, session_dir=tmp_path)
    assert out == "tillg"
    spath = tmp_path / "session-tillg.json"
    assert spath.exists()
    assert (spath.stat().st_mode & 0o777) == 0o600
    c = scripted.instances[0]
    assert c.device_applied and c.country == "DE"   # same stable device + geo as get_client


def test_default_session_dir_is_the_one_get_client_loads_from(scripted, private_config_dir):
    auth.login_interactive_free("tillg", "pw", _no_code)
    assert (private_config_dir / "session-tillg.json").exists()


def test_existing_session_uuids_are_kept(scripted, tmp_path):
    (tmp_path / "session-tillg.json").write_text("{}")
    auth.login_interactive_free("tillg", "pw", _no_code, session_dir=tmp_path)
    assert scripted.instances[0].uuids == {"uuid": "stable"}


@pytest.mark.parametrize("script,via", [("2fa-sms", "SMS"), ("2fa-totp", "authenticator app")])
def test_two_factor_calls_code_with_via(scripted, tmp_path, script, via):
    scripted.script = script
    seen = []
    def code(v):
        seen.append(v)
        return " 123456 "
    assert auth.login_interactive_free("tillg", "pw", code, session_dir=tmp_path) == "tillg"
    assert seen == [via]
    assert scripted.instances[0].codes_used == ["123456"]


def test_challenge_code_calls_code_with_via(scripted, tmp_path):
    scripted.script = "challenge"
    seen = []
    auth.login_interactive_free("tillg", "pw", lambda v: seen.append(v) or "999", session_dir=tmp_path)
    assert seen == ["email"]
    assert scripted.instances[0].codes_used == ["999"]


def test_bad_password(scripted, tmp_path):
    scripted.script = "bad"
    with pytest.raises(auth.BadPassword) as ei:
        auth.login_interactive_free("tillg", "s3cret", _no_code, session_dir=tmp_path)
    assert "s3cret" not in str(ei.value)
    assert not (tmp_path / "session-tillg.json").exists()


def test_code_timeout(scripted, tmp_path):
    scripted.script = "2fa-sms"
    def code(via):
        raise TimeoutError("nobody answered")
    with pytest.raises(auth.CodeTimeout):
        auth.login_interactive_free("tillg", "s3cret", code, session_dir=tmp_path)


def test_other_failures_are_login_failed_without_password(scripted, tmp_path):
    scripted.script = "boom"
    with pytest.raises(auth.LoginFailed) as ei:
        auth.login_interactive_free("tillg", "s3cret", _no_code, session_dir=tmp_path)
    assert "s3cret" not in str(ei.value)
    assert "kaput" in str(ei.value)


def test_code_callback_error_is_login_failed(scripted, tmp_path):
    scripted.script = "2fa-sms"
    def code(via):
        raise ValueError("form closed")
    with pytest.raises(auth.LoginFailed):
        auth.login_interactive_free("tillg", "pw", code, session_dir=tmp_path)


def test_interactive_path_still_prompts_with_input(fake_client, monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or " 42 ")
    class C(_FakeClient):
        def login(self, username, password, verification_code=None):
            if not verification_code:
                raise TwoFactorRequired("2fa")
            self.code = verification_code
    c = C()
    auth._password_login(c, "tillg", "pw")
    assert c.code == "42" and "2FA" in prompts[0]
