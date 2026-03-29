"""Tests for plugins/base.py — ABCs, PluginInfo, and PluginError."""

from pathlib import Path

import pytest

from yamd.core.job import Job, JobType
from yamd.plugins.base import (
    FormatPlugin,
    OutputPlugin,
    PluginError,
    PluginInfo,
    SourcePlugin,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def job(tmp_path: Path) -> Job:
    return Job(
        type=JobType.DOWNLOAD,
        source="https://example.com/video",
        output_dir=tmp_path,
        output_format="mp4",
    )


# ── Minimal concrete implementations for testing ──────────────────────────────

class ConcreteSourcePlugin(SourcePlugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="test-source",
            display_name="Test Source",
            version="1.0.0",
            description="A test source plugin.",
            supported_extensions=["mp4", "webm"],
        )

    def can_handle(self, job: Job) -> bool:
        return job.source.startswith("https://example.com")

    async def download(self, job: Job) -> Path:
        job.set_progress(1.0)
        return job.output_dir / "output.mp4"


class ConcreteFormatPlugin(FormatPlugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="test-format",
            display_name="Test Format",
            version="1.0.0",
            description="A test format plugin.",
        )

    def can_convert(self, source_ext: str, target_ext: str) -> bool:
        return source_ext == "mp4" and target_ext == "mp3"

    async def convert(self, job: Job, source_path: Path) -> Path:
        if job.output_format is None:
            raise ValueError("output_format is required for conversion.")
        job.set_progress(1.0)
        return job.output_dir / f"output.{job.output_format}"


class ConcreteOutputPlugin(OutputPlugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="test-output",
            display_name="Test Output",
            version="1.0.0",
            description="A test output plugin.",
        )

    async def process(self, job: Job, result_path: Path) -> Path:
        return result_path


# ── PluginInfo ────────────────────────────────────────────────────────────────

def test_plugin_info_fields() -> None:
    info = PluginInfo(
        name="ytdlp",
        display_name="yt-dlp",
        version="2024.1.1",
        description="Download media via yt-dlp.",
        supported_extensions=["mp4", "webm", "mp3"],
    )
    assert info.name == "ytdlp"
    assert info.display_name == "yt-dlp"
    assert info.version == "2024.1.1"
    assert "mp4" in info.supported_extensions


def test_plugin_info_default_extensions_empty() -> None:
    info = PluginInfo(
        name="x",
        display_name="X",
        version="0.1",
        description="Minimal.",
    )
    assert info.supported_extensions == []


def test_plugin_info_is_frozen() -> None:
    info = PluginInfo(name="x", display_name="X", version="0.1", description=".")
    with pytest.raises(Exception):
        info.name = "y"  # type: ignore[misc]


# ── SourcePlugin ──────────────────────────────────────────────────────────────

def test_source_plugin_info(job: Job) -> None:
    plugin = ConcreteSourcePlugin()
    assert plugin.info.name == "test-source"


def test_source_plugin_can_handle_matching_url(job: Job) -> None:
    plugin = ConcreteSourcePlugin()
    assert plugin.can_handle(job) is True


def test_source_plugin_cannot_handle_other_url(tmp_path: Path) -> None:
    plugin = ConcreteSourcePlugin()
    other_job = Job(
        type=JobType.DOWNLOAD,
        source="https://other.com/video",
        output_dir=tmp_path,
    )
    assert plugin.can_handle(other_job) is False


@pytest.mark.asyncio
async def test_source_plugin_download(job: Job) -> None:
    job.transition_to_running = lambda: None
    from yamd.core.job import JobStatus
    job.transition(JobStatus.RUNNING)
    plugin = ConcreteSourcePlugin()
    result = await plugin.download(job)
    assert result == job.output_dir / "output.mp4"
    assert job.progress == 1.0


def test_source_plugin_cannot_instantiate_abc() -> None:
    with pytest.raises(TypeError):
        SourcePlugin()  # type: ignore[abstract]


# ── FormatPlugin ──────────────────────────────────────────────────────────────

def test_format_plugin_info() -> None:
    plugin = ConcreteFormatPlugin()
    assert plugin.info.name == "test-format"


def test_format_plugin_can_convert_supported() -> None:
    plugin = ConcreteFormatPlugin()
    assert plugin.can_convert("mp4", "mp3") is True


def test_format_plugin_cannot_convert_unsupported() -> None:
    plugin = ConcreteFormatPlugin()
    assert plugin.can_convert("avi", "flac") is False


@pytest.mark.asyncio
async def test_format_plugin_convert(job: Job, tmp_path: Path) -> None:
    from yamd.core.job import JobStatus
    job.transition(JobStatus.RUNNING)
    plugin = ConcreteFormatPlugin()
    source = tmp_path / "input.mp4"
    source.touch()
    result = await plugin.convert(job, source)
    assert result == job.output_dir / "output.mp4"
    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_format_plugin_raises_without_output_format(
    tmp_path: Path,
) -> None:
    from yamd.core.job import JobStatus
    job = Job(
        type=JobType.CONVERT,
        source="/tmp/input.mp4",
        output_dir=tmp_path,
        output_format=None,
    )
    job.transition(JobStatus.RUNNING)
    plugin = ConcreteFormatPlugin()
    with pytest.raises(ValueError, match="output_format"):
        await plugin.convert(job, tmp_path / "input.mp4")


def test_format_plugin_cannot_instantiate_abc() -> None:
    with pytest.raises(TypeError):
        FormatPlugin()  # type: ignore[abstract]


# ── OutputPlugin ──────────────────────────────────────────────────────────────

def test_output_plugin_info() -> None:
    plugin = ConcreteOutputPlugin()
    assert plugin.info.name == "test-output"


@pytest.mark.asyncio
async def test_output_plugin_process_returns_path(
    job: Job, tmp_path: Path
) -> None:
    plugin = ConcreteOutputPlugin()
    result_path = tmp_path / "output.mp4"
    result_path.touch()
    returned = await plugin.process(job, result_path)
    assert returned == result_path


def test_output_plugin_cannot_instantiate_abc() -> None:
    with pytest.raises(TypeError):
        OutputPlugin()  # type: ignore[abstract]


# ── PluginError ───────────────────────────────────────────────────────────────

def test_plugin_error_message() -> None:
    err = PluginError("ytdlp", "network timeout")
    assert "ytdlp" in str(err)
    assert "network timeout" in str(err)


def test_plugin_error_attributes() -> None:
    cause = ConnectionError("refused")
    err = PluginError("ffmpeg", "process crashed", cause=cause)
    assert err.plugin_name == "ffmpeg"
    assert err.message == "process crashed"
    assert err.cause is cause


def test_plugin_error_without_cause() -> None:
    err = PluginError("gallerydl", "404 not found")
    assert err.cause is None


def test_plugin_error_repr() -> None:
    err = PluginError("ffmpeg", "bad codec")
    r = repr(err)
    assert "ffmpeg" in r
    assert "bad codec" in r


def test_plugin_error_is_exception() -> None:
    err = PluginError("x", "y")
    assert isinstance(err, Exception)
    with pytest.raises(PluginError):
        raise err