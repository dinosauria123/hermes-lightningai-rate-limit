"""Lightning AI rate limiter — per-process token bucket (15 req/min)."""
import time
import threading

_MAX_PER_WINDOW = 15
_WINDOW_SEC = 60.0
_SAFETY_MARGIN = 0.1

_lock = threading.Lock()
_slots = []


def _targets_lightning(kwargs):
    provider = str(kwargs.get("provider") or "").lower()
    base_url = str(kwargs.get("base_url") or "").lower()
    return provider in ("lightning", "lightning-ai", "lightningai") or \
        "lightning.ai" in base_url or "lightning" in provider


def _wait_for_slot():
    while True:
        with _lock:
            now = time.monotonic()
            cutoff = now - _WINDOW_SEC
            # keep only timestamps within the current window
            recent = [s for s in _slots if s > cutoff]
            if len(recent) < _MAX_PER_WINDOW:
                _slots[:] = recent + [now]
                return
            oldest = recent[0]
            wait = _WINDOW_SEC - (now - oldest) + _SAFETY_MARGIN
        time.sleep(max(0.05, min(wait, _WINDOW_SEC)))


def _throttle(request, next_call, **kw):
    if not _targets_lightning(kw):
        return next_call(request)
    _wait_for_slot()
    return next_call(request)


def register(ctx):
    try:
        ctx.register_middleware("llm_execution", _throttle)
    except Exception:
        pass
