"""Simple in-memory rate limiter for FastAPI."""
import time
from collections import defaultdict
from functools import wraps

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse


class RateLimiter:
    """Token-bucket rate limiter keyed by client IP."""

    def __init__(self):
        # {key: [timestamps]}
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    def is_limited(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if key has exceeded max_requests in the last window_seconds."""
        now = time.monotonic()
        # Periodic cleanup every 5 minutes
        if now - self._last_cleanup > 300:
            self._cleanup(now, window_seconds)
        cutoff = now - window_seconds
        hits = self._hits[key]
        # Remove expired entries for this key
        self._hits[key] = hits = [t for t in hits if t > cutoff]
        if len(hits) >= max_requests:
            return True
        hits.append(now)
        return False

    def _cleanup(self, now: float, default_window: int):
        """Remove stale keys to prevent memory growth."""
        self._last_cleanup = now
        cutoff = now - default_window * 2
        stale = [k for k, v in self._hits.items() if not v or v[-1] < cutoff]
        for k in stale:
            del self._hits[k]


# Global instance
_limiter = RateLimiter()


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_429_HTML = (
    '<html><body style="background:#0b0d14;color:#e2e8f0;font-family:sans-serif;'
    'display:flex;align-items:center;justify-content:center;height:100vh;">'
    '<div style="text-align:center"><p style="font-size:3rem;margin:0">429</p>'
    '<p style="color:#94a3b8">Too many requests. Please try again later.</p>'
    '<a href="/" style="color:#3b82f6;font-size:.875rem">&larr; Back</a></div></body></html>'
)


def rate_limit(max_requests: int = 5, window_seconds: int = 60, html: bool = False):
    """Decorator for FastAPI route handlers. Returns 429 when limit exceeded."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if not request:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break
            if request:
                ip = _get_client_ip(request)
                key = f"{func.__module__}.{func.__name__}:{ip}"
                if _limiter.is_limited(key, max_requests, window_seconds):
                    if html:
                        return HTMLResponse(_429_HTML, status_code=429)
                    return JSONResponse(
                        {"error": "Too many requests. Please try again later."},
                        status_code=429,
                    )
            return await func(*args, **kwargs)
        return wrapper
    return decorator
