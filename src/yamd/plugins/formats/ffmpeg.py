"""ffmpeg format plugin.

Converts local media files between formats using ffmpeg as a subprocess.
Progress is read from ffmpeg's machine-readable ``-progress pipe:1``
output, which emits ``key=value`` lines at regular intervals.

Flow
────
1. Probe the source file with ffprobe to get total duration (µs).
2. Build the ffmpeg command with ``-progress pipe:1 -nostats``.
3. Run it as an asyncio subprocess, reading stdout line by line.
4. Parse ``out_time_us=`` to compute progress ratio.
5. Detect ``progress=end`` to confirm clean completion.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from yamd.core.job import Job
from yamd.plugins.base import FormatPlugin, PluginError, PluginInfo

logger = logging.getLogger(__name__)

# Microseconds → seconds
_US_PER_SEC = 1_000_000

# Format pairs this plugin supports: source_ext → set of target_exts.
# Extend this table as needed — ffmpeg supports far more than listed here.
_SUPPORTED: dict[str, set[str]] = {
    "mp4":  {"mp3", "aac", "wav", "flac", "ogg", "mkv", "webm", "avi", "mov"},
    "mkv":  {"mp4", "mp3", "aac", "wav", "flac", "webm", "avi"},
    "webm": {"mp4", "mp3", "ogg", "mkv"},
    "avi":  {"mp4", "mkv", "mp3", "wav"},
    "mov":  {"mp4", "mkv", "mp3", "wav", "aac"},
    "flv":  {"mp4", "mp3", "aac"},
    "mp3":  {"wav", "flac", "ogg", "aac", "mp4"},
    "wav":  {"mp3", "flac", "ogg", "aac", "mp4"},
    "flac": {"mp3", "wav", "ogg", "aac"},
    "ogg":  {"mp3", "wav", "flac"},
    "aac":  {"mp3", "wav", "flac", "ogg"},
    "m4a":  {"mp3", "wav", "flac", "ogg", "aac"},
}


class FfmpegPlugin(FormatPlugin):
    """Convert local media files using ffmpeg.

    Args:
        ffmpeg_bin:  Path or name of the ffmpeg executable.
        ffprobe_bin: Path or name of the ffprobe executable.
        extra_args:  Additional ffmpeg arguments inserted before the output
                     path. Useful for codec selection, bitrate, etc.
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        extra_args: list[str] | None = None,
    ) -> None:
        self._ffmpeg_bin  = ffmpeg_bin
        self._ffprobe_bin = ffprobe_bin
        self._extra_args  = extra_args or []

    # ── FormatPlugin interface ────────────────────────────────────────────────

    @property
    def info(self) -> PluginInfo:
        source_exts = list(_SUPPORTED.keys())
        return PluginInfo(
            name="ffmpeg",
            display_name="ffmpeg",
            version="n/a",  # queried at runtime if needed
            description="Convert media files between formats using ffmpeg.",
            supported_extensions=source_exts,
        )

    def can_convert(self, source_ext: str, target_ext: str) -> bool:
        """Return True if ffmpeg can convert source_ext to target_ext.

        Looks up the ``_SUPPORTED`` table. Both extensions must be lowercase
        and without a leading dot (the Converter normalises them before calling).

        Args:
            source_ext: Source format (e.g. ``"mp4"``).
            target_ext: Target format (e.g. ``"mp3"``).

        Returns:
            True if the pair is in the supported table.
        """
        return target_ext in _SUPPORTED.get(source_ext, set())

    async def convert(self, job: Job, source_path: Path) -> Path:
        """Convert ``source_path`` to ``job.output_format`` using ffmpeg.

        Args:
            job:         Provides ``output_dir`` and ``output_format``.
            source_path: Absolute path to the source file.

        Returns:
            Path to the converted output file.

        Raises:
            ValueError:  If ``job.output_format`` is None.
            PluginError: If ffprobe or ffmpeg fails.
        """
        if not job.output_format:
            raise ValueError(
                f"job.output_format is required for conversion "
                f"(job {job.id!r})."
            )

        output_path = (
            job.output_dir / f"{source_path.stem}.{job.output_format}"
        )

        duration_us = await self._probe_duration(source_path)
        await self._run_ffmpeg(job, source_path, output_path, duration_us)

        return output_path

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _probe_duration(self, source_path: Path) -> float:
        """Return the total duration of source_path in microseconds.

        Uses ffprobe with ``-show_entries format=duration``.
        Returns 0.0 if the duration cannot be determined (progress will
        still work, just always show 0%).

        Raises:
            PluginError: If ffprobe cannot be executed or exits non-zero.
        """
        cmd = [
            self._ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            raise PluginError(
                self.info.name,
                f"ffprobe not found at {self._ffprobe_bin!r}. "
                "Make sure ffmpeg is installed and on your PATH.",
            )

        if proc.returncode != 0:
            logger.warning(
                "ffprobe exited %d for %s: %s",
                proc.returncode, source_path, stderr.decode().strip(),
            )
            return 0.0

        try:
            return float(stdout.decode().strip()) * _US_PER_SEC
        except ValueError:
            logger.warning("Could not parse ffprobe duration for %s.", source_path)
            return 0.0

    async def _run_ffmpeg(
        self,
        job: Job,
        source_path: Path,
        output_path: Path,
        duration_us: float,
    ) -> None:
        """Run ffmpeg and relay progress to the job.

        Args:
            job:         The running job (used for set_progress).
            source_path: Input file.
            output_path: Output file.
            duration_us: Total duration in microseconds (for progress ratio).

        Raises:
            PluginError: If ffmpeg cannot be found or exits with an error.
        """
        cmd = [
            self._ffmpeg_bin,
            "-y",                    # overwrite output without prompting
            "-i", str(source_path),
            "-progress", "pipe:1",   # machine-readable progress → stdout
            "-nostats",              # suppress human-readable stats on stderr
            *self._extra_args,
            str(output_path),
        ]

        logger.debug("ffmpeg command: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise PluginError(
                self.info.name,
                f"ffmpeg not found at {self._ffmpeg_bin!r}. "
                "Make sure ffmpeg is installed and on your PATH.",
            )

        await self._read_progress(proc, job, duration_us)

        if proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            raise PluginError(
                self.info.name,
                f"ffmpeg exited with code {proc.returncode} "
                f"converting {source_path.name!r} → {output_path.name!r}. "
                f"stderr: {stderr.decode(errors='replace').strip()}",
            )

    async def _read_progress(
        self,
        proc: asyncio.subprocess.Process,
        job: Job,
        duration_us: float,
    ) -> None:
        """Read ffmpeg's -progress stdout and update job.set_progress().

        ffmpeg emits blocks of ``key=value`` lines, ending with either
        ``progress=continue`` or ``progress=end``.

        Args:
            proc:        The running ffmpeg subprocess.
            job:         The job to update.
            duration_us: Total duration in microseconds. Used as denominator.
        """
        if proc.stdout is None:
            return

        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            logger.debug("ffmpeg progress: %s", line)

            if line.startswith("out_time_us="):
                value = line.split("=", 1)[1]
                try:
                    out_us = float(value)
                    if duration_us > 0 and out_us >= 0:
                        ratio = min(out_us / duration_us, 1.0)
                        try:
                            job.set_progress(ratio)
                        except RuntimeError:
                            pass  # job left RUNNING state — ignore
                except ValueError:
                    pass

            elif line == "progress=end":
                try:
                    job.set_progress(1.0)
                except RuntimeError:
                    pass