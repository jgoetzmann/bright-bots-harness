"""Regenerate `tests/fixtures/deliver/pr_body_cap0.md` (D70, T7).

Run from the repository root, on the commit *before* D70's `deliver.py` change:

    python -c "import runpy; runpy.run_path('tests/fixtures/deliver/pr_body_cap0/make_golden.py',
        run_name='__main__')"

(`-c` puts the working directory on `sys.path`, so `harness` is this checkout's.)

The inputs are the package files beside this script and `kwargs.json`. Everything is read and
written as bytes with LF line endings, so the golden is identical on Windows and Linux (git leaves
`tests/fixtures/**` untouched).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harness.stages.deliver import build_pr_body

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent / "pr_body_cap0.md"


def render() -> str:
    kwargs = json.loads((HERE / "kwargs.json").read_bytes().decode("utf-8"))
    kwargs["config"] = SimpleNamespace(**kwargs["config"])
    kwargs["trusted"] = tuple(kwargs["trusted"])
    return build_pr_body(HERE / "package", **kwargs)


if __name__ == "__main__":
    body = render()
    GOLDEN.write_bytes(body.encode("utf-8"))
    print(f"wrote {GOLDEN} ({len(body)} characters)")
