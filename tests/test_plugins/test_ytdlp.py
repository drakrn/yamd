"""Tests for plugins/sources/ytdlp.py.

All tests mock yt_dlp.YoutubeDL — no real network calls are made.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yamd.core.job import Job, JobStatus, JobType
from yamd.plugins.base import PluginError
from yamd.plugins.sources.ytdlp import YtDlpPlugin


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def plugin() -> YtDlpPlugin:
    return YtDlpPlugin()


@pytest.fixture()
def job(tmp_path: Path) -> Job:
    j = Job(
        type=JobType.DOWNLOAD,
        source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        output_dir=tmp_path,
        output_format="mp4",
    )
    j.transition(JobStatus.RUNNING)
    return j


def make_mock_ydl(
    info: dict | None = None,
    filename: str = "/tmp/output/video.mp4",
    raise_on_extract: Exception | None = None,
) -> MagicMock:
    """Build a mock YoutubeDL context manager."""
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)

    if raise_on_extract:
        mock_ydl.extract_info.side_effect = raise_on_extract
    else:
        mock_ydl.extract_info.return_value = info or {
            "title": "Rick Astley - Never Gonna Give You Up",
            "uploader": "Rick Astley",
            "duration": 212,
            "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "ext": "mp4",
        }

    mock_ydl.prepare_filename.return_value = filename
    return mock_ydl


# ── PluginInfo ────────────────────────────────────────────────────────────────

def test_plugin_info_name(plugin: YtDlpPlugin) -> None:
    assert plugin.info.name == "ytdlp"


def test_plugin_info_display_name(plugin: YtDlpPlugin) -> None:
    assert plugin.info.display_name == "yt-dlp"


def test_plugin_info_has_version(plugin: YtDlpPlugin) -> None:
    assert isinstance(plugin.info.version, str)
    assert len(plugin.info.version) > 0


def test_plugin_info_supported_extensions(plugin: YtDlpPlugin) -> None:
    assert "mp4" in plugin.info.supported_extensions
    assert "mp3" in plugin.info.supported_extensions


# ── can_handle ────────────────────────────────────────────────────────────────

def test_can_handle_youtube_url(plugin: YtDlpPlugin, job: Job) -> None:
    mock_ie = MagicMock()
    mock_ie.suitable.return_value = True
    mock_ie.working.return_value = True

    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl._ies = {"YoutubeIE": mock_ie}

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        assert plugin.can_handle(job) is True


def test_can_handle_unrecognised_url(plugin: YtDlpPlugin, tmp_path: Path) -> None:
    job = Job(
        type=JobType.DOWNLOAD,
        source="https://definitely-not-a-site.xyz/video",
        output_dir=tmp_path,
    )
    mock_ie = MagicMock()
    mock_ie.suitable.return_value = False

    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl._ies = {"GenericIE": mock_ie}

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        assert plugin.can_handle(job) is False


def test_can_handle_returns_false_on_exception(
    plugin: YtDlpPlugin, job: Job
) -> None:
    with patch("yt_dlp.YoutubeDL", side_effect=Exception("yt-dlp exploded")):
        assert plugin.can_handle(job) is False


# ── download — happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_returns_output_path(
    plugin: YtDlpPlugin, job: Job, tmp_path: Path
) -> None:
    expected = tmp_path / "video.mp4"
    mock_ydl = make_mock_ydl(filename=str(expected))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        result = await plugin.download(job)

    assert result == expected


@pytest.mark.asyncio
async def test_download_populates_metadata(
    plugin: YtDlpPlugin, job: Job, tmp_path: Path
) -> None:
    mock_ydl = make_mock_ydl(filename=str(tmp_path / "video.mp4"))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        await plugin.download(job)

    assert job.metadata["title"] == "Rick Astley - Never Gonna Give You Up"
    assert job.metadata["uploader"] == "Rick Astley"
    assert job.metadata["duration"] == 212


@pytest.mark.asyncio
async def test_download_output_format_changes_extension(
    plugin: YtDlpPlugin, tmp_path: Path
) -> None:
    job = Job(
        type=JobType.DOWNLOAD,
        source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        output_dir=tmp_path,
        output_format="mp3",
    )
    job.transition(JobStatus.RUNNING)

    # yt-dlp returns a .webm filename; our plugin rewrites to .mp3
    mock_ydl = make_mock_ydl(filename=str(tmp_path / "video.webm"))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        result = await plugin.download(job)

    assert result.suffix == ".mp3"


# ── progress hook ─────────────────────────────────────────────────────────────

def test_progress_hook_downloading(plugin: YtDlpPlugin, job: Job) -> None:
    hook = plugin._make_progress_hook(job)
    hook({
        "status": "downloading",
        "downloaded_bytes": 50,
        "total_bytes": 100,
    })
    # 50/100 * 0.95 = 0.475
    assert job.progress == pytest.approx(0.475)


def test_progress_hook_finished_sets_095(plugin: YtDlpPlugin, job: Job) -> None:
    hook = plugin._make_progress_hook(job)
    hook({"status": "finished"})
    assert job.progress == pytest.approx(0.95)


def test_progress_hook_no_total_bytes_skips(
    plugin: YtDlpPlugin, job: Job
) -> None:
    hook = plugin._make_progress_hook(job)
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 0})
    assert job.progress == 0.0  # unchanged


def test_progress_hook_uses_estimate_when_no_total(
    plugin: YtDlpPlugin, job: Job
) -> None:
    hook = plugin._make_progress_hook(job)
    hook({
        "status": "downloading",
        "downloaded_bytes": 25,
        "total_bytes": None,
        "total_bytes_estimate": 100,
    })
    assert job.progress == pytest.approx(0.25 * 0.95)


def test_progress_hook_clamps_above_095(plugin: YtDlpPlugin, job: Job) -> None:
    hook = plugin._make_progress_hook(job)
    hook({
        "status": "downloading",
        "downloaded_bytes": 200,
        "total_bytes": 100,
    })
    assert job.progress <= 0.95


def test_progress_hook_silences_runtime_error_on_bad_status(
    plugin: YtDlpPlugin, tmp_path: Path
) -> None:
    """Hook must not raise even if job is not RUNNING."""
    job = Job(
        type=JobType.DOWNLOAD,
        source="https://example.com",
        output_dir=tmp_path,
    )
    hook = plugin._make_progress_hook(job)
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
    # No exception raised — RuntimeError from set_progress is suppressed


# ── download — failure paths ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_raises_plugin_error_on_download_error(
    plugin: YtDlpPlugin, job: Job
) -> None:
    import yt_dlp.utils

    mock_ydl = make_mock_ydl(
        raise_on_extract=yt_dlp.utils.DownloadError("404 not found")
    )
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(PluginError, match="Download failed"):
            await plugin.download(job)


@pytest.mark.asyncio
async def test_download_raises_plugin_error_on_extractor_error(
    plugin: YtDlpPlugin, job: Job
) -> None:
    import yt_dlp.utils

    mock_ydl = make_mock_ydl(
        raise_on_extract=yt_dlp.utils.ExtractorError("unsupported URL")
    )
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(PluginError, match="Could not extract"):
            await plugin.download(job)


@pytest.mark.asyncio
async def test_download_raises_plugin_error_when_info_is_none(
    plugin: YtDlpPlugin, job: Job
) -> None:
    mock_ydl = make_mock_ydl(info=None)
    mock_ydl.extract_info.return_value = None

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(PluginError, match="no info"):
            await plugin.download(job)


@pytest.mark.asyncio
async def test_download_wraps_unexpected_exception(
    plugin: YtDlpPlugin, job: Job
) -> None:
    mock_ydl = make_mock_ydl(
        raise_on_extract=RuntimeError("something unexpected")
    )
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(PluginError, match="Unexpected error"):
            await plugin.download(job)


# ── extra_opts ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extra_opts_are_merged(tmp_path: Path) -> None:
    plugin = YtDlpPlugin(extra_opts={"ratelimit": 1_000_000})
    job = Job(
        type=JobType.DOWNLOAD,
        source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        output_dir=tmp_path,
        output_format="mp4",
    )
    job.transition(JobStatus.RUNNING)

    mock_ydl = make_mock_ydl(filename=str(tmp_path / "video.mp4"))
    captured_opts: dict = {}

    def fake_ydl_constructor(opts):
        captured_opts.update(opts)
        return mock_ydl

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl_constructor):
        await plugin.download(job)

    assert captured_opts.get("ratelimit") == 1_000_000