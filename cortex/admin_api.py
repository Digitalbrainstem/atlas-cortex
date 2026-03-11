"""Backward-compat shim — admin API moved to cortex.admin package.

Import from cortex.admin instead. This shim re-exports key symbols
so existing tests that mock cortex.admin_api._db still work.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "cortex.admin_api is deprecated — use cortex.admin instead",
    DeprecationWarning,
    stacklevel=2,
)

from cortex.admin import router  # noqa: F401
from cortex.admin.helpers import _db, _rows, _row  # noqa: F401
