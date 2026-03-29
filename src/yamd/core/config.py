"""Application configuration.

All settings are read from environment variables or a .env file.
The prefix for every variable is ``YAMD_`` (e.g. YAMD_OUTPUT_DIR).

Usage::

    from yamd.core.config import get_settings

    settings = get_settings()
    print(settings.output_dir)

``get_settings()`` is cached — the same object is returned on every call
within a process. In tests, use ``get_settings.cache_clear()`` and
temporarily set env vars to override values.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, environment-driven configuration for yamd.

    All fields map to a ``YAMD_``-prefixed environment variable.
    Defaults are chosen to be safe for local development.

    Attributes:
        output_dir:          Directory where downloaded / converted files land.
        log_level:           Python logging level name (DEBUG, INFO, …).
        max_concurrent_jobs: Concurrency cap passed to JobManager.
        ffmpeg_bin:          Path to the ffmpeg binary. Empty = use PATH.
        api_host:            Host the HTTP API binds to.
        api_port:            Port the HTTP API listens on.
        api_key:             Bearer token protecting the API. Empty = no auth.
    """

    model_config = SettingsConfigDict(
        env_prefix="YAMD_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore unknown YAMD_ vars — forward-compatible with future versions.
        extra="ignore",
    )

    # ── Output ────────────────────────────────────────────────────────────────
    output_dir: Path = Field(
        default=Path.home() / "Downloads" / "yamd",
        description="Default directory where output files are saved.",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Logging verbosity. One of DEBUG, INFO, WARNING, ERROR.",
    )

    # ── Jobs ──────────────────────────────────────────────────────────────────
    max_concurrent_jobs: int = Field(
        default=3,
        ge=1,
        description="Maximum number of jobs that run simultaneously.",
    )

    # ── Backends ──────────────────────────────────────────────────────────────
    ffmpeg_bin: str = Field(
        default="",
        description="Absolute path to ffmpeg binary. Empty = rely on PATH.",
    )

    # ── HTTP API ──────────────────────────────────────────────────────────────
    api_host: str = Field(
        default="127.0.0.1",
        description="Host the HTTP API server binds to.",
    )
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Port the HTTP API server listens on.",
    )
    api_key: str = Field(
        default="",
        description="Bearer token for API auth. Empty string disables auth.",
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        normalised = v.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalised not in valid:
            raise ValueError(
                f"Invalid log_level {v!r}. Must be one of: {', '.join(sorted(valid))}."
            )
        return normalised

    @field_validator("output_dir")
    @classmethod
    def _expand_output_dir(cls, v: Path) -> Path:
        """Resolve ~ and relative paths to absolute paths."""
        return v.expanduser().resolve()

    # ── Convenience helpers ───────────────────────────────────────────────────

    @property
    def log_level_int(self) -> int:
        """Return the log level as a Python logging integer constant."""
        return logging.getLevelName(self.log_level)

    @property
    def ffmpeg_executable(self) -> str:
        """Return the ffmpeg binary to invoke: explicit path or bare name."""
        return self.ffmpeg_bin if self.ffmpeg_bin else "ffmpeg"

    @property
    def api_auth_enabled(self) -> bool:
        """True when the HTTP API requires a bearer token."""
        return bool(self.api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings, cached for the lifetime of the process.

    In tests, call ``get_settings.cache_clear()`` before patching env vars
    to ensure a fresh Settings object is constructed.
    """
    return Settings()