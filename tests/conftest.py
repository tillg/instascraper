"""Suite-wide guards: no real config, no real sleeping, no real sockets.

The whole test suite is network-free and sleep-free by design. These autouse
fixtures make that a property the suite *enforces* rather than a convention it
relies on — and, now that a run persists pacing state, they keep every test off
the user's real `~/.config/instascraper/`.
"""

from __future__ import annotations

import socket
import time

import pytest

from instascraper import activity, auth, cli, config


@pytest.fixture(autouse=True)
def private_config_dir(monkeypatch, tmp_path):
    """Redirect every `~/.config/instascraper` path at its point of use.

    Each module binds its own name at import (`from .config import CONFIG_DIR`),
    so the redirect has to be applied per module, not just on `config`.
    """
    home = tmp_path / "config"
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "CONFIG_PATH", home / ".env")
    monkeypatch.setattr(activity, "CONFIG_DIR", home)
    monkeypatch.setattr(auth, "DEFAULT_SESSION_DIR", home)
    monkeypatch.setattr(cli, "CONFIG_PATH", home / ".env")
    return home


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """A real sleep in a test is a bug: inject a clock instead."""

    def boom(seconds):
        raise AssertionError(
            f"time.sleep({seconds!r}) in a test — inject a sleep/clock instead."
        )

    monkeypatch.setattr(time, "sleep", boom)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A real socket in a test is a bug: the suite never talks to Instagram."""

    def boom(self, address):
        raise AssertionError(f"socket.connect({address!r}) in a test — no network.")

    monkeypatch.setattr(socket.socket, "connect", boom)
