"""yt-dlp source plugin.

Uses yt-dlp as a Python library to download media from any URL that
yt-dlp supports (YouTube, Vimeo, SoundCloud, and thousands more).

Progress is relayed to the Job via yt-dlp's progress_hooks mechanism,
which fires on every downloaded chunk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

import yt_dlp
import yt_dlp.utils

from yamd.core.job import Job
from yamd.plugins.base import PluginError, PluginInfo, SourcePlugin

logger = logging.getLogger(__name__)

# yt-dlp progress hook status strings
_DOWNLOADING = "downloading"
_FINISHED = "finished"
_ERROR = "error"


class YtDlpPlugin(SourcePlugin):
    """Download media from any yt-dlp-supported URL.

    This plugin delegates entirely to the ``yt_dlp`` library.
    It never shells out to a subprocess.

    Args:
        extra_opts: Additional yt-dlp options merged on top of the defaults.
                    Useful for testing or advanced configuration.
    """

    def __init__(self, extra_opts: dict[str, Any] | None = None) -> None:
        self._extra_opts = extra_opts or {}

    # ── SourcePlugin interface ────────────────────────────────────────────────

    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="ytdlp",
            display_name="yt-dlp",
            version=yt_dlp.version.__version__,
            description="Download media from YouTube, Vimeo, SoundCloud, and thousands more.",
            supported_extensions=["mp4", "webm", "mp3", "m4a", "ogg", "flv", "mkv"],
        )

    def can_handle(self, job: Job) -> bool:
        """Return True if yt-dlp has an extractor for this URL.

        We ask yt-dlp itself rather than maintaining a list of domains —
        this means we automatically support any site yt-dlp adds in future.

        Args:
            job: The job whose source URL is inspected.

        Returns:
            True if a yt-dlp extractor matched the URL.
        """
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                extractors = ydl._ies  # internal extractor registry
                for ie in extractors.values():
                    with contextlib.suppress(Exception):
                        if ie.suitable(job.source) and ie.working():
                            return True
        except Exception:
            pass
        return False

    async def download(self, job: Job) -> Path:
        """Download the URL in ``job.source`` using yt-dlp.

        Runs the blocking yt-dlp call in a thread pool executor so it
        does not block the asyncio event loop.

        Args:
            job: The job to execute.

        Returns:
            Path to the downloaded file.

        Raises:
            PluginError: On any yt-dlp download failure.
        """
        loop = asyncio.get_running_loop()
        try:
            result_path = await loop.run_in_executor(
                None, self._download_sync, job
            )
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError(
                self.info.name,
                f"Unexpected error downloading {job.source!r}: {exc}",
                cause=exc,
            ) from exc

        return result_path

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_opts(self, job: Job) -> dict[str, Any]:
        """Build the yt-dlp options dict for this job."""
        # Output template: <output_dir>/<title>.<ext>
        outtmpl = str(job.output_dir / "%(title)s.%(ext)s")

        opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            # Merge audio+video into a single file when separate streams exist
            "merge_output_format": job.output_format or "mp4",
            "progress_hooks": [self._make_progress_hook(job)],
            "postprocessor_hooks": [],
        }

        # If a specific output format is requested, add a postprocessor
        if job.output_format:
            opts["postprocessors"] = [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": job.output_format,
                }
            ]

        opts.update(self._extra_opts)
        return opts

    def _make_progress_hook(self, job: Job):
        """Return a yt-dlp progress hook that updates job.set_progress()."""

        def hook(d: dict[str, Any]) -> None:
            status = d.get("status")

            if status == _DOWNLOADING:
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total > 0:
                    # Reserve the last 5% for post-processing
                    ratio = min(downloaded / total, 1.0) * 0.95
                    with contextlib.suppress(RuntimeError):
                        job.set_progress(ratio)

            elif status == _FINISHED:
                with contextlib.suppress(RuntimeError):
                    job.set_progress(0.95)  # post-processing starts

            elif status == _ERROR:
                logger.warning("yt-dlp reported an error hook for job %s.", job.id)

        return hook

    def _download_sync(self, job: Job) -> Path:
        """Blocking download — must be called from a thread, not the event loop."""
        opts = self._build_opts(job)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job.source, download=True)

        except yt_dlp.utils.DownloadError as exc:
            raise PluginError(
                self.info.name,
                f"Download failed for {job.source!r}: {exc}",
                cause=exc,
            ) from exc

        except yt_dlp.utils.ExtractorError as exc:
            raise PluginError(
                self.info.name,
                f"Could not extract info from {job.source!r}: {exc}",
                cause=exc,
            ) from exc

        if info is None:
            raise PluginError(
                self.info.name,
                f"yt-dlp returned no info for {job.source!r}.",
            )

        # Populate job metadata from what yt-dlp extracted
        job.metadata.update({
            "title":     info.get("title", ""),
            "uploader":  info.get("uploader", ""),
            "duration":  info.get("duration"),
            "webpage_url": info.get("webpage_url", job.source),
            "ext":       info.get("ext", ""),
        })

        # Resolve the actual output path from yt-dlp's info dict
        filename = ydl.prepare_filename(info)
        output_path = Path(filename)

        # If a format conversion happened, the extension changed
        if job.output_format:
            output_path = output_path.with_suffix(f".{job.output_format}")

        logger.debug("yt-dlp wrote file: %s", output_path)
        return output_path