"""The store seam (Delivery 2 §2): one queue protocol, two backings.

`Store` is the SQLite class (Delivery 1's `harness/store.py`, moved verbatim to `sqlite.py`) so every
existing call site and test keeps working. `StoreProtocol` is the structural type the stages are
written against; `GitHubStore` (Delivery 2) is the second implementation.
"""

from __future__ import annotations

from harness.store.sqlite import STATES, StageRun, Store, WorkItem

SqliteStore = Store

__all__ = ["STATES", "SqliteStore", "StageRun", "Store", "WorkItem"]
