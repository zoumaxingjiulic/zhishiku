"""Monotonic deadlines shared across sequential external operations."""
import time


def remaining_timeout(deadline: float | None, default: float, check_active=None) -> float:
    if check_active:
        check_active()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Assistant execution deadline exceeded')
    return min(default, remaining)
