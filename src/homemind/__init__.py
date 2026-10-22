"""HomeMind — a self-hosted family AI assistant."""

from homemind.compat import prepare_environment
from homemind.version import HOMEMIND_VERSION

# NOTE: the environment bridge is deliberately *not* applied on import.
# ``prepare_environment()`` rewrites ``os.environ`` (HOMEMIND_* -> OCTOP_* and
# the data root), and doing that as an import side effect leaks into every
# other library and test process that merely imports this package. The entry
# points (``homemind.cli``, ``homemind.launch``) call it instead.
__version__ = HOMEMIND_VERSION

__all__ = ["HOMEMIND_VERSION", "__version__", "prepare_environment"]
