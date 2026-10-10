"""HomeMind — a self-hosted family AI assistant."""

from importlib.metadata import PackageNotFoundError, version

from homemind.compat import prepare_environment

prepare_environment()

__all__ = ["prepare_environment"]

try:
    __version__ = version("homemind")
except PackageNotFoundError:
    __version__ = "0.0.0"
