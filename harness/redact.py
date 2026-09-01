"""Secret redaction (SPEC §5.8) and the I-8 write guard every artifact write routes through."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from harness.errors import WriteOutsideAllowedRoots

REDACTION = "[REDACTED]"

# --- patterns (SPEC §5.8) ---------------------------------------------------
# Vendor prefixes are case-sensitive on purpose: lower-casing them would match ordinary prose.

_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
_GITHUB_TOKEN = re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")
_GITHUB_PAT = re.compile(r"github_pat_[A-Za-z0-9_]{40,}")
_AWS_ACCESS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
# The whole match goes, key name included (B49); the rest of the line is untouched (B50).
_KEYED_VALUE = re.compile(r"(?i)(authorization|api[_-]?key|secret|token|password)\s*[:=]\s*\S+")
# Applied before _KEYED_VALUE so an auth header carrying ``Bearer <tok>`` loses the token, not
# just the scheme word; the keyed pass then swallows what is left of the header.
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def redact(text: str) -> str:
    """Replace every §5.8 pattern, and every live secret value, with ``[REDACTED]``."""
    if not text:
        return text
    from harness import config as _config  # lazy: config is imported by modules redact serves

    out = _PRIVATE_KEY_BLOCK.sub(REDACTION, text)
    out = _ANTHROPIC_KEY.sub(REDACTION, out)
    out = _GITHUB_PAT.sub(REDACTION, out)
    out = _GITHUB_TOKEN.sub(REDACTION, out)
    out = _AWS_ACCESS_KEY.sub(REDACTION, out)
    out = _BEARER.sub(REDACTION, out)
    out = _KEYED_VALUE.sub(REDACTION, out)

    # Longest first, so a value that contains another value does not leave a tail behind.
    for value in sorted(_config.secret_values(), key=len, reverse=True):
        if value in out:
            out = out.replace(value, REDACTION)
    return out


def redact_json(obj: object) -> object:
    """Walk nested dicts, lists and tuples, redacting every string leaf (B51)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {key: redact_json(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [redact_json(value) for value in obj]
    if isinstance(obj, tuple):
        return tuple(redact_json(value) for value in obj)
    return obj


# --- I-8 write guard --------------------------------------------------------

_WRITE_ROOTS: tuple[Path, ...] = ()


def set_write_roots(roots: Sequence[Path]) -> None:
    """Declare the only paths the harness may write to. An empty sequence disables the guard."""
    global _WRITE_ROOTS
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(Path(root).resolve())
        except OSError:
            resolved.append(Path(root).absolute())
    _WRITE_ROOTS = tuple(resolved)


def allowed_roots() -> tuple[Path, ...]:
    """The currently declared write roots, resolved."""
    return _WRITE_ROOTS


def _is_allowed(path: Path) -> bool:
    if not _WRITE_ROOTS:
        return True
    try:
        target = Path(path).resolve()
    except OSError:
        target = Path(path).absolute()
    for root in _WRITE_ROOTS:
        if target == root:
            return True
        if target.is_relative_to(root):
            return True
    return False


def guarded_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` after checking the write roots. Does **not** redact."""
    target = Path(path)
    if not _is_allowed(target):
        roots = ", ".join(str(root) for root in _WRITE_ROOTS) or "(none)"
        raise WriteOutsideAllowedRoots(f"refusing to write {target}; allowed roots: {roots}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_redacted(path: Path, text: str) -> None:
    """Redact then write. No unredacted byte ever reaches the filesystem (B52)."""
    guarded_write(path, redact(text))
