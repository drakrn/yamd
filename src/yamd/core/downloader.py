"""Downloader — orchestrates SourcePlugins and the JobManager.

The Downloader is the entry point for all download operations.
It selects the right SourcePlugin for a given job and submits a
worker coroutine to the JobManager.

Usage::

    manager    = JobManager(max_concurrent=3)
    downloader = Downloader(manager, plugins=[YtDlpPlugin()])

    job = Job(type=JobType.DOWNLOAD, source="https://…", output_dir=…)
    await downloader.download(job)
    await manager.wait_all()
"""

from __future__ import annotations

import logging
from pathlib import Path

from yamd.core.job import Job, JobType
from yamd.core.job_manager import JobManager
from yamd.plugins.base import PluginError, SourcePlugin

logger = logging.getLogger(__name__)


class NoPluginError(Exception):
    """Raised when no registered SourcePlugin can handle a given job."""

    def __init__(self, source: str) -> None:
        super().__init__(
            f"No source plugin can handle {source!r}. "
            "Make sure the appropriate plugin is installed and registered."
        )
        self.source = source


class Downloader:
    """Selects the right SourcePlugin and submits download jobs.

    Args:
        manager: The JobManager that will schedule and track the job.
        plugins: Ordered list of SourcePlugin instances to try.
                 The first plugin whose ``can_handle()`` returns True is used.
    """

    def __init__(
        self,
        manager: JobManager,
        plugins: list[SourcePlugin],
    ) -> None:
        if not plugins:
            raise ValueError("At least one SourcePlugin must be provided.")
        self._manager = manager
        self._plugins = plugins

    # ── Public API ────────────────────────────────────────────────────────────

    async def download(self, job: Job) -> None:
        """Select a plugin and submit the job to the JobManager.

        The job transitions to RUNNING inside the manager's worker.
        This method returns as soon as the job is queued — it does not
        wait for the download to complete. Use ``manager.wait_all()``
        or register a callback to know when it finishes.

        Args:
            job: A PENDING Job with type DOWNLOAD.

        Raises:
            ValueError:    If the job type is not DOWNLOAD.
            NoPluginError: If no registered plugin can handle the URL.
        """
        if job.type is not JobType.DOWNLOAD:
            raise ValueError(
                f"Downloader only handles DOWNLOAD jobs; "
                f"got {job.type.value!r} for job {job.id!r}."
            )

        plugin = self._select_plugin(job)
        logger.info(
            "Job %s: selected plugin %r for %r.",
            job.id, plugin.info.name, job.source,
        )

        async def worker(j: Job) -> None:
            output_path = await plugin.download(j)
            j.metadata["output_path"] = str(output_path)
            logger.info("Job %s: downloaded to %s.", j.id, output_path)

        await self._manager.submit(job, worker)

    def resolve_plugin(self, job: Job) -> SourcePlugin:
        """Return the plugin that would be selected for this job.

        Useful for introspection (e.g. showing the user which backend
        will be used before starting the download).

        Raises:
            NoPluginError: If no plugin matches.
        """
        return self._select_plugin(job)

    @property
    def plugins(self) -> list[SourcePlugin]:
        """The list of registered SourcePlugins, in priority order."""
        return list(self._plugins)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _select_plugin(self, job: Job) -> SourcePlugin:
        """Return the first plugin that can handle the job's source URL.

        Raises:
            NoPluginError: If no plugin matches.
        """
        for plugin in self._plugins:
            try:
                if plugin.can_handle(job):
                    return plugin
            except Exception:
                logger.exception(
                    "Plugin %r raised while checking can_handle for %r.",
                    plugin.info.name, job.source,
                )
        raise NoPluginError(job.source)