"""单实例固定窗口限流器；按登录 IP 或 JWT 用户隔离。"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic


class FixedWindowRateLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60, now: float | None = None) -> tuple[bool, int]:
        current = monotonic() if now is None else now
        cutoff = current - window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, int(window_seconds - (current - hits[0]) + 0.999))
                return False, retry_after
            hits.append(current)
            if len(self._hits) > 2000:
                self._prune(cutoff)
            return True, 0

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            self._hits.pop(key, None)
