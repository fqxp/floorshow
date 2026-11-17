import shutil
import subprocess
import time

import pytest

from floorshow.render.inhibit import IdleInhibitor

CALL = ["gdbus", "call", "--session", "--dest", "org.gnome.SessionManager", "--object-path", "/org/gnome/SessionManager",
        "--method", "org.gnome.SessionManager.GetInhibitors"]


def inhibitors():
    out = subprocess.run(CALL, capture_output=True, text=True, timeout=5)
    return out.stdout.count("/org/gnome/SessionManager/Inhibitor") if out.returncode == 0 else None


@pytest.mark.skipif(not shutil.which("gnome-session-inhibit") or not shutil.which("gdbus") or inhibitors() is None,
                    reason="needs a GNOME session")
def test_inhibitor_registers_with_the_session_and_releases_on_stop():
    before = inhibitors()
    inh = IdleInhibitor("test")
    assert inh.start() and inh.active
    for _ in range(50):
        if inhibitors() == before + 1:
            break
        time.sleep(0.1)
    assert inhibitors() == before + 1
    inh.stop()
    assert not inh.active
    for _ in range(50):
        if inhibitors() == before:
            break
        time.sleep(0.1)
    assert inhibitors() == before


def test_missing_tool_is_not_an_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    inh = IdleInhibitor()
    assert inh.start() is False and not inh.active
    inh.stop()  # harmless
