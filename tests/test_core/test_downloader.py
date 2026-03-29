"""Tests for core/downloader.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yamd.core.downloader import Downloader, NoPluginError
from yamd.core.job import Job, JobStatus, JobType
from yamd.core.job_manager import JobManager
from yamd.plugins.base import PluginError, PluginInfo, SourcePlugin


# ── Fake plugin helpers ───────────────────────────────────────────────────────

def make_plugin(
    name: str = "fake",
    can_handle_result: bool = True,
    download_result: Path | None = None,
    raises: Exception | None = None,
) -> SourcePlugin:
    """Build a mock SourcePlugin."""
    plugin = MagicMock(spec=SourcePlugin)
    plugin.info = PluginInfo(
        name=name,
        display_name=name.capitalize(),
        version="0.0.1",
        description="Fake plugin for testing.",
    )
    plugin.can_handle.return_value = can_handle_result

    if raises:
        plugin.download = AsyncMock(side_effect=raises)
    else:
        async def fake_download(job: Job) -> Path:
            job.set_progress(1.0)
            return download_result or (job.output_dir / "output.mp4")

        plugin.download = fake_download

    return plugin


def make_job(tmp_path: Path, source: str = "https://example.com/v") -> Job:
    return Job(type=JobType.DOWNLOAD, source=source, output_dir=tmp_path)


# ── Construction ──────────────────────────────────────────────────────────────

def test_no_plugins_raises() -> None:
    manager = JobManager()
    with pytest.raises(ValueError, match="At least one"):
        Downloader(manager, plugins=[])


def test_plugins_property_returns_copy(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    downloader = Downloader(manager, plugins=[plugin])
    plugins = downloader.plugins
    plugins.clear()
    assert len(downloader.plugins) == 1


# ── Plugin selection ──────────────────────────────────────────────────────────

def test_resolve_plugin_returns_first_match(tmp_path: Path) -> None:
    manager = JobManager()
    first  = make_plugin(name="first",  can_handle_result=True)
    second = make_plugin(name="second", can_handle_result=True)
    downloader = Downloader(manager, plugins=[first, second])
    job = make_job(tmp_path)
    assert downloader.resolve_plugin(job).info.name == "first"


def test_resolve_plugin_skips_non_matching(tmp_path: Path) -> None:
    manager = JobManager()
    skip  = make_plugin(name="skip",  can_handle_result=False)
    match = make_plugin(name="match", can_handle_result=True)
    downloader = Downloader(manager, plugins=[skip, match])
    job = make_job(tmp_path)
    assert downloader.resolve_plugin(job).info.name == "match"


def test_resolve_plugin_raises_when_no_match(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(can_handle_result=False)
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)
    with pytest.raises(NoPluginError, match="No source plugin"):
        downloader.resolve_plugin(job)


def test_resolve_plugin_skips_plugin_that_raises_on_can_handle(
    tmp_path: Path,
) -> None:
    manager = JobManager()
    broken = make_plugin(name="broken")
    broken.can_handle.side_effect = RuntimeError("extractor exploded")
    fallback = make_plugin(name="fallback", can_handle_result=True)
    downloader = Downloader(manager, plugins=[broken, fallback])
    job = make_job(tmp_path)
    assert downloader.resolve_plugin(job).info.name == "fallback"


# ── download() ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_submits_job_to_manager(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)

    await downloader.download(job)
    await manager.wait_all()

    assert job.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_download_sets_output_path_in_metadata(tmp_path: Path) -> None:
    expected = tmp_path / "output.mp4"
    manager = JobManager()
    plugin = make_plugin(download_result=expected)
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)

    await downloader.download(job)
    await manager.wait_all()

    assert job.metadata["output_path"] == str(expected)


@pytest.mark.asyncio
async def test_download_raises_for_non_download_job(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin()
    downloader = Downloader(manager, plugins=[plugin])
    job = Job(
        type=JobType.CONVERT,
        source="/tmp/input.mp4",
        output_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="DOWNLOAD"):
        await downloader.download(job)


@pytest.mark.asyncio
async def test_download_raises_no_plugin_error(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(can_handle_result=False)
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)

    with pytest.raises(NoPluginError):
        await downloader.download(job)


@pytest.mark.asyncio
async def test_download_job_fails_when_plugin_raises(tmp_path: Path) -> None:
    manager = JobManager()
    plugin = make_plugin(raises=PluginError("fake", "network error"))
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)

    await downloader.download(job)
    await manager.wait_all()

    assert job.status is JobStatus.FAILED
    assert "network error" in job.error


@pytest.mark.asyncio
async def test_download_does_not_wait_for_completion(tmp_path: Path) -> None:
    """download() should return immediately — it only queues the job."""
    import asyncio
    started = asyncio.Event()
    completed = asyncio.Event()

    async def slow_download(job: Job) -> Path:
        job.set_progress(0.5)
        started.set()
        await asyncio.sleep(0.1)
        job.set_progress(1.0)
        completed.set()
        return job.output_dir / "output.mp4"

    manager = JobManager()
    plugin = make_plugin()
    plugin.download = slow_download
    downloader = Downloader(manager, plugins=[plugin])
    job = make_job(tmp_path)

    await downloader.download(job)
    # At this point the job may not be done yet
    assert not completed.is_set()

    await manager.wait_all()
    assert completed.is_set()


# ── NoPluginError ─────────────────────────────────────────────────────────────

def test_no_plugin_error_message() -> None:
    err = NoPluginError("https://unknown.com/video")
    assert "https://unknown.com/video" in str(err)
    assert err.source == "https://unknown.com/video"


def test_no_plugin_error_is_exception() -> None:
    with pytest.raises(NoPluginError):
        raise NoPluginError("https://x.com")