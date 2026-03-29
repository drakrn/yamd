"""
Smoke test — verifies the project scaffold is correctly installed.
If this passes, the package is importable and pytest is wired up.
"""


def test_package_is_importable() -> None:
    import yamd  # noqa: F401


def test_version_is_defined() -> None:
    import yamd

    assert hasattr(yamd, "__version__")
    assert isinstance(yamd.__version__, str)