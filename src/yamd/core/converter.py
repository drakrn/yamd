"""Converter — orchestrates FormatPlugins and the JobManager.

The Converter is the entry point for all local file conversion operations.
It selects the right FormatPlugin for a given job and submits a worker
coroutine to the JobManager.

Usage::

    manager   = JobManager(max_concurrent=3)
    converter = Converter(manager, plugins=[FfmpegPlugin()])

    job = Job(
        type=JobType.CONVERT,
        source="/path/to/input.mp4",
        output_dir=Path("~/Downloads/yamd"),
        output_format="mp3",
    )
    await converter.convert(job)
    await manager.wait_all()
"""

from __future__ import annotations

import logging
from pathlib import Path

from yamd.core.job import Job, JobType
from yamd.core.job_manager import JobManager
from yamd.plugins.base import FormatPlugin

logger = logging.getLogger(__name__)


class NoFormatPluginError(Exception):
    """Raised when no registered FormatPlugin can handle a conversion pair.

    Attributes:
        source_ext: The source file extension (without dot).
        target_ext: The requested target extension (without dot).
    """

    def __init__(self, source_ext: str, target_ext: str) -> None:
        super().__init__(
            f"No format plugin can convert {source_ext!r} → {target_ext!r}. "
            "Make sure the appropriate plugin is installed and registered."
        )
        self.source_ext = source_ext
        self.target_ext = target_ext


class MissingOutputFormatError(ValueError):
    """Raised when a CONVERT job has no output_format set."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"Job {job_id!r} has no output_format. "
            "A target format (e.g. 'mp3', 'mp4') is required for conversion."
        )


class Converter:
    """Selects the right FormatPlugin and submits conversion jobs.

    Args:
        manager: The JobManager that will schedule and track the job.
        plugins: Ordered list of FormatPlugin instances to try.
                 The first plugin whose ``can_convert()`` returns True is used.
    """

    def __init__(
        self,
        manager: JobManager,
        plugins: list[FormatPlugin],
    ) -> None:
        if not plugins:
            raise ValueError("At least one FormatPlugin must be provided.")
        self._manager = manager
        self._plugins = plugins

    # ── Public API ────────────────────────────────────────────────────────────

    async def convert(self, job: Job) -> None:
        """Select a plugin and submit the conversion job to the JobManager.

        Returns immediately after queuing — does not wait for completion.
        Use ``manager.wait_all()`` or register a callback to react when done.

        Args:
            job: A PENDING Job with type CONVERT. Must have ``output_format``
                 and a valid file path in ``source``.

        Raises:
            ValueError:           If the job type is not CONVERT.
            MissingOutputFormatError: If ``job.output_format`` is None.
            NoFormatPluginError:  If no registered plugin supports the
                                  source → target format pair.
        """
        if job.type is not JobType.CONVERT:
            raise ValueError(
                f"Converter only handles CONVERT jobs; "
                f"got {job.type.value!r} for job {job.id!r}."
            )

        if not job.output_format:
            raise MissingOutputFormatError(job.id)

        source_path = Path(job.source)
        source_ext  = source_path.suffix.lstrip(".").lower()
        target_ext  = job.output_format.lower()

        plugin = self._select_plugin(source_ext, target_ext)
        logger.info(
            "Job %s: selected plugin %r for %r → %r.",
            job.id, plugin.info.name, source_ext, target_ext,
        )

        async def worker(j: Job) -> None:
            output_path = await plugin.convert(j, source_path)
            j.metadata["output_path"] = str(output_path)
            j.metadata["source_ext"]  = source_ext
            j.metadata["target_ext"]  = target_ext
            logger.info("Job %s: converted to %s.", j.id, output_path)

        await self._manager.submit(job, worker)

    def resolve_plugin(self, source_ext: str, target_ext: str) -> FormatPlugin:
        """Return the plugin that would be selected for this format pair.

        Useful for introspection before starting a job (e.g. to inform the
        user which backend will be used).

        Args:
            source_ext: Source extension without dot (e.g. ``"mp4"``).
            target_ext: Target extension without dot (e.g. ``"mp3"``).

        Raises:
            NoFormatPluginError: If no plugin matches.
        """
        return self._select_plugin(source_ext, target_ext)

    @property
    def plugins(self) -> list[FormatPlugin]:
        """The list of registered FormatPlugins, in priority order."""
        return list(self._plugins)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _select_plugin(self, source_ext: str, target_ext: str) -> FormatPlugin:
        """Return the first plugin that supports the conversion pair.

        Raises:
            NoFormatPluginError: If no plugin matches.
        """
        for plugin in self._plugins:
            try:
                if plugin.can_convert(source_ext, target_ext):
                    return plugin
            except Exception:
                logger.exception(
                    "Plugin %r raised while checking can_convert(%r, %r).",
                    plugin.info.name, source_ext, target_ext,
                )
        raise NoFormatPluginError(source_ext, target_ext)