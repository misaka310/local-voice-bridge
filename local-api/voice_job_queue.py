from __future__ import annotations

import time
from collections import deque
from threading import Condition
from typing import Callable, Deque, Generic, TypeVar


T = TypeVar("T")


class VoiceJobQueueClosed(RuntimeError):
    pass


class VoiceJobQueueFull(RuntimeError):
    pass


class VoiceJobQueue(Generic[T]):
    """Small cancel-aware FIFO with explicit invalidation and shutdown semantics."""

    def __init__(self, maxsize: int = 256) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = int(maxsize)
        self._items: Deque[T] = deque()
        self._condition = Condition()
        self._closed = False
        self._wake_serial = 0

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def enqueue(self, item: T, *, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while len(self._items) >= self._maxsize:
                if self._closed:
                    raise VoiceJobQueueClosed("voice job queue is closed")
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VoiceJobQueueFull("voice job queue is full")
                self._condition.wait(remaining)
            if self._closed:
                raise VoiceJobQueueClosed("voice job queue is closed")
            self._items.append(item)
            self._condition.notify_all()

    def take(self, *, timeout: float | None = None) -> T | None:
        """Return the next item, None on timeout/wake, or raise after a drained close."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            observed_wake = self._wake_serial
            while not self._items:
                if self._closed:
                    raise VoiceJobQueueClosed("voice job queue is closed")
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(remaining)
                if observed_wake != self._wake_serial and not self._items:
                    return None
            item = self._items.popleft()
            self._condition.notify_all()
            return item

    def invalidate(self, predicate: Callable[[T], bool]) -> list[T]:
        removed: list[T] = []
        with self._condition:
            kept: Deque[T] = deque()
            while self._items:
                item = self._items.popleft()
                if predicate(item):
                    removed.append(item)
                else:
                    kept.append(item)
            self._items = kept
            if removed:
                self._condition.notify_all()
        return removed

    def clear(self) -> list[T]:
        with self._condition:
            removed = list(self._items)
            self._items.clear()
            self._condition.notify_all()
            return removed

    def wake(self) -> None:
        with self._condition:
            self._wake_serial += 1
            self._condition.notify_all()

    def close(self, *, discard: bool = True) -> list[T]:
        with self._condition:
            if self._closed:
                return []
            self._closed = True
            removed = list(self._items) if discard else []
            if discard:
                self._items.clear()
            self._wake_serial += 1
            self._condition.notify_all()
            return removed
