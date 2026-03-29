"""Tests for plugins/formats/ffmpeg.py.

All tests mock asyncio.create_subprocess_exec — no real ffmpeg needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yamd.core.job import Job, JobStatus, JobType
from yamd.plugins.base import PluginError
from yamd.plugins.formats.ffmpeg import FfmpegPlugin


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def plugin() -> FfmpegPlugin:
    return FfmpegPlugin()


@pytest.fixture()
def job(tmp_path: Path) -> Job:
    j = Job(
        type=JobType.CONVERT,
        source=str(tmp_path / "input.mp4"),
        output_dir=tmp_path,
        output_format="mp3",
    )
    j.transition(JobStatus.RUNNING)
    return j


def make_proc(
    stdout_lines: list[str] | None = None,
    returncode: int = 0,
    stderr: bytes = b"",
) -> MagicMock:
    """Build a mock asyncio subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=stderr)

    lines = [f"{line}\n".encode() for line in (stdout_lines or [])]

    async def fake_stdout():
        for line in lines:
            yield line

    proc.stdout = fake_stdout()
    proc.communicate = AsyncMock(return_value=(b"10.5\n", b""))
    return proc


def make_ffprobe_proc(duration_seconds: float = 10.5) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    stdout = f"{duration_seconds}\n".encode()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ── PluginInfo ────────────────────────────────────────────────────────────────

def test_plugin_info_name(plugin: FfmpegPlugin) -> None:
    assert plugin.info.name == "ffmpeg"


def test_plugin_info_display_name(plugin: FfmpegPlugin) -> None:
    assert plugin.info.display_name == "ffmpeg"


def test_plugin_info_has_supported_extensions(plugin: FfmpegPlugin) -> None:
    assert "mp4" in plugin.info.supported_extensions
    assert "mp3" in plugin.info.supported_extensions


# ── can_convert ───────────────────────────────────────────────────────────────

def test_can_convert_mp4_to_mp3(plugin: FfmpegPlugin) -> None:
    assert plugin.can_convert("mp4", "mp3") is True


def test_can_convert_wav_to_flac(plugin: FfmpegPlugin) -> None:
    assert plugin.can_convert("wav", "flac") is True


def test_cannot_convert_unknown_pair(plugin: FfmpegPlugin) -> None:
    assert plugin.can_convert("xyz", "abc") is False


def test_cannot_convert_same_format(plugin: FfmpegPlugin) -> None:
    assert plugin.can_convert("mp3", "mp3") is False


def test_can_convert_mkv_to_mp4(plugin: FfmpegPlugin) -> None:
    assert plugin.can_convert("mkv", "mp4") is True


# ── _probe_duration ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_duration_returns_microseconds(
    plugin: FfmpegPlugin, tmp_path: Path
) -> None:
    ffprobe_proc = make_ffprobe_proc(duration_seconds=10.5)

    with patch("asyncio.create_subprocess_exec", return_value=ffprobe_proc):
        duration = await plugin._probe_duration(tmp_path / "input.mp4")

    assert duration == pytest.approx(10.5 * 1_000_000)


@pytest.mark.asyncio
async def test_probe_duration_returns_zero_on_bad_output(
    plugin: FfmpegPlugin, tmp_path: Path
) -> None:
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"N/A\n", b""))

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        duration = await plugin._probe_duration(tmp_path / "input.mp4")

    assert duration == 0.0


@pytest.mark.asyncio
async def test_probe_duration_returns_zero_on_nonzero_exit(
    plugin: FfmpegPlugin, tmp_path: Path
) -> None:
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        duration = await plugin._probe_duration(tmp_path / "input.mp4")

    assert duration == 0.0


@pytest.mark.asyncio
async def test_probe_duration_raises_when_ffprobe_not_found(
    plugin: FfmpegPlugin, tmp_path: Path
) -> None:
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(PluginError, match="ffprobe not found"):
            await plugin._probe_duration(tmp_path / "input.mp4")


# ── convert — happy path ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_convert_returns_output_path(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    progress_lines = [
        "out_time_us=5000000",
        "progress=continue",
        "out_time_us=10500000",
        "progress=end",
    ]
    ffprobe_proc = make_ffprobe_proc(10.5)
    ffmpeg_proc  = make_proc(stdout_lines=progress_lines)

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, ffmpeg_proc],
    ):
        result = await plugin.convert(job, tmp_path / "input.mp4")

    assert result == tmp_path / "input.mp3"


