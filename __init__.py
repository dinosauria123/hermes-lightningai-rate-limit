"""Lightning AI rate limiter — wall-clock aligned 15 requests/minute gate.

Measured against https://lightning.ai/api/v1/chat/completions (2026-10-13):
  x-ratelimit-limit-requests: 15   — fixed 60s window aligned to the wall-clock
                                     minute (429s clear exactly at :00, verified twice)
  x-ratelimit-limit-tokens:  120000
  no Retry-After / no reset header; the 429 body is plain text
  GET /models is NOT rate limited

The gate counts requests inside the current wall-clock minute: the 16th request
waits for the next minute boundary. Server counters re-sync us when a response
exposes them, and a 429 parks the rest of the window.
"""
import threading
import time

MAX_PER_WINDOW = 15
WINDOW_SEC = 60
SETTLE_MARGIN = 0.7  # allow for clock skew between us and the server

_lock = threading.Lock()
_state = {"window": None, "count": 0}


def _wall_window(now=None):
    """Start timestamp of the wall-clock minute *now* falls in (matches the server's window)."""
    return int((time.time() if now is None else now) // WINDOW_SEC) * WINDOW_SEC


def _targets_lightning(kwargs):
    provider = str(kwargs.get("provider") or "").lower()
    base_url = str(kwargs.get("base_url") or "").lower()
    return (
        provider in ("lightning", "lightning-ai", "lightningai")
        or "lightning" in provider
        or "lightning.ai" in base_url
    )


def _acquire():
    """Claim one slot in the current window, sleeping to the next window when it is full."""
    while True:
        with _lock:
            now = time.time()
            window = _wall_window(now)
            if _state["window"] != window:
                _state["window"] = window
                _state["count"] = 0
            if _state["count"] < MAX_PER_WINDOW:
                _state["count"] += 1
                return
            wait = (window + WINDOW_SEC) - now + SETTLE_MARGIN
        time.sleep(max(0.05, min(wait, WINDOW_SEC)))


def _headers_of(response):
    """Best-effort header mapping from whatever the provider call returned."""
    for attr in ("headers", "_headers"):
        headers = getattr(response, attr, None)
        if headers is not None and hasattr(headers, "get"):
            return headers
    for attr in ("_response", "http_response", "response", "raw_response"):
        inner = getattr(response, attr, None)
        headers = getattr(inner, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            return headers
    if isinstance(response, dict):
        return response.get("headers")
    return None


def _resync(response):
    """Adopt the server's own counter so our window matches it exactly."""
    headers = _headers_of(response)
    if not headers:
        return
    try:
        remaining = int(headers.get("x-ratelimit-remaining-requests"))
    except (TypeError, ValueError):
        return
    with _lock:
        _state["window"] = _wall_window()
        _state["count"] = max(0, MAX_PER_WINDOW - remaining)


def _park_window():
    """Mark the current window as spent (the server answered 429)."""
    with _lock:
        _state["window"] = _wall_window()
        _state["count"] = MAX_PER_WINDOW


def _throttle(request, next_call, **kw):
    if not _targets_lightning(kw):
        return next_call(request)
    _acquire()
    try:
        response = next_call(request)
    except Exception as exc:  # downstream failure propagates as itself (next_call is single-use)
        text = str(exc).lower()
        if "429" in text or "rate limit" in text:
            _park_window()
        raise
    _resync(response)
    return response


def register(ctx):
    try:
        ctx.register_middleware("llm_execution", _throttle)
    except Exception:
        pass
