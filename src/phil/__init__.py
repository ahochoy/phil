from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

DIST_NAME = "phil-agent"


def _installed_version() -> str:
    """The installed distribution's version, which comes from pyproject.toml. "0+unknown" when
    Phil runs from a source tree that was never installed."""
    try:
        return _dist_version(DIST_NAME)
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _installed_version()