@pytest.mark.asyncio
async def test_convert_updates_progress(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    progress_lines = [
        "out_time_us=5250000",
        "progress=continue",
        "progress=end",
    ]
    ffprobe_proc = make_ffprobe_proc(10.5)
    ffmpeg_proc  = make_proc(stdout_lines=progress_lines)

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, ffmpeg_proc],
    ):
        await plugin.convert(job, tmp_path / "input.mp4")

    assert job.progress == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_convert_progress_end_sets_full(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    ffprobe_proc = make_ffprobe_proc(10.0)
    ffmpeg_proc  = make_proc(stdout_lines=["progress=end"])

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, ffmpeg_proc],
    ):
        await plugin.convert(job, tmp_path / "input.mp4")

    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_convert_with_zero_duration_skips_progress(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    """When ffprobe returns 0, progress lines are ignored (no division by zero)."""
    ffprobe_proc = make_ffprobe_proc(0.0)
    ffmpeg_proc  = make_proc(stdout_lines=[
        "out_time_us=5000000",
        "progress=end",
    ])

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, ffmpeg_proc],
    ):
        await plugin.convert(job, tmp_path / "input.mp4")

    assert job.progress == 1.0  # progress=end still fires


# ── convert — failure paths ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_convert_raises_on_ffmpeg_not_found(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    ffprobe_proc = make_ffprobe_proc(10.0)

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, FileNotFoundError],
    ):
        with pytest.raises(PluginError, match="ffmpeg not found"):
            await plugin.convert(job, tmp_path / "input.mp4")


@pytest.mark.asyncio
async def test_convert_raises_on_nonzero_exit(
    plugin: FfmpegPlugin, job: Job, tmp_path: Path
) -> None:
    ffprobe_proc = make_ffprobe_proc(10.0)
    ffmpeg_proc  = make_proc(
        stdout_lines=[],
        returncode=1,
        stderr=b"Invalid codec",
    )

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[ffprobe_proc, ffmpeg_proc],
    ):
        with pytest.raises(PluginError, match="exited with code 1"):
            await plugin.convert(job, tmp_path / "input.mp4")


@pytest.mark.asyncio
async def test_convert_raises_without_output_format(
    plugin: FfmpegPlugin, tmp_path: Path
) -> None:
    job = Job(
        type=JobType.CONVERT,
        source=str(tmp_path / "input.mp4"),
        output_dir=tmp_path,
        output_format=None,
    )
    job.transition(JobStatus.RUNNING)

    with pytest.raises(ValueError, match="output_format"):
        await plugin.convert(job, tmp_path / "input.mp4")


# ── extra_args ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extra_args_passed_to_ffmpeg(tmp_path: Path) -> None:
    plugin = FfmpegPlugin(extra_args=["-b:a", "192k"])
    job = Job(
        type=JobType.CONVERT,
        source=str(tmp_path / "input.mp4"),
        output_dir=tmp_path,
        output_format="mp3",
    )
    job.transition(JobStatus.RUNNING)

    ffprobe_proc = make_ffprobe_proc(5.0)
    ffmpeg_proc  = make_proc(stdout_lines=["progress=end"])
    captured_cmd: list[str] = []

    async def fake_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        if "ffprobe" in cmd[0]:
            return ffprobe_proc
        return ffmpeg_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await plugin.convert(job, tmp_path / "input.mp4")

    assert "-b:a" in captured_cmd
    assert "192k" in captured_cmd


# ── custom binaries ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_ffmpeg_bin_is_used(tmp_path: Path) -> None:
    plugin = FfmpegPlugin(
        ffmpeg_bin="/usr/local/bin/ffmpeg",
        ffprobe_bin="/usr/local/bin/ffprobe",
    )
    job = Job(
        type=JobType.CONVERT,
        source=str(tmp_path / "input.mp4"),
        output_dir=tmp_path,
        output_format="mp3",
    )
    job.transition(JobStatus.RUNNING)

    ffprobe_proc = make_ffprobe_proc(5.0)
    ffmpeg_proc  = make_proc(stdout_lines=["progress=end"])
    first_cmd: list[str] = []
    second_cmd: list[str] = []
    call_count = 0

    async def fake_exec(*cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_cmd.append(cmd[0])
            return ffprobe_proc
        second_cmd.append(cmd[0])
        return ffmpeg_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await plugin.convert(job, tmp_path / "input.mp4")

    assert first_cmd[0] == "/usr/local/bin/ffprobe"
    assert second_cmd[0] == "/usr/local/bin/ffmpeg"