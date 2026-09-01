"""The package stage: build the review package and move the item to ``packaged``.

No model call and no spend. Everything the package contains already exists on disk by the time this
runs; this stage is assembly, and the assembling lives in :mod:`harness.packager`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from harness.clone import Lease
from harness.context import Context
from harness.errors import HarnessError
from harness.halt import check_halt
from harness.packager import build

__all__ = ["package"]

log = logging.getLogger("harness")


def package(ctx: Context, item_id: int, lease: Lease) -> Path:
    """Delegate to the packager, then transition ``implementing -> packaged``."""
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")

    path = build(ctx, item_id, lease)

    ctx.store.transition(item_id, "packaged", reason="review package built")
    ctx.store.append_event(item_id, "info", f"packaged into {path}")
    ctx.record_decision(
        f"packaged item {item_id} into {path} from branch {lease.branch} "
        f"at base {lease.base_sha}; the package is self-contained and needs no credential to verify"
    )
    log.info("packaged item %s -> %s", item_id, path)
    return path
