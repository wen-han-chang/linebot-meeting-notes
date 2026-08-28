import asyncio
from contextlib import suppress

from linebot_meeting.jobs import JobLimiter


async def test_job_limiter_bounds_active_and_queued_jobs() -> None:
    limiter = JobLimiter(max_active=1, max_queued=1)

    first = await limiter.reserve()
    second = await limiter.reserve()
    rejected = await limiter.reserve()

    assert first.accepted and not first.waiting
    assert second.accepted and second.waiting
    assert not rejected.accepted

    started = asyncio.Event()
    release = asyncio.Event()

    async def run_first() -> None:
        async with limiter.execution():
            started.set()
            await release.wait()

    task = asyncio.create_task(run_first())
    await started.wait()
    stats = await limiter.stats()
    assert stats.active == 1
    assert stats.queued == 1

    release.set()
    await task
    await limiter.cancel_reservation()
    stats = await limiter.stats()
    assert stats.active == 0
    assert stats.queued == 0


async def test_cancelled_waiting_job_releases_reservation() -> None:
    limiter = JobLimiter(max_active=1, max_queued=1)
    await limiter.reserve()
    await limiter.reserve()

    hold = asyncio.Event()

    async def active_job() -> None:
        async with limiter.execution():
            await hold.wait()

    active = asyncio.create_task(active_job())
    await asyncio.sleep(0)
    waiting = asyncio.create_task(_run_job(limiter))
    await asyncio.sleep(0)
    waiting.cancel()
    with suppress(asyncio.CancelledError):
        await waiting

    hold.set()
    await active
    stats = await limiter.stats()
    assert stats.active == 0
    assert stats.queued == 0


async def _run_job(limiter: JobLimiter) -> None:
    async with limiter.execution():
        pass
