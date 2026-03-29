"""Tests for core/job_manager.py."""

import asyncio
from pathlib import Path

import pytest

from yamd.core.job import Job, JobStatus, JobType
from yamd.core.job_manager import JobManager, JobNotFoundError


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_job(tmp_path: Path, source: str = "https://example.com") -> Job:
    return Job(type=JobType.DOWNLOAD, source=source, output_dir=tmp_path)


async def instant_worker(job: Job) -> None:
    """Worker that completes immediately."""
    job.set_progress(1.0)


async def slow_worker(job: Job) -> None:
    """Worker that takes a little time."""
    job.set_progress(0.5)
    await asyncio.sleep(0.05)
    job.set_progress(1.0)


async def failing_worker(job: Job) -> None:
    """Worker that always raises."""
    raise RuntimeError("something went wrong")


async def blocking_worker(job: Job) -> None:
    """Worker that blocks until cancelled."""
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        raise


# ── Construction ──────────────────────────────────────────────────────────────

def test_invalid_max_concurrent_raises() -> None:
    with pytest.raises(ValueError, match="max_concurrent"):
        JobManager(max_concurrent=0)


def test_max_concurrent_property() -> None:
    manager = JobManager(max_concurrent=5)
    assert manager.max_concurrent == 5


# ── Submit ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_runs_job_to_done(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert job.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_submit_non_pending_raises(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    job.transition(JobStatus.CANCELLED)
    with pytest.raises(ValueError, match="PENDING"):
        await manager.submit(job, instant_worker)


@pytest.mark.asyncio
async def test_submit_duplicate_id_raises(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, slow_worker)
    with pytest.raises(KeyError):
        await manager.submit(job, slow_worker)
    await manager.wait_all()


# ── Progress & timestamps ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_progress_reaches_one(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_started_at_and_finished_at_set(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.finished_at >= job.started_at


# ── Failure ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failing_worker_marks_job_failed(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, failing_worker)
    await manager.wait_all()
    assert job.status is JobStatus.FAILED
    assert job.error == "something went wrong"


# ── Cancellation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_running_job(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, blocking_worker)
    await asyncio.sleep(0.02)  # let the job start
    await manager.cancel(job.id)
    assert job.status is JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_pending_job(tmp_path: Path) -> None:
    """A job queued behind the semaphore can be cancelled before it starts."""
    manager = JobManager(max_concurrent=1)

    blocker = make_job(tmp_path, source="https://blocker.com")
    waiter = make_job(tmp_path, source="https://waiter.com")

    await manager.submit(blocker, blocking_worker)
    await manager.submit(waiter, instant_worker)

    await asyncio.sleep(0.02)  # blocker is RUNNING, waiter is still PENDING
    await manager.cancel(waiter.id)
    await manager.cancel(blocker.id)
    await manager.wait_all()

    assert waiter.status is JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_terminal_job_is_noop(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert job.status is JobStatus.DONE
    await manager.cancel(job.id)          # should not raise or change status
    assert job.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_cancel_unknown_id_raises(tmp_path: Path) -> None:
    manager = JobManager()
    with pytest.raises(JobNotFoundError):
        await manager.cancel("nonexistent-id")


# ── Concurrency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_limit_is_respected(tmp_path: Path) -> None:
    """At most max_concurrent jobs should be RUNNING at the same time."""
    max_concurrent = 2
    manager = JobManager(max_concurrent=max_concurrent)
    running_peak = 0
    currently_running = 0

    async def counting_worker(job: Job) -> None:
        nonlocal running_peak, currently_running
        currently_running += 1
        running_peak = max(running_peak, currently_running)
        await asyncio.sleep(0.05)
        currently_running -= 1

    jobs = [make_job(tmp_path, source=f"https://example.com/{i}") for i in range(5)]
    for job in jobs:
        await manager.submit(job, counting_worker)

    await manager.wait_all()
    assert running_peak <= max_concurrent


# ── Listing ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_jobs_returns_all(tmp_path: Path) -> None:
    manager = JobManager()
    jobs = [make_job(tmp_path, source=f"https://example.com/{i}") for i in range(3)]
    for job in jobs:
        await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert len(manager.list_jobs()) == 3


@pytest.mark.asyncio
async def test_list_jobs_filtered_by_status(tmp_path: Path) -> None:
    manager = JobManager()
    good = make_job(tmp_path, source="https://good.com")
    bad  = make_job(tmp_path, source="https://bad.com")
    await manager.submit(good, instant_worker)
    await manager.submit(bad, failing_worker)
    await manager.wait_all()
    assert manager.list_jobs(status=JobStatus.DONE) == [good]
    assert manager.list_jobs(status=JobStatus.FAILED) == [bad]


# ── Callbacks ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_callback_is_called(tmp_path: Path) -> None:
    manager = JobManager()
    seen_statuses: list[str] = []

    def on_update(job: Job) -> None:
        seen_statuses.append(job.status.value)

    manager.add_callback(on_update)
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()

    assert "running" in seen_statuses
    assert "done" in seen_statuses


@pytest.mark.asyncio
async def test_async_callback_is_called(tmp_path: Path) -> None:
    manager = JobManager()
    seen_statuses: list[str] = []

    async def on_update(job: Job) -> None:
        seen_statuses.append(job.status.value)

    manager.add_callback(on_update)
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()

    assert "running" in seen_statuses
    assert "done" in seen_statuses


@pytest.mark.asyncio
async def test_remove_callback(tmp_path: Path) -> None:
    manager = JobManager()
    calls: list[int] = []

    def on_update(job: Job) -> None:
        calls.append(1)

    manager.add_callback(on_update)
    manager.remove_callback(on_update)

    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()

    assert calls == []


@pytest.mark.asyncio
async def test_crashing_callback_does_not_stop_job(tmp_path: Path) -> None:
    manager = JobManager()

    def bad_callback(job: Job) -> None:
        raise RuntimeError("callback exploded")

    manager.add_callback(bad_callback)
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()

    assert job.status is JobStatus.DONE


# ── get() ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_existing_job(tmp_path: Path) -> None:
    manager = JobManager()
    job = make_job(tmp_path)
    await manager.submit(job, instant_worker)
    await manager.wait_all()
    assert manager.get(job.id) is job


def test_get_nonexistent_job_raises() -> None:
    manager = JobManager()
    with pytest.raises(JobNotFoundError):
        manager.get("ghost-id")