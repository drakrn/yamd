"""Tests for core/converter.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yamd.core.converter import Converter, MissingOutputFormatError, NoFormatPluginError
from yamd.core.job import Job, JobStatus, JobType
from yamd.core.job_manager import JobManager
from yamd.plugins.base import FormatPlugin, PluginError, PluginInfo


# ── Fake plugin helpers ───────────────────────────────────────────────────────

def make_plugin(
    name: str = "fake-format",
    can_convert_result: bool = True,
    output_suffix: str = "mp3",
    raises: Exception | None = None,
) -> FormatPlugin:
    """Build a mock FormatPlugin."""
    plugin = MagicMock(spec=FormatPlugin)
    plugin.info = PluginInfo(
        name=name,
        display_name=name.capitalize(),
        version="0.0.1",
        description="Fake format plugin for testing.",
    )
    plugin.can_convert.return_value = can_convert_result

    if raises:
        async def failing_convert(job: Job, source_path: Path) -> Path:
            raise raises

        plugin.convert = failing_convert
    else:
        async def fake_convert(job: Job, source_path: Path) -> Path:
            job.set_progress(1.0)
            return job.output_dir / f"output.{output_suffix}"

        plugin.convert = fake_convert

    return plugin


def make_convert_job(
    tmp_path: Path,
    source: str = "/tmp/input.mp4",
    output_format: str | None = "mp3",
) -> Job:
    return Job(
        type=JobType.CONVERT,
        source=source,
        output_dir=tmp_path,
        output_format=output_format,
    )


# ── Construction ──────────────────────────────────────────────────────────────

def test_no_plugins_raises() -> None:
    manager = JobManager()
    with pytest.raises(ValueError, match="At least one"):
        Converter(manager, plugins=[])


def test_plugins_property_returns_copy(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    converter = Converter(manager, plugins=[plugin])
    plugins = converter.plugins
    plugins.clear()
    assert len(converter.plugins) == 1


# ── Plugin selection ──────────────────────────────────────────────────────────

def test_resolve_plugin_returns_first_match() -> None:
    manager = JobManager()
    first  = make_plugin(name="first",  can_convert_result=True)
    second = make_plugin(name="second", can_convert_result=True)
    converter = Converter(manager, plugins=[first, second])
    assert converter.resolve_plugin("mp4", "mp3").info.name == "first"


def test_resolve_plugin_skips_non_matching() -> None:
    manager = JobManager()
    skip  = make_plugin(name="skip",  can_convert_result=False)
    match = make_plugin(name="match", can_convert_result=True)
    converter = Converter(manager, plugins=[skip, match])
    assert converter.resolve_plugin("mp4", "mp3").info.name == "match"


def test_resolve_plugin_raises_when_no_match() -> None:
    manager = JobManager()
    plugin = make_plugin(can_convert_result=False)
    converter = Converter(manager, plugins=[plugin])
    with pytest.raises(NoFormatPluginError):
        converter.resolve_plugin("avi", "flac")


def test_resolve_plugin_skips_plugin_raising_on_can_convert() -> None:
    manager = JobManager()
    broken = make_plugin(name="broken")
    broken.can_convert.side_effect = RuntimeError("plugin exploded")
    fallback = make_plugin(name="fallback", can_convert_result=True)
    converter = Converter(manager, plugins=[broken, fallback])
    assert converter.resolve_plugin("mp4", "mp3").info.name == "fallback"


# ── convert() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_convert_submits_job_to_manager(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path)

    await converter.convert(job)
    await manager.wait_all()

    assert job.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_convert_sets_output_path_in_metadata(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(output_suffix="mp3")
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path, output_format="mp3")

    await converter.convert(job)
    await manager.wait_all()

    assert "output_path" in job.metadata
    assert job.metadata["output_path"].endswith(".mp3")


@pytest.mark.asyncio
async def test_convert_sets_ext_metadata(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path, source="/tmp/video.mp4", output_format="mp3")

    await converter.convert(job)
    await manager.wait_all()

    assert job.metadata["source_ext"] == "mp4"
    assert job.metadata["target_ext"] == "mp3"


@pytest.mark.asyncio
async def test_convert_raises_for_non_convert_job(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    converter = Converter(manager, plugins=[plugin])
    job = Job(
        type=JobType.DOWNLOAD,
        source="https://example.com/v",
        output_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="CONVERT"):
        await converter.convert(job)


@pytest.mark.asyncio
async def test_convert_raises_missing_output_format(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path, output_format=None)

    with pytest.raises(MissingOutputFormatError):
        await converter.convert(job)


@pytest.mark.asyncio
async def test_convert_raises_no_format_plugin_error(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(can_convert_result=False)
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path)

    with pytest.raises(NoFormatPluginError):
        await converter.convert(job)


@pytest.mark.asyncio
async def test_convert_job_fails_when_plugin_raises(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(raises=PluginError("fake-format", "codec not found"))
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path)

    await converter.convert(job)
    await manager.wait_all()

    assert job.status is JobStatus.FAILED
    assert "codec not found" in job.error


@pytest.mark.asyncio
async def test_convert_normalises_extension_case(tmp_path: Path) -> None:
    """Extensions should be lowercased before plugin selection."""
    manager = JobManager()
    plugin = make_plugin(can_convert_result=False)

    def case_sensitive_can_convert(src: str, tgt: str) -> bool:
        return src == "mp4" and tgt == "mp3"

    plugin.can_convert.side_effect = case_sensitive_can_convert
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(
        tmp_path,
        source="/tmp/input.MP4",
        output_format="MP3",
    )

    await converter.convert(job)
    await manager.wait_all()

    assert job.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_convert_does_not_wait_for_completion(tmp_path: Path) -> None:
    """convert() should return immediately after queuing."""
    import asyncio

    completed = asyncio.Event()

    async def slow_convert(job: Job, source_path: Path) -> Path:
        job.set_progress(0.5)
        await asyncio.sleep(0.1)
        job.set_progress(1.0)
        completed.set()
        return job.output_dir / "output.mp3"

    manager = JobManager()
    plugin = make_plugin()
    plugin.convert = slow_convert
    converter = Converter(manager, plugins=[plugin])
    job = make_convert_job(tmp_path)

    await converter.convert(job)
    assert not completed.is_set()

    await manager.wait_all()
    assert completed.is_set()


# ── NoFormatPluginError ───────────────────────────────────────────────────────

def test_no_format_plugin_error_message() -> None:
    err = NoFormatPluginError("avi", "flac")
    assert "avi" in str(err)
    assert "flac" in str(err)
    assert err.source_ext == "avi"
    assert err.target_ext == "flac"


def test_no_format_plugin_error_is_exception() -> None:
    with pytest.raises(NoFormatPluginError):
        raise NoFormatPluginError("x", "y")


# ── MissingOutputFormatError ──────────────────────────────────────────────────

def test_missing_output_format_error_message() -> None:
    err = MissingOutputFormatError("job-123")
    assert "job-123" in str(err)


def test_missing_output_format_error_is_value_error() -> None:
    err = MissingOutputFormatError("job-abc")
    assert isinstance(err, ValueError)