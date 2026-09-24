from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable
from urllib.request import urlopen as _stdlib_urlopen


class SlidingWindowRateLimiter:
    """Thread-safe process-wide sliding-window request limiter."""

    def __init__(
        self,
        max_calls: int = 5,
        window_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_calls < 1 or window_seconds <= 0:
            raise ValueError("max_calls and window_seconds must be positive")
        self.max_calls = int(max_calls)
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._sleep = sleeper
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                delay = self.window_seconds - (now - self._calls[0])
            # Sleep outside the lock so other waiting threads can re-check the
            # same shared window without serializing network response time.
            self._sleep(max(0.001, delay))


GLOBAL_API_RATE_LIMITER = SlidingWindowRateLimiter(max_calls=5, window_seconds=1.0)


def rate_limited_urlopen(*args: Any, **kwargs: Any):
    """Open a URL after entering the application's shared 5 req/s window."""

    GLOBAL_API_RATE_LIMITER.acquire()
    return _stdlib_urlopen(*args, **kwargs)
