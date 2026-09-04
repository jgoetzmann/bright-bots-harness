# Delivery documents

The four documents the harness was built and accepted against. Nothing resolves them at runtime,
so they live here rather than at the root. Amendments are never made by editing them: each is a
row in the root `DECISIONS.md` (D31, D34), so the frozen text stays comparable to what was signed.

| File | What it is |
|---|---|
| `HARNESS-SPEC.md` | Frozen spec. Delivery 1: store, governor, runner, stages, invariants I-1…I-10, behaviors B1–B86 |
| `HARNESS-REVIEW.md` | Runnable review protocol for Delivery 1. Run from the repository root |
| `DELIVERY-2-HANDOFF.md` | Frozen spec. Delivery 2: GitHub mode, trust gate, workflows, local mode, B100–B150 |
| `DELIVERY-2-REVIEW.md` | Runnable review protocol for Delivery 2. Run from the repository root |

## Reading order

1. `HARNESS-SPEC.md` — start here; Delivery 2 assumes all of it and supersedes none of it.
2. `DELIVERY-2-HANDOFF.md` — what Delivery 2 added, and the §3 file map. That map still shows
   these four files at the repository root; D34 is the amendment that moved them here.
3. Root `DECISIONS.md` — every ruling D1–D34, including the amendments to both specs above.
4. The two review protocols, only when you are actually reviewing. Work them in order, and treat
   `DELIVERY-2-REVIEW.md` §D2-R12 as the instruction to re-run `HARNESS-REVIEW.md` in full.

Delivery 3 shipped no document here: its design is `.fullsend/RUN-DECISIONS-D3.md` (gitignored,
local only) and its rulings are D31–D33 in `DECISIONS.md`.
