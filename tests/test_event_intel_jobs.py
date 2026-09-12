"""The durable worker's renewal thread, and the contextvars it needs.

run_once() spawns a background thread whose only job is to keep renewing the
lease while the real research runs on the main thread. That thread used to
start with nothing setting event_intel_jobs.CURRENT inside it: a brand new
threading.Thread does not inherit the parent thread's contextvars Context, so
CURRENT.set(job) on the main thread -- regardless of whether it runs before or
after thread.start() -- has no effect on what the child thread's own
CURRENT.get() returns. store._pg_conn() reads CURRENT to stamp the
session-level evi.worker_token GUC on every connection it opens, so a
connection opened from inside the renewal thread was silently getting
token='' instead of the real lease token.

This is inert today only because heartbeat() writes to evi_jobs, a table the
evi_worker_guard trigger does not fence (heartbeat's own UPDATE is scoped by
the real run_id/token bound as query parameters, not by the session GUC). It
would stop being inert the moment anyone adds a guarded-table write inside the
renewal loop, where it would silently fail every time despite a valid lease.
"""
import threading

from tracker import event_intel_jobs as J


class _FakeStop:
    """A stand-in for the real threading.Event `run_once()` passes as `stop`.

    The real renewal loop is `while not stop.wait(20)`: a genuine
    threading.Event would make this test either wait out 20 real seconds or
    require globally monkeypatching Event.wait, which collides with CPython's
    own internal use of Event objects during thread start/shutdown. A minimal
    fake with the one method _renew_loop actually calls sidesteps both.
    """
    def __init__(self, false_times=1):
        self.calls = 0
        self.false_times = false_times

    def wait(self, timeout=None):
        self.calls += 1
        return self.calls > self.false_times


def test_renewal_loop_sets_current_so_a_connection_opened_there_gets_the_real_token(monkeypatch):
    job = {"run_id": 1, "email": "a@b.example", "token": "the-real-lease-token"}
    seen = []

    def fake_heartbeat(j):
        # The exact thing that broke: store._pg_conn() (not exercised here,
        # to keep this test off a real database) reads this same contextvar
        # to decide what worker_token to stamp on the connection it opens.
        seen.append(J.CURRENT.get())
        return False  # ends _renew_loop after one beat, same as a lost lease

    monkeypatch.setattr(J, "heartbeat", fake_heartbeat)

    thread = threading.Thread(target=J._renew_loop, args=(job, _FakeStop()), daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive(), "the renewal thread did not exit"
    assert seen == [job], (
        "CURRENT.get() inside the renewal thread should be the claimed job, "
        "not the default (None) a fresh thread context starts with")


def test_a_heartbeat_exception_ends_the_loop_without_raising(monkeypatch):
    """The database fences a stale worker's later writes after lease expiry
    -- heartbeat() failing is an expected end state, not a crash."""
    def raising_heartbeat(j):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(J, "heartbeat", raising_heartbeat)

    thread = threading.Thread(target=J._renew_loop, args=({"run_id": 1}, _FakeStop()), daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
