"""Job dataclass and related types.
 
A Job is the central unit of work in yamd. Every download or conversion
is represented as a Job from creation to completion. It is a plain
dataclass — no I/O, no threading, no side effects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

class JobType(str, Enum):
    """Whether this job fetches online media or converts a local file."""
 
    DOWNLOAD = "download"
    CONVERT = "convert"
    
class JobStatus(str, Enum):
    """Lifecycle states of a Job.
 
    Allowed transitions:
        PENDING -> RUNNING -> DONE
        PENDING -> CANCELLED
        RUNNING -> FAILED
        RUNNING -> CANCELLED
    """
 
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    
# Which transitions are valid, keyed by current status.
_ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.DONE: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

@dataclass
class Job:
    """A single unit of work — one download or one conversion.
 
    Attributes:
        id:            Unique identifier (UUID4 string).
        type:          DOWNLOAD or CONVERT.
        source:        URL for downloads, file path for conversions.
        output_dir:    Directory where the output file will be written.
        output_format: Target format (e.g. "mp3", "mp4"). None = keep original.
        status:        Current lifecycle state.
        progress:      Completion ratio in [0.0, 1.0].
        error:         Error message, populated when status is FAILED.
        metadata:      Arbitrary backend-specific data (title, duration, …).
        created_at:    When the job was created (UTC).
        started_at:    When execution began (UTC). None until RUNNING.
        finished_at:   When execution ended (UTC). None until terminal state.
    """
 
    type: JobType
    source: str
    output_dir: Path
 
    # Optional fields with defaults
    output_format: str | None = None
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
 
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
 
    # Identity — always last so callers never need to pass it
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # ── State transitions ──────────────────────────────────────────────────────
 
    def transition(self, new_status: JobStatus) -> None:
        """Move the job to a new status, enforcing valid transitions.
 
        Raises:
            ValueError: If the transition is not allowed from the current state.
        """
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition job {self.id!r} "
                f"from {self.status.value!r} to {new_status.value!r}. "
                f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}."
            )
        now = datetime.now(timezone.utc)
 
        if new_status is JobStatus.RUNNING:
            self.started_at = now
        elif new_status in {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}:
            self.finished_at = now
 
        self.status = new_status
        
    # ── Convenience predicates ─────────────────────────────────────────────────
 
    @property
    def is_pending(self) -> bool:
        return self.status is JobStatus.PENDING
 
    @property
    def is_running(self) -> bool:
        return self.status is JobStatus.RUNNING
 
    @property
    def is_terminal(self) -> bool:
        """True when the job has reached a final state and will not change."""
        return self.status in {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}
 
    # ── Progress helper ────────────────────────────────────────────────────────
 
    def set_progress(self, value: float) -> None:
        """Update progress, clamping to [0.0, 1.0].
 
        Raises:
            RuntimeError: If the job is not currently RUNNING.
        """
        if self.status is not JobStatus.RUNNING:
            raise RuntimeError(
                f"Cannot update progress on job {self.id!r} "
                f"with status {self.status.value!r}."
            )
        self.progress = max(0.0, min(1.0, value))
    
    # ── Representation ─────────────────────────────────────────────────────────
 
    def __repr__(self) -> str:
        return (
            f"Job(id={self.id!r}, type={self.type.value!r}, "
            f"status={self.status.value!r}, progress={self.progress:.0%})"
        )