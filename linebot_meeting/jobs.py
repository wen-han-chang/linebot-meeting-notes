"""限制程序內影音任務數量，避免單一 Render instance 過載。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class Admission:
    accepted: bool
    waiting: bool = False


@dataclass(frozen=True)
class JobStats:
    active: int
    queued: int
    capacity: int


class JobLimiter:
    def __init__(self, max_active: int, max_queued: int):
        self.max_active = max_active
        self.capacity = max_active + max_queued
        self._slots = asyncio.Semaphore(max_active)
        self._lock = asyncio.Lock()
        self._in_flight = 0
        self._active = 0

    async def reserve(self) -> Admission:
        async with self._lock:
            if self._in_flight >= self.capacity:
                return Admission(accepted=False)
            waiting = self._in_flight >= self.max_active
            self._in_flight += 1
            return Admission(accepted=True, waiting=waiting)

    async def cancel_reservation(self) -> None:
        async with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    @asynccontextmanager
    async def execution(self) -> AsyncIterator[None]:
        acquired = False
        try:
            await self._slots.acquire()
            acquired = True
            async with self._lock:
                self._active += 1
            yield
        finally:
            async with self._lock:
                if acquired:
                    self._active = max(0, self._active - 1)
                self._in_flight = max(0, self._in_flight - 1)
            if acquired:
                self._slots.release()

    async def stats(self) -> JobStats:
        async with self._lock:
            return JobStats(
                active=self._active,
                queued=max(0, self._in_flight - self._active),
                capacity=self.capacity,
            )
