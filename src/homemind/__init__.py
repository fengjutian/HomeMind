"""HomeMind — a self-hosted family AI assistant."""

from homemind.compat import prepare_environment
from homemind.version import HOMEMIND_VERSION

prepare_environment()

__version__ = HOMEMIND_VERSION

__all__ = ["HOMEMIND_VERSION", "__version__", "prepare_environment"]
