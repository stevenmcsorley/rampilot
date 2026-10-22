"""Small, bounded time-series buffer used by the history graph."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    time: float
    value: float


class HistoryBuffer:
    """A fixed-duration, hard-capped history so memory use cannot grow forever."""

    def __init__(self, duration: float = 60.0, max_points: int = 180):
        if duration <= 0:
            raise ValueError("duration must be positive")
        self.duration = duration
        self.max_points = max(2, max_points)
        self._points: deque[HistoryPoint] = deque(maxlen=self.max_points)

    def add(self, value: float, timestamp: float | None = None) -> None:
        ts = time.monotonic() if timestamp is None else timestamp
        # Keep timestamps strictly increasing even if the clock is adjusted or a
        # caller supplies a duplicate sample time.
        if self._points and ts <= self._points[-1].time:
            ts = self._points[-1].time + 1e-6
        self._points.append(HistoryPoint(ts, max(0.0, min(1.0, float(value)))))
        self.prune(ts)

    def prune(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - self.duration
        while self._points and self._points[0].time < cutoff:
            self._points.popleft()

    def clear(self) -> None:
        self._points.clear()

    @property
    def points(self) -> tuple[HistoryPoint, ...]:
        return tuple(self._points)

    def __len__(self) -> int:
        return len(self._points)

    @property
    def latest(self) -> HistoryPoint | None:
        return self._points[-1] if self._points else None
