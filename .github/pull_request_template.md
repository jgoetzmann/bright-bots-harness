## What

<!-- One or two sentences. -->

## Why

<!-- The issue or decision this serves. Link it. -->

## Kind

- [ ] Proposal (`proposals/<issue>-<slug>.md`) — merging this **is** approval (gate 1). Front matter was validated by `harness propose`; the implement workflow picks the item up on merge.
- [ ] Harness code
- [ ] Governance (`.harness/`, `.github/`, `prompts/`, `harness/gates.py`, `harness/redact.py`) — CODEOWNERS review required
- [ ] Documentation only

## Checklist

- [ ] `selftest` is green on both `ubuntu-latest` and `windows-latest`
- [ ] No secret, token, `.env` content, or private data appears in the diff
- [ ] If a pinned file changed (`harness/gates.py`, `harness/packager.py`, `harness/redact.py`, `prompts/**`), `.harness/PIN` was regenerated with `python -m harness.verify_pin --write` and the reason is recorded in `DECISIONS.md`
- [ ] `.harness/config.json` changes touch operational knobs only — never the gate sequence, the redaction patterns, or the proposal schema (B112)
- [ ] `.harness/trust.txt` changes name real GitHub handles, one per line
