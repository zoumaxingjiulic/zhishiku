"""Monotonic deadlines shared across sequential external operations."""
import time


class DeadlineExceeded(TimeoutError):
    """A bounded operation exhausted its deadline; do not turn it into fallback data."""


def remaining_timeout(deadline: float | None, default: float, check_active=None) -> float:
    if check_active:
        check_active()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded('Assistant execution deadline exceeded')
    return min(default, remaining)
