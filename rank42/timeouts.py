"""Small Unix-process timeout helpers for expensive single-candidate stages."""
from __future__ import annotations

import contextlib
import signal


class StageTimeout(TimeoutError):
    pass


@contextlib.contextmanager
def hard_timeout(seconds: int | float | None, label: str = "stage"):
    """Interrupt an expensive in-process stage with SIGALRM on Unix.

    Rank Hunter's scientific workers run as dedicated subprocesses on Linux, so
    a process-local alarm is the smallest reliable guard that leaves the outer
    batch alive and able to record the candidate as completed-with-timeout.
    """
    seconds = float(seconds or 0)
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _alarm(_signum, _frame):
        raise StageTimeout(f"{label} exceeded {seconds:g}s")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
