"""Tests for core/job.py — Job dataclass and state machine."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from yamd.core.job import Job, JobStatus, JobType


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def download_job(tmp_path: Path) -> Job:
    return Job(
        type=JobType.DOWNLOAD,
        source="https://example.com/video",
        output_dir=tmp_path,
    )


@pytest.fixture()
def convert_job(tmp_path: Path) -> Job:
    return Job(
        type=JobType.CONVERT,
        source="/tmp/input.mp4",
        output_dir=tmp_path,
        output_format="mp3",
    )


# ── Construction ──────────────────────────────────────────────────────────────

def test_job_has_unique_ids(tmp_path: Path) -> None:
    a = Job(type=JobType.DOWNLOAD, source="https://a.com", output_dir=tmp_path)
    b = Job(type=JobType.DOWNLOAD, source="https://b.com", output_dir=tmp_path)
    assert a.id != b.id


def test_job_default_status_is_pending(download_job: Job) -> None:
    assert download_job.status is JobStatus.PENDING


def test_job_default_progress_is_zero(download_job: Job) -> None:
    assert download_job.progress == 0.0


def test_job_created_at_is_utc(download_job: Job) -> None:
    assert download_job.created_at.tzinfo is not None
    assert download_job.started_at is None
    assert download_job.finished_at is None


def test_job_output_format_optional(download_job: Job) -> None:
    assert download_job.output_format is None


# ── State transitions ─────────────────────────────────────────────────────────

def test_pending_to_running(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    assert download_job.status is JobStatus.RUNNING
    assert isinstance(download_job.started_at, datetime)


def test_running_to_done(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.transition(JobStatus.DONE)
    assert download_job.status is JobStatus.DONE
    assert isinstance(download_job.finished_at, datetime)


def test_running_to_failed(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.error = "network timeout"
    download_job.transition(JobStatus.FAILED)
    assert download_job.status is JobStatus.FAILED
    assert isinstance(download_job.finished_at, datetime)


def test_pending_to_cancelled(download_job: Job) -> None:
    download_job.transition(JobStatus.CANCELLED)
    assert download_job.status is JobStatus.CANCELLED
    assert isinstance(download_job.finished_at, datetime)


def test_running_to_cancelled(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.transition(JobStatus.CANCELLED)
    assert download_job.status is JobStatus.CANCELLED


def test_invalid_transition_raises(download_job: Job) -> None:
    with pytest.raises(ValueError, match="Cannot transition"):
        download_job.transition(JobStatus.DONE)  # PENDING → DONE is not allowed


def test_terminal_state_raises_on_transition(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.transition(JobStatus.DONE)
    with pytest.raises(ValueError, match="terminal state"):
        download_job.transition(JobStatus.FAILED)


# ── Predicates ────────────────────────────────────────────────────────────────

def test_is_pending(download_job: Job) -> None:
    assert download_job.is_pending
    assert not download_job.is_running
    assert not download_job.is_terminal


def test_is_running(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    assert download_job.is_running
    assert not download_job.is_pending
    assert not download_job.is_terminal


def test_is_terminal_done(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.transition(JobStatus.DONE)
    assert download_job.is_terminal


def test_is_terminal_failed(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.transition(JobStatus.FAILED)
    assert download_job.is_terminal


def test_is_terminal_cancelled(download_job: Job) -> None:
    download_job.transition(JobStatus.CANCELLED)
    assert download_job.is_terminal


# ── Progress ──────────────────────────────────────────────────────────────────

def test_set_progress_while_running(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.set_progress(0.42)
    assert download_job.progress == pytest.approx(0.42)


def test_set_progress_clamps_above_one(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.set_progress(1.5)
    assert download_job.progress == 1.0


def test_set_progress_clamps_below_zero(download_job: Job) -> None:
    download_job.transition(JobStatus.RUNNING)
    download_job.set_progress(-0.1)
    assert download_job.progress == 0.0


def test_set_progress_raises_when_not_running(download_job: Job) -> None:
    with pytest.raises(RuntimeError, match="Cannot update progress"):
        download_job.set_progress(0.5)


# ── Representation ────────────────────────────────────────────────────────────

def test_repr_contains_key_fields(download_job: Job) -> None:
    r = repr(download_job)
    assert "download" in r
    assert "pending" in r
    assert "0%" in r