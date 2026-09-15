## What

<!-- One or two sentences. -->

## Why

<!-- The issue or decision this serves. Link it. -->

## Kind

- [ ] Proposal (`proposals/<id>-<slug>.md`): merging it approves the work (gate 1)
- [ ] Harness code
- [ ] Governance: `.harness/`, `.github/`, `prompts/`, `harness/gates.py`, `harness/packager.py`,
      `harness/redact.py`, `harness/verify_pin.py`, `harness/trust.py` or `harness/keywords.py`
      (CODEOWNERS review required)
- [ ] Documentation only

## Checklist

- [ ] `selftest` is green on both `ubuntu-latest` and `windows-latest`
- [ ] No secret, token, `.env` content or private data appears in the diff
- [ ] If a pinned file changed (`harness/gates.py`, `harness/packager.py`, `harness/redact.py` or
      `prompts/**`), `.harness/PIN` was regenerated with `python -m harness.verify_pin --write`, and
      the reason is recorded in `DECISIONS.md`
- [ ] `.harness/config.json` changes touch operational knobs only
- [ ] New `.harness/trust.txt` lines are the ones `harness trust line <login> --level N` prints
