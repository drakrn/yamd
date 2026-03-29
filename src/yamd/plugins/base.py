"""Plugin base classes for yamd.

Every backend integration implements one of the ABCs defined here.
The core never imports yt-dlp, ffmpeg, or gallery-dl directly —
it only speaks to these interfaces.

Plugin types
────────────
SourcePlugin  — fetch online media from a URL
FormatPlugin  — convert a local file to another format
OutputPlugin  — post-process a completed job (tag, rename, move, …)

Discovery
─────────
Plugins are discovered via Python entry points (see pyproject.toml).
Third-party packages can ship their own plugins without modifying yamd.

    [project.entry-points."yamd.plugins.sources"]
    myplugin = "mypkg.plugins:MySourcePlugin"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yamd.core.job import Job


# ── Plugin metadata ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PluginInfo:
    """Descriptive metadata about a plugin.

    Attributes:
        name:        Short machine-readable identifier (e.g. ``"ytdlp"``).
        display_name: Human-readable name shown in the UI (e.g. ``"yt-dlp"``).
        version:     Plugin version string.
        description: One-line description of what the plugin does.
        supported_extensions: File extensions this plugin can handle,
                              without leading dot (e.g. ``["mp4", "webm"]``).
                              Empty list means no restriction.
    """

    name: str
    display_name: str
    version: str
    description: str
    supported_extensions: list[str] = field(default_factory=list)


# ── Source plugin ─────────────────────────────────────────────────────────────

class SourcePlugin(ABC):
    """Fetch online media from a URL.

    Implementations wrap a specific downloader backend (yt-dlp,
    gallery-dl, …) and translate its output into yamd's Job model.

    Lifecycle inside a worker::

        plugin = YtDlpPlugin()
        if plugin.can_handle(job):
            await plugin.download(job)
    """

    @property
    @abstractmethod
    def info(self) -> PluginInfo:
        """Return static metadata about this plugin."""

    @abstractmethod
    def can_handle(self, job: Job) -> bool:
        """Return True if this plugin is able to process the given job.

        Implementations typically inspect the URL scheme or domain.
        This is called before ``download`` to select the right plugin.

        Args:
            job: The job whose source URL should be inspected.

        Returns:
            True if this plugin can download the job's source.
        """

    @abstractmethod
    async def download(self, job: Job) -> Path:
        """Download the media described by ``job``.

        The implementation must call ``job.set_progress()`` periodically
        so the manager and UI can track progress.

        Args:
            job: The job to execute. ``job.output_dir`` is the destination
                 directory. ``job.output_format`` may hint at the desired
                 container format (or be None for the source default).

        Returns:
            Path to the downloaded file.

        Raises:
            PluginError: On any download failure.
        """


# ── Format plugin ─────────────────────────────────────────────────────────────

class FormatPlugin(ABC):
    """Convert a local file from one format to another.

    Implementations wrap a transcoding backend (ffmpeg, …).

    Lifecycle inside a worker::

        plugin = FfmpegPlugin()
        if plugin.can_convert(src_ext, dst_ext):
            output = await plugin.convert(job, source_path)
    """

    @property
    @abstractmethod
    def info(self) -> PluginInfo:
        """Return static metadata about this plugin."""

    @abstractmethod
    def can_convert(self, source_ext: str, target_ext: str) -> bool:
        """Return True if this plugin can transcode between the given formats.

        Args:
            source_ext: Source file extension without dot (e.g. ``"mp4"``).
            target_ext: Target file extension without dot (e.g. ``"mp3"``).

        Returns:
            True if the conversion is supported.
        """

    @abstractmethod
    async def convert(self, job: Job, source_path: Path) -> Path:
        """Convert ``source_path`` to ``job.output_format``.

        The implementation must call ``job.set_progress()`` periodically.

        Args:
            job:         The job driving this conversion. ``job.output_dir``
                         is where the output file should be written.
                         ``job.output_format`` is the target extension.
            source_path: Absolute path to the source file.

        Returns:
            Path to the converted output file.

        Raises:
            PluginError: On any conversion failure.
            ValueError:  If ``job.output_format`` is None.
        """


# ── Output plugin ─────────────────────────────────────────────────────────────

class OutputPlugin(ABC):
    """Post-process a file after a job completes.

    Examples: write ID3 tags, rename according to a template, move
    to a final destination directory.

    Lifecycle inside a worker::

        for plugin in output_plugins:
            result_path = await plugin.process(job, result_path)
    """

    @property
    @abstractmethod
    def info(self) -> PluginInfo:
        """Return static metadata about this plugin."""

    @abstractmethod
    async def process(self, job: Job, result_path: Path) -> Path:
        """Post-process the file at ``result_path``.

        Output plugins form a pipeline — each one receives the path
        returned by the previous one. The final path after all plugins
        is the canonical output location reported to the user.

        Args:
            job:         The completed job providing metadata.
            result_path: Path to the file to post-process.

        Returns:
            Path to the (possibly renamed / moved) output file.

        Raises:
            PluginError: On any post-processing failure.
        """


# ── Exceptions ────────────────────────────────────────────────────────────────

class PluginError(Exception):
    """Raised by any plugin when it cannot complete its operation.

    Attributes:
        plugin_name: The ``PluginInfo.name`` of the failing plugin.
        message:     Human-readable description of the failure.
        cause:       The original exception, if any.
    """

    def __init__(
        self,
        plugin_name: str,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(f"[{plugin_name}] {message}")
        self.plugin_name = plugin_name
        self.message = message
        self.cause = cause

    def __repr__(self) -> str:
        return (
            f"PluginError(plugin={self.plugin_name!r}, "
            f"message={self.message!r}, cause={self.cause!r})"
        )