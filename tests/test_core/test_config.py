"""Tests for core/config.py."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yamd.core.config import Settings, get_settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_settings(**kwargs) -> Settings:
    """Construct a Settings object with specific values, bypassing env / .env.

    We pass _env_file=None to prevent pydantic-settings from reading any
    real .env file on disk, then override fields directly via kwargs.
    """
    return Settings(_env_file=None, **kwargs)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache before and after every test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_default_output_dir_is_absolute() -> None:
    s = make_settings()
    assert s.output_dir.is_absolute()


def test_default_log_level() -> None:
    s = make_settings()
    assert s.log_level == "INFO"


def test_default_max_concurrent_jobs() -> None:
    s = make_settings()
    assert s.max_concurrent_jobs == 3


def test_default_api_host() -> None:
    s = make_settings()
    assert s.api_host == "127.0.0.1"


def test_default_api_port() -> None:
    s = make_settings()
    assert s.api_port == 8000


def test_default_api_key_is_empty() -> None:
    s = make_settings()
    assert s.api_key == ""


def test_default_ffmpeg_bin_is_empty() -> None:
    s = make_settings()
    assert s.ffmpeg_bin == ""


# ── Env var overrides ─────────────────────────────────────────────────────────

def test_output_dir_from_env(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"YAMD_OUTPUT_DIR": str(tmp_path)}, clear=False):
        s = Settings(_env_file=None)
    assert s.output_dir == tmp_path.resolve()


def test_log_level_from_env() -> None:
    with patch.dict(os.environ, {"YAMD_LOG_LEVEL": "DEBUG"}, clear=False):
        s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"


def test_max_concurrent_jobs_from_env() -> None:
    with patch.dict(os.environ, {"YAMD_MAX_CONCURRENT_JOBS": "5"}, clear=False):
        s = Settings(_env_file=None)
    assert s.max_concurrent_jobs == 5


def test_api_key_from_env() -> None:
    with patch.dict(os.environ, {"YAMD_API_KEY": "secret-token"}, clear=False):
        s = Settings(_env_file=None)
    assert s.api_key == "secret-token"


# ── Validators ────────────────────────────────────────────────────────────────

def test_log_level_normalised_to_uppercase() -> None:
    s = make_settings(log_level="debug")
    assert s.log_level == "DEBUG"


def test_invalid_log_level_raises() -> None:
    with pytest.raises(Exception, match="Invalid log_level"):
        make_settings(log_level="VERBOSE")


def test_max_concurrent_jobs_below_one_raises() -> None:
    with pytest.raises(Exception):
        make_settings(max_concurrent_jobs=0)


def test_api_port_out_of_range_raises() -> None:
    with pytest.raises(Exception):
        make_settings(api_port=99999)


def test_output_dir_tilde_is_expanded() -> None:
    s = make_settings(output_dir=Path("~/some/path"))
    assert "~" not in str(s.output_dir)
    assert s.output_dir.is_absolute()


# ── Convenience properties ────────────────────────────────────────────────────

def test_log_level_int_returns_integer() -> None:
    import logging
    s = make_settings(log_level="WARNING")
    assert s.log_level_int == logging.WARNING


def test_ffmpeg_executable_default_is_ffmpeg() -> None:
    s = make_settings()
    assert s.ffmpeg_executable == "ffmpeg"


def test_ffmpeg_executable_uses_explicit_path() -> None:
    s = make_settings(ffmpeg_bin="/usr/local/bin/ffmpeg")
    assert s.ffmpeg_executable == "/usr/local/bin/ffmpeg"


def test_api_auth_enabled_when_key_set() -> None:
    s = make_settings(api_key="my-secret")
    assert s.api_auth_enabled is True


def test_api_auth_disabled_when_key_empty() -> None:
    s = make_settings(api_key="")
    assert s.api_auth_enabled is False


# ── Caching ───────────────────────────────────────────────────────────────────

def test_get_settings_returns_same_instance() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_get_settings_cache_clear_returns_new_instance() -> None:
    a = get_settings()
    get_settings.cache_clear()
    b = get_settings()
    assert a is not b