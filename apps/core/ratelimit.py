"""Fixed-window rate limiting on the shared cache (Redis in production)."""

from django.core.cache import cache


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Count one hit against `key`; return False once more than `limit` hits fall in the window."""
    cache_key = f"rl:{key}"
    cache.add(cache_key, 0, timeout=window_seconds)  # no-op if the window is already open
    try:
        count = cache.incr(cache_key)
    except ValueError:  # key expired between add() and incr()
        cache.set(cache_key, 1, timeout=window_seconds)
        count = 1
    return count <= limit


def claim_cooldown(key: str, seconds: int) -> bool:
    """Atomically claim a cooldown slot. Returns False if one is still running."""
    return cache.add(f"cd:{key}", 1, timeout=seconds)
