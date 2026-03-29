"""Shared pytest fixtures for the yamd test suite."""
 
import pytest
 
 
@pytest.fixture()
def tmp_output_dir(tmp_path):
    """A temporary directory that acts as YAMD_OUTPUT_DIR in tests."""
    output = tmp_path / "output"
    output.mkdir()
    return output
 