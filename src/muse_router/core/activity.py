"""In-memory activity log for the dashboard.

Only request metadata is kept: model, account id, timing and token counts.
No prompt content, no cookies and no API keys are ever recorded here.
"""

import threading
from collections import deque
from datetime import datetime, timezone


def _truncate(text, limit=200):
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


class ActivityLog:
    """Thread-safe, bounded log of recent gateway requests."""

    def __init__(self, maxlen: int = 200):
        self._events: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, kind, model=None, account_id=None, status="success",
               duration_ms=0, ttft_ms=None, prompt_tokens=0,
               completion_tokens=0, error=None):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "model": model,
            "account_id": account_id,
            "status": status,
            "duration_ms": int(duration_ms),
            "ttft_ms": int(ttft_ms) if ttft_ms is not None else None,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "error": _truncate(error),
        }
        with self._lock:
            self._events.append(event)
        return event

    def recent(self, limit: int = 100):
        limit = max(1, min(int(limit or 100), 200))
        with self._lock:
            events = list(self._events)
        # Newest first, so the console log shows the latest request on top.
        return list(reversed(events))[:limit]

    def clear(self):
        with self._lock:
            self._events.clear()

    def summary(self):
        with self._lock:
            events = list(self._events)
        total = len(events)
        success = sum(1 for e in events if e["status"] == "success")
        by_model: dict = {}
        by_account: dict = {}
        for e in events:
            model_key = e["model"] or "unknown"
            entry = by_model.setdefault(model_key, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
            entry["requests"] += 1
            entry["prompt_tokens"] += e["prompt_tokens"]
            entry["completion_tokens"] += e["completion_tokens"]
            acc_key = e["account_id"] or "unknown"
            aentry = by_account.setdefault(acc_key, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
            aentry["requests"] += 1
            aentry["prompt_tokens"] += e["prompt_tokens"]
            aentry["completion_tokens"] += e["completion_tokens"]
        return {
            "total_requests": total,
            "success": success,
            "failed": total - success,
            "total_prompt_tokens": sum(e["prompt_tokens"] for e in events),
            "total_completion_tokens": sum(e["completion_tokens"] for e in events),
            "by_model": by_model,
            "by_account": by_account,
        }


# Fallback singleton, used only when a route runs without app.state.activity
# (create_app always sets app.state.activity, so tests and the real server
# never hit this path; it exists so a stray route cannot crash on AttributeError).
default_activity = ActivityLog()


def activity_from_app(app):
    activity = getattr(app.state, "activity", None)
    return activity if activity is not None else default_activity
