import io
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from floorshow.render.watchdog import StallWatchdog


def test_dumps_once_per_stall_and_rearms():
    dumps = []
    w = StallWatchdog(dumps.append)
    assert w.check(0.0, True) is False
    assert w.check(2.0, False, 2.0) is True  # first stalled check dumps
    assert w.check(3.0, False, 3.0) is False and w.check(9.0, False, 9.0) is False  # ...and only once
    assert len(dumps) == 1 and "no frame for 2.0 s" in dumps[0]
    assert w.check(10.0, True) is False  # frames flow again: re-armed
    assert w.check(13.0, False, 3.0) is True and len(dumps) == 2


def test_a_real_dump_shows_what_the_blocked_thread_is_waiting_on(tmp_path):
    """The point of the watchdog: the dump names the blocking call."""
    from floorshow.render.watchdog import file_dumper
    import threading

    gate = threading.Event()

    def blocked_in_swap_like_call():
        gate.wait(10)

    t = threading.Thread(target=blocked_in_swap_like_call, name="render", daemon=True)
    t.start()
    time.sleep(0.1)
    with (tmp_path / "stacks").open("w") as f:
        file_dumper(f)("=== stall ===\n")
    gate.set()
    text = (tmp_path / "stacks").read_text()
    assert "=== stall ===" in text and "blocked_in_swap_like_call" in text and "render" in text


@pytest.mark.skipif(sys.platform != "linux", reason="SIGUSR1 dump is Linux-only here")
def test_sigusr1_dumps_stacks_of_a_running_process(tmp_path):
    script = textwrap.dedent(f"""
        import time
        from floorshow.render.watchdog import install_signal_dump
        f = open({str(tmp_path / 'sig.stacks')!r}, 'a', buffering=1)
        install_signal_dump(f)
        def waiting_forever():
            time.sleep(30)
        print('ready', flush=True)
        waiting_forever()
    """)
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ready"
        os.kill(p.pid, signal.SIGUSR1)
        for _ in range(50):
            time.sleep(0.1)
            if (tmp_path / "sig.stacks").exists() and "waiting_forever" in (tmp_path / "sig.stacks").read_text():
                break
        assert "waiting_forever" in (tmp_path / "sig.stacks").read_text()
    finally:
        p.kill()
