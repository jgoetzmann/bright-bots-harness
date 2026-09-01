# Decisions — Delivery 1 build

Recorded reasons for every place the implementation goes beyond, or reads between the lines of,
`HARNESS-SPEC.md` v1.1. §0 of the spec asks for exactly this file.

| # | Decision | Why |
|---|---|---|
| D1 | A dependency-install step (`npm ci` at the root and in `backend/`, only where a lockfile exists) runs before the baseline gate sequence. | On a fresh clone every one of the seven gates is vacuously red (`eslint is not recognized`, `npx` fetching a random `prisma@8-rc`). The product's own pre-push hook assumes an installed tree (`--skip-install`). The step is recorded in `EVIDENCE.md` under its own heading and in `manifest.json` it is absent from `gates`; the gate sequence itself is unchanged (§5.11). |
| D2 | A post-change sequence whose only reds are pre-existing is recorded as "no new failures versus baseline; NOT green", never as "green". | §5.9.3 says baseline reds are not attributed to the change; it does not say they are cured. Calling the sequence green would be the "silently green" outcome A13 forbids. |
| D3 | `bash scripts/check-prisma-drift.sh` is red on the untouched tree on this host (it wants a database). | §12 Q1 is open. The red is carried as pre-existing; nothing is loosened. Answering Q1 (a throwaway Postgres) would turn it green. |
| D4 | `governor.Authorization` keeps its spec-frozen name although `HARNESS-REVIEW.md` R2.3 greps `harness/` for the literal `Authorization`. | §5.3 freezes the dataclass name. R2.3's intent is the HTTP header; the only hits are the dataclass and its four uses in `governor.py`, and `gh.py` sets no such header (B31). The reviewer should read those hits, as R2.7 already instructs for its own grep. |
| D5 | On Windows, `doctor` and the CLI runner resolve `claude`/`npm`/`npx` through `shutil.which` before spawning. | The entry points are `.CMD` shims and `CreateProcess` will not find them by bare name. Injected spawns still receive the unresolved argv so B25 stays testable. |
| D6 | Halt is honoured inside `implement` (after install, after baseline, after the post-change gates, before each diagnose cycle), not only at stage boundaries. On halt the clone is released and the item reset to `approved`. | A10 and R7.7. A halt that waits for a 30-minute gate run to finish is not a kill switch. |
| D7 | `Config` carries two extra trailing fields, `github_token_present` and `github_token_shape_ok`, and `config.py` exposes `environ_snapshot()`, `secret_values()`, `read_secret()`. | I-4 confines `os.environ` to `config.py`, yet the CLI runner must build a child environment (B26), the redactor must scrub configured secret values (§5.8), and `identity` must detect presence without reading the value (§5.12). These are the narrowest seams that satisfy all three. |
| D8 | The session budget lives in memory on the `Governor`, per process. | §5.3 gives no persistence for it and every command is a fresh process; the weekly budget is the durable one and is in the store. |
| D9 | Directed discover leaves the row in `discovered` as created; `packaged → shipped` is accepted by the store's state machine. | §5.2.2 lists the pair and B10 requires every listed pair to be accepted. The stage that would use it does not exist, which is the actual Tier-2 guard (§9 I-1). |
| D10 | The two secret keys (`HARNESS_GITHUB_TOKEN`, `ANTHROPIC_API_KEY`) are optional in `.env`; every other key is required. | B79 needs "absent" to be a legal state; B3 needs typo'd budget keys to fail. |
| D11 | `redact()` replaces the whole match of the generic `key: value` pattern, key name included, and also scrubs `Bearer <token>`. | B49 says the pattern is replaced with `[REDACTED]`. The spec's own `\S+` stops at the space after `Bearer`, which would leave the token behind. |

Open questions carried forward unchanged from §12: Q1 (local throwaway Postgres), Q2 (issue comments /
Tier 1). The collision re-check before implement (Q2's stop-gap) is live and blocked a real item during
acceptance (`#801`, claimed by `fix-801/ci-shell-gate-isolation`).
