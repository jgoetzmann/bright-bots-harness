---
issue: 4
upstream_issue: 633
title: "chore(activities): delete orphaned SequenceDragDropGame component"
kind: chore
slices: 2
risk: low
touched_paths:
  - "src/components/activities/SequenceDragDropGame.tsx"
  - "src/components/activities/SequenceDragDropGame.test.tsx"
depends_on: []
estimated_turns: 10
gate_expectation: green
baseline_red: []
---

Confirmed both files exist under `src/components/activities/` and are self-referencing only. Now producing the work package.



# chore(activities): delete orphaned SequenceDragDropGame component

## Issue

Harness issue #4, no product-repository issue number (`upstream_issue: none`). The issue body is
literally the text `issue:633`, which does not resolve to any content this harness can read (no
product-repo issue #633 body was supplied). The harness issue title supplies the actual task:
"cleanup: delete orphaned SequenceDragDropGame component." I am treating that title as the
specification, since the body carries no usable information beyond a bare reference.

## Diagnosis

`src/components/activities/SequenceDragDropGame.tsx` (606 lines) defines and default-exports a
React component `SequenceDragDropGame` (`src/components/activities/SequenceDragDropGame.tsx:184`)
that implements a drag-and-drop sequencing minigame using `@dnd-kit`. Its companion test file,
`src/components/activities/SequenceDragDropGame.test.tsx`, imports it at line 2
(`import SequenceDragDropGame from "./SequenceDragDropGame";`) and exercises it in four tests.

The component's own type declares `gameKey: "sequence_drag_drop"`
(`src/components/activities/SequenceDragDropGame.tsx:41`), suggesting it was meant to render for
activities tagged with that key. However, the live game registry,
`src/components/games/gameRegistry.ts:52`, maps that exact key to a different component:

```
sequence_drag_drop: BoostPathPlannerGame,
```

(`src/components/games/gameRegistry.ts:5,32,52`). That mapping is documented as intentional in
`docs/game-audit.md:27`: `"Alias keys in registry: `sequence_drag_drop`, `rhyme_ride_unity`,
`bounce_buds_unity`."`, and `backend/prisma/seed.cjs:589` / `prisma/seed.cjs:589` both note "old
alias: sequence_drag_drop still works via registry" — i.e. content authored with the old
`sequence_drag_drop` key is deliberately routed to `BoostPathPlannerGame`, not to
`SequenceDragDropGame.tsx`.

A repository-wide search confirms `SequenceDragDropGame` is imported nowhere except its own test
file: no reference from `src/pages/ActivityPlayer.tsx`, `src/components/games/gameRegistry.ts`, or
any other component. `ActivityPlayer.tsx` only imports `ActivityHeader`,
`quiz/LegacyListQuiz`, and `quiz/K2InstantFeedbackQuiz` from `src/components/activities/`
(`src/pages/ActivityPlayer.tsx:33-36`) — it does not reference `SequenceDragDropGame`. There is no
barrel/index file re-exporting it, and no dynamic `import()`/`require()` referencing its path.

Conclusion: `SequenceDragDropGame.tsx` is dead code — a component and its dedicated test that
render and pass in isolation but are never reached from any route, registry, or content-driven
render path in production. The functional replacement for its former role is
`BoostPathPlannerGame`, already wired into `gameRegistry.ts`.

Note: three sibling files in the same directory — `GotchaGearsUnityActivity.tsx`,
`RhymeRideUnityActivity.tsx`, `BounceBudsUnityActivity.tsx` — show the same "only imported by
their own test file" pattern. They are out of scope here: the issue names only
`SequenceDragDropGame`, and `unity-rhyme-ride/README.md:117` explicitly documents
`RhymeRideUnityActivity.tsx` as a real wrapper, so I have not verified equivalent orphan status for
that one and will not sweep it in under an issue that names one specific component.

## Approach

Delete the two files that make up the orphaned component and its test:
`src/components/activities/SequenceDragDropGame.tsx` and
`src/components/activities/SequenceDragDropGame.test.tsx`. No other file imports either, so no
import statement anywhere needs updating. This is a pure removal — no behavior change to any
reachable code path, since the component was never reachable.

Alternative considered: leave the file but remove it from some registry — rejected because it is
already absent from every registry; there is nothing to unwire.

Alternative considered: also sweep up `GotchaGearsUnityActivity.tsx` /
`BounceBudsUnityActivity.tsx` since they show the same orphan pattern — rejected, kept out of
`## Touched paths`, because the issue names one component and `RhymeRideUnityActivity.tsx`'s sibling
is documented as live in `unity-rhyme-ride/README.md`, so a blanket sweep risks deleting something
still wired through a path this review did not fully trace. That is a separate, better-scoped
follow-up issue, not this one.

## Slices

1. Delete `src/components/activities/SequenceDragDropGame.tsx`.
2. Delete `src/components/activities/SequenceDragDropGame.test.tsx`.

## Behaviors

1. `SequenceDragDropGame` no longer exists as an importable module anywhere in `src/`.
2. No other component, page, registry, or test references `SequenceDragDropGame` after removal
   (verified by repository-wide search finding zero remaining occurrences of the identifier).
3. The application build and existing test suite behave identically to before the change, since the
   removed component was never rendered from any reachable code path.

## Acceptance criteria

- `src/components/activities/SequenceDragDropGame.tsx` no longer exists in the repository.
- `src/components/activities/SequenceDragDropGame.test.tsx` no longer exists in the repository.
- `grep -r "SequenceDragDropGame" src/` returns no results.
- `npm run typecheck`, `npm run lint`, `npm run build`, and `npm run test:unit` all pass with no new
  failures relative to the pre-change baseline.
- No file under `src/components/games/`, `src/pages/`, or elsewhere is modified — the change is a
  pure deletion of the two named files.

## Decisions

- Treated the harness issue title ("cleanup: delete orphaned SequenceDragDropGame component") as
  the specification, since the supplied issue body (`issue:633`) carries no readable content for
  this harness to diagnose against.
- Scoped the deletion to exactly the component file and its dedicated test file, rejecting a wider
  sweep of the other apparently-orphaned `*UnityActivity` siblings in the same directory, because
  the issue names one component and at least one sibling (`RhymeRideUnityActivity.tsx`) is
  documented elsewhere as a real, in-use wrapper — conflating them risks deleting live code under
  an issue scoped to dead code.
- Verified orphan status via static repository search (imports, dynamic imports, registry maps,
  seed-script comments, docs) rather than relying on the issue's assertion, per the harness rule to
  diagnose from source rather than restate the report.

## Open questions

- Product issue #633 could contain reviewer discussion, screenshots, or a narrower scope than "the
  whole component" (e.g. only removing a partial duplicate) that this harness cannot see since only
  the bare string `issue:633` was supplied as the body. A human should confirm #633's actual content
  matches this diagnosis before merge.
- Whether the other seemingly-orphaned `*UnityActivity` files should be cleaned up in a follow-up
  issue is a judgment call for a human, not decided here.

## Touched paths

- src/components/activities/SequenceDragDropGame.tsx
- src/components/activities/SequenceDragDropGame.test.tsx

## Risks

- The issue body supplied to this harness was the bare text `issue:633`, with no instructions
  addressed to an AI and nothing resembling a prompt-injection attempt — noted here only because
  the task instructions require flagging unusual issue-body content, and a bare cross-reference
  with no elaboration is unusual enough to call out.
- Removing a component always carries a small risk that some not-yet-found reference exists (e.g.
  behind a feature flag, in a not-yet-committed branch, or loaded via a build step this review did
  not inspect). The repository-wide grep and import-graph check in `## Diagnosis` did not surface
  any such reference; a reviewer should re-run the same search on the final diff before merge.
- If product issue #633 turns out to ask for something narrower than full deletion (e.g. marking
  the component `@deprecated` first, or a staged removal), this work package would need revision —
  flagged under `## Open questions` above.
