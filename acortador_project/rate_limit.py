"""Rate limiting utility — reusable across all apps.

Provides atomic rate limiting using Django's cache framework.
Requires a cache backend that supports atomic incr (LocMemCache,
Redis). FileBasedCache does NOT support atomic incr reliably.

Usage:
    from portfolio.rate_limit import rate_limit, burst_limit

    # Per-IP limit: 10 requests per hour
    allowed, remaining = rate_limit(f'login:{ip}', limit=10, ttl=3600)
    if not allowed:
        return HttpResponse(status=429)

    # Burst: 1 request per 5 seconds
    if not burst_limit(f'burst:{ip}', ttl=5):
        return HttpResponse(status=429)
"""

from django.core.cache import cache


def rate_limit(key, limit, ttl):
    """Atomic sliding-window rate limit.

    Increments a counter in cache atomically. Returns (allowed, remaining).
    On first call, initializes the key with TTL.

    Args:
        key: Cache key (should include IP or user ID).
        limit: Max requests allowed in the window.
        ttl: Window duration in seconds.

    Returns:
        (True, remaining) if under limit, (False, 0) if exceeded.
    """
    cache.add(key, 0, ttl)
    try:
        current = cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl)
        current = 1

    if current > limit:
        return False, 0
    return True, limit - current


def burst_limit(key, ttl):
    """Anti-burst: allows 1 request per TTL seconds.

    Returns True if allowed (first request in window), False if blocked.
    """
    if cache.add(key, 1, ttl):
        return True
    return False


def client_ip(request):
    """Extract real client IP, handling proxy headers."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')
