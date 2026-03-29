"""JobManager — async queue for yamd jobs.

Responsibilities:
- Accept job submissions from any interface (CLI, GUI, API).
- Run jobs concurrently up to a configurable limit.
- Relay progress updates and status changes to registered callbacks.
- Support cancellation of pending or running jobs.

This module has zero I/O of its own. The actual work (downloading,
converting) is injected as a coroutine factory at submission time,
keeping the manager fully decoupled from the backends.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from yamd.core.job import Job, JobStatus

logger = logging.getLogger(__name__)

# Type aliases
ProgressCallback = Callable[[Job], Awaitable[None] | None]
WorkerFactory = Callable[[Job], Awaitable[None]]


class JobNotFoundError(KeyError):
    """Raised when an operation targets a job id that does not exist."""


class JobManager:
    """Async manager for yamd jobs.

    Usage::

        manager = JobManager(max_concurrent=3)

        async def my_worker(job: Job) -> None:
            job.set_progress(0.5)
            await asyncio.sleep(1)   # real work here

        job = Job(type=JobType.DOWNLOAD, source="https://…", output_dir=…)
        await manager.submit(job, my_worker)
        await manager.wait_all()

    Args:
        max_concurrent: Maximum number of jobs that may run simultaneously.
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be ≥ 1, got {max_concurrent}.")

        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # id → Job
        self._jobs: dict[str, Job] = {}
        # id → running asyncio Task (only present while the job is RUNNING)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Callbacks notified on every status change or progress update
        self._callbacks: list[ProgressCallback] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def submit(self, job: Job, worker: WorkerFactory) -> None:
        """Register a job and schedule it for execution.

        The job must be in PENDING status. Execution begins as soon as a
        concurrency slot is available.

        Args:
            job:    The Job to run. Must have status PENDING.
            worker: Async callable that receives the job and performs the work.
                    It should call job.set_progress() periodically and raise on
                    failure — the manager handles state transitions.

        Raises:
            ValueError: If the job is not in PENDING status.
            KeyError:   If a job with the same id is already registered.
        """
        if not job.is_pending:
            raise ValueError(
                f"Only PENDING jobs can be submitted; "
                f"job {job.id!r} has status {job.status.value!r}."
            )
        if job.id in self._jobs:
            raise KeyError(f"Job {job.id!r} is already registered.")

        self._jobs[job.id] = job
        task = asyncio.create_task(
            self._run(job, worker), name=f"yamd-job-{job.id}"
        )
        self._tasks[job.id] = task
        logger.debug("Submitted job %s (%s)", job.id, job.type.value)

    async def cancel(self, job_id: str) -> None:
        """Cancel a pending or running job.

        If the job is already in a terminal state, this is a no-op.

        Args:
            job_id: The id of the job to cancel.

        Raises:
            JobNotFoundError: If no job with this id exists.
        """
        job = self._get(job_id)

        if job.is_terminal:
            logger.debug("Job %s is already terminal (%s), skip cancel.", job_id, job.status.value)
            return

        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass  # handled inside _run

        # If somehow the task finished before we cancelled, don't double-transition.
        if not job.is_terminal:
            job.transition(JobStatus.CANCELLED)
            await self._notify(job)

        logger.info("Cancelled job %s.", job_id)

    async def wait_all(self) -> None:
        """Await completion of all currently submitted jobs."""
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get(self, job_id: str) -> Job:
        """Return the Job with the given id.

        Raises:
            JobNotFoundError: If no job with this id exists.
        """
        return self._get(job_id)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
    ) -> list[Job]:
        """Return all registered jobs, optionally filtered by status.

        Args:
            status: If provided, return only jobs with this status.

        Returns:
            List of matching jobs ordered by creation time (oldest first).
        """
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at)
        if status is not None:
            jobs = [j for j in jobs if j.status is status]
        return jobs

    def add_callback(self, callback: ProgressCallback) -> None:
        """Register a callback invoked on every job update.

        The callback receives the updated Job. It may be a plain function or
        an async coroutine function — both are supported.

        Args:
            callback: Callable[[Job], Awaitable[None] | None]
        """
        self._callbacks.append(callback)

    def remove_callback(self, callback: ProgressCallback) -> None:
        """Unregister a previously added callback.

        Args:
            callback: The exact callable that was passed to add_callback.
        """
        self._callbacks.remove(callback)

    @property
    def max_concurrent(self) -> int:
        """The concurrency limit this manager was created with."""
        return self._max_concurrent

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(f"No job found with id {job_id!r}.") from None

    async def _run(self, job: Job, worker: WorkerFactory) -> None:
        """Acquire a concurrency slot, run the worker, handle transitions."""
        async with self._semaphore:
            try:
                job.transition(JobStatus.RUNNING)
                await self._notify(job)
                logger.info("Started job %s.", job.id)

                await worker(job)

                if not job.is_terminal:
                    job.transition(JobStatus.DONE)
                    job.set_progress(1.0) if not job.is_terminal else None
                    await self._notify(job)
                    logger.info("Completed job %s.", job.id)

            except asyncio.CancelledError:
                if not job.is_terminal:
                    job.transition(JobStatus.CANCELLED)
                    await self._notify(job)
                logger.info("Job %s was cancelled.", job.id)
                raise  # let asyncio clean up the task properly

            except Exception as exc:
                if not job.is_terminal:
                    job.error = str(exc)
                    job.transition(JobStatus.FAILED)
                    await self._notify(job)
                logger.exception("Job %s failed: %s", job.id, exc)

            finally:
                self._tasks.pop(job.id, None)

    async def _notify(self, job: Job) -> None:
        """Invoke all registered callbacks with the updated job."""
        for callback in self._callbacks:
            try:
                result = callback(job)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Callback %r raised an exception.", callback)