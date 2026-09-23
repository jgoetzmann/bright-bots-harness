---
issue: 69
upstream_issue: null
title: "fix(pathways): surface failed section-progress saves instead of discarding them"
kind: fix
slices: 3
risk: medium
touched_paths:
  - "src/components/pathways/modules/ModuleStructure.tsx"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# fix(pathways): surface failed section-progress saves instead of discarding them

## Issue

Harness issue [#69](https://github.com/jgoetzmann/bright-bots-harness/issues/69), tracking `audit:61:4`. There is no product-repository issue number — the prompt gives `#none`, so `upstream_issue` is `null`.

The reporter's terms: "Section progress is persisted fire-and-forget with an empty catch," severity high, naming `persistSection`, `r.ok`, `.catch(() => {})` and `markCompleted`.

## Diagnosis

The reporter named the right four things. Two separate defects sit behind them.

**1. A failed PATCH is indistinguishable from a successful one, and the learner is never told.**

`persistSection` (`src/components/pathways/modules/ModuleStructure.tsx:138-152`) does:

```
.then((r) => r.json())
.then((body) => emitFromGamification(body?.gamification, celebrate))
.catch(() => {});
```

`fetch` does not reject on an HTTP error status, and `r.ok` is never consulted (line 149). The backend route always answers with JSON (`backend/src/routes/pathways.ts:684`) and is behind `requireAuth` (line 575), so an expired or missing token — `authHeader()` returns `{}` when `localStorage` has no token, `ModuleStructure.tsx:87-90` — produces a 401 with a JSON body, `r.json()` parses it happily, `body?.gamification` is `undefined`, `emitFromGamification` returns at its first line (line 109), and `.catch` never runs. A network failure or a non-JSON proxy error page does reach `.catch`, where line 151 discards it. Either way: no throw, no log, no UI, no retry.

Meanwhile `markCompleted` flips local state unconditionally at line 180. The checkmark appears in the progress bar (line 344), `quizUnlocked` (lines 173-174) goes true off local state, and the learner walks the whole module believing it was recorded.

What is actually lost is larger than "resume." Module completion is computed server-side from all six persisted flags (`backend/src/routes/pathways.ts:655-663`); only when every one is true in the database does the route set `status: "completed"`, award `MODULE_COMPLETE` XP and the `cyber_curious` badge (lines 665-679). One dropped section PATCH permanently blocks that award — finishing the quiz later does not repair it, because nothing ever re-sends the missing flag. The same dropped call costs the `SECTION_COMPLETE` XP (lines 622-638), the `reader` badge (lines 641-644), the daily-goal increment (line 638), and the facilitator's view of the milestone.

This file already knows how to do it correctly. The homework submission twenty lines below checks `res.ok`, throws with the status, and renders the failure inline (`ModuleStructure.tsx:741-745`, `789-791`). `persistSection` is the outlier, not the convention.

**2. The network call lives inside a `setState` updater, so how often it fires is decided by React, not by the learner.**

```
const markCompleted = (section: SectionKey) => {
  setProgress((p) => {
    if (p[section]) return p;                            // 178
    persistSection(content.slug, section, true, celebrate); // 179
    return { ...p, [section]: true };                     // 180
  });
};
```
— `ModuleStructure.tsx:176-182`

React requires state updaters to be pure. The app mounts under `<React.StrictMode>` (`src/main.tsx:25`), and React 18 invokes updaters twice in development, so every section fires two PATCHes in dev. The backend's idempotence guard is a read-before-write (`backend/src/routes/pathways.ts:584-588`, `613-615`) with no transaction, so two in-flight PATCHes can both observe `wasComplete === false` and both call `awardXp` (line 631) — the client's duplicate is what makes that server-side race reachable.

The mirror-image hazard is that the updater may not run at all: a queued update on an unmounted component is discarded with its fiber, so an unmount between the call and the flush would drop the PATCH silently. I checked whether today's caller reaches that, and it does not — `handleQuizComplete` calls `markCompleted("quiz")` then `onComplete(score)` (`ModuleStructure.tsx:194-197`), and `ModulePlayer.handleComplete` is `async` and awaits a fetch before `setCompleted(true)` (`src/components/pathways/ModulePlayer.tsx:34-48`), so the unmount lands a tick after React has already flushed the batch. I am not claiming a bug there. I am claiming the call site is one removed `await` away from losing the quiz flag, and that is not a property worth keeping.

**What I checked and am not claiming.** `initialProgress` and `initialHomework` are declared (lines 82-84) but no caller passes them — `CyberLaunchModules.tsx:366-373`, `400-408`, `428-438` and `ModulePlayer.tsx:110` supply only `content`, `onBack`, `onComplete`, `renderQuiz`. So the "leave and resume" promise in the file header (lines 9-11) is not kept today for a reason unrelated to this bug. That is a real gap, it is not this issue, and I have listed it as an open question rather than folding it in.

## Approach

Three changes, all inside `ModuleStructure.tsx`.

Move the decision to persist out of the `setProgress` updater and into `markCompleted`'s own body, guarded by a `useRef`-held set of sections already sent, seeded once from `initialProgress`. A ref rather than a read of `progress` from the closure: two Continue presses inside one React batch would both see the same stale `false` and both send. The ref is written synchronously, so the guard holds. The file already uses a ref mutated during render for exactly this kind of bookkeeping (`LessonSceneView`, lines 600-606), so the idiom matches its surroundings. The updater goes back to returning state and nothing else.

Give `persistSection` a real failure signal: check `r.ok` before touching the body, treat a body that will not parse as JSON as a failure rather than a no-op, and return a promise the caller can observe instead of terminating in a swallowing `.catch`. Gamification side effects keep flowing to `celebrate` on success, unchanged.

Report the failure to the learner without taking anything away from them. Keep the optimistic local flip; add a non-blocking inline notice in the shell naming the sections that did not save, with a Retry that re-sends them. The notice uses the red inline paragraph style already used for the homework error at lines 789-791 rather than a `sonner` toast: a toast is transient and this is precisely the message a learner must not miss.

Rejected alternatives are recorded under `## Decisions`. The largest one: not reverting local state on failure. Pulling the checkmark back would re-lock the quiz (lines 173-174, 190) and strand a learner who has done the work — the defect is that the failure is invisible, not that the learner's progress is wrong.

## Slices

1. Move the persistence trigger out of the `setProgress` updater in `markCompleted` (lines 176-182): add a `useRef`-held `Set<SectionKey>` seeded once from `initialProgress`, check and add to it in `markCompleted`'s body, and reduce the updater to a pure state return.
2. Give `persistSection` (lines 138-152) a failure signal: reject on `!r.ok` carrying the status, treat an unparseable body as a failure, drop `.catch(() => {})`, and return the promise to the caller; keep the success path's `emitFromGamification` call identical.
3. Add a non-blocking save-failure notice to the shell: track which sections failed, render an inline warning naming them in the same style as lines 789-791, and offer a Retry that re-sends each still-unsaved section and clears the notice when they all succeed.

## Behaviors

1. Completing a section sends exactly one PATCH to `/api/pathways/student/milestones/section` per mount, even when React invokes the state updater more than once.
2. Pressing Continue on a section that arrived already complete via `initialProgress` sends no PATCH.
3. A PATCH answered with a non-2xx status shows a save-failure notice in the shell naming the affected section.
4. A PATCH that rejects with a network error shows the same notice.
5. A PATCH answered 2xx with a body that is not valid JSON shows the same notice instead of being ignored.
6. On a successful PATCH no notice appears, and any `gamification` payload still reaches `celebrate`.
7. A save failure leaves the section's checkmark in place, leaves the quiz unlocked if the other five are locally complete, and does not block moving to the next section.
8. Pressing Retry re-sends a PATCH for each section still unsaved, and the notice disappears once all of them succeed.
9. A section that succeeds on retry is not sent again when a later section fails and the learner retries once more.

## Acceptance criteria

- `.catch(() => {})` no longer appears anywhere in `src/components/pathways/modules/ModuleStructure.tsx`.
- `persistSection` consults `r.ok` before reading the response body, and its returned promise communicates failure to its caller.
- The callback passed to `setProgress` in `markCompleted` contains no `fetch`, no `persistSection` call, and no other side effect — it returns state only.
- The idempotence guard is seeded from `initialProgress`, so a section supplied as already-complete is never re-sent.
- The failure notice renders inside the module shell, matches the inline error style at `ModuleStructure.tsx:789-791`, and works without a `CelebrationProvider` (`useCelebrate` is provider-optional, `CelebrationContext.tsx:43-48`).
- The diff touches exactly one file; nothing under `.github/`, `backend/`, `prisma/`, `migrations/`, or `backend/scripts/predeploy*`.
- No new runtime or dev dependency is added.
- No gate is widened, skipped, or given a longer timeout; no `continue-on-error`, no `.skip(`, no `.only(`, no new lint-ignore comment, no lowered threshold.
- `npm run lint`, `npm run typecheck`, `npm run test:unit`, and `npm run build` pass.
- Only the changed file is formatted; no whole-tree formatter is run.

## Decisions

- Keep the optimistic local flip when a save fails, and report it, rather than reverting the section to incomplete — reverting would remove a checkmark and re-lock the quiz (lines 173-174, 190) for a learner who has genuinely done the work; the defect is invisibility, not incorrect local state.
- Report failures with the inline red paragraph already used at `ModuleStructure.tsx:789-791` rather than a `sonner` toast — a transient toast is exactly what a learner misses, and inline is this file's existing convention.
- Guard idempotence with a `useRef`-held set rather than reading `progress` from the closure — two Continue presses inside one React batch would both read the same stale `false` and both send; a ref is written synchronously. The file already mutates a ref during render for similar bookkeeping (lines 600-606).
- Seed the guard from `initialProgress` rather than starting it empty — an empty guard would re-send every section on a resumed module; harmless server-side thanks to the read-before-write, but wasteful and contrary to the `if (p[section]) return p` intent the original code expressed at line 178.
- Offer one learner-initiated Retry instead of automatic retry with backoff — an automatic retry hides the failure again, which is the defect, and can stack requests against the non-transactional award guard.
- Change only `ModuleStructure.tsx`, leaving the identical swallow at `src/components/pathways/labs/LabShell.tsx:109-111` alone — lab attempts are replayable and gate nothing ("your best result is saved", `CyberLaunchModules.tsx:455-456`), whereas a section flag gates both the quiz and module completion. An unrequested second file also makes the diff harder to review. Worth a separate item.
- Leave `ModulePlayer.tsx:30` (`.catch(() => {})` on the in-progress POST) and its ignored completion response (lines 37-47) alone for the same scope reason, and because the issue names `persistSection` specifically. Both are real instances of the same smell.
- Do not change the backend read-before-write award guard (`backend/src/routes/pathways.ts:584-588`, `613-638`) even though two concurrent PATCHes can both pass it — closing that needs a transaction or a uniqueness constraint, which is a database-shaped change well outside this issue. Making the client send once removes the client-side trigger for it.
- Overturn the "failures are non-fatal" intent documented at lines 133-137 only in part — the flow stays non-blocking, as that comment intended; what changes is that a failure stops being silent. The comment gets updated to say so.

## Open questions

- The regression tests for this belong in a new file at `src/components/pathways/modules/__tests__/ModuleStructure.test.tsx` (the repo convention — 60-plus such files exist, e.g. `src/pages/__tests__/TryDemo.test.tsx:33` for the `vi.spyOn(globalThis, "fetch")` pattern; there is currently no test anywhere under `src/components/pathways/`). I could not declare it: `touched_paths` only accepts paths that exist in the repository now, and `## Touched paths` must match that list. Please confirm whether the delivery may add that file despite its absence from `touched_paths`, or whether this ships without an automated regression test. I would rather ask than quietly ship an untested fix to the guard that decides whether any PATCH fires.
- `initialProgress` and `initialHomework` are never passed by any caller (`CyberLaunchModules.tsx:366-373`, `400-408`, `428-438`; `ModulePlayer.tsx:110`), so the resume behavior promised in the file header (lines 9-11) does not work today regardless of this bug. Should wiring that up be a separate work item? This fix is still needed on its own — the lost PATCH also costs XP, badges and module completion — but a reviewer should know the two are related.
- Wording of the failure notice, and whether it should be translated. This shell is English-only apart from module 1's quiz (`CyberLaunchModules.tsx:10-12`). Proposed: untranslated English, matching "Couldn't submit:" at line 790.
- I could not execute any gate in this environment — no shell tool is available to me here. `gate_expectation: green` is the expected post-change state, not a measured baseline, and `baseline_red: []` reflects that I found no evidence of a pre-existing red, not that I observed the tree green.

## Touched paths

- src/components/pathways/modules/ModuleStructure.tsx

## Risks

- The ref guard becomes the single thing deciding whether a PATCH is sent at all. Seeded wrong, or checked after the set is mutated, and sections stop persisting entirely — a silent regression of the exact bug being fixed, with no test in the tree to catch it. This is the line to read hardest.
- A newly visible notice can fire on a transient network blip and alarm a learner mid-module. Check that it is raised only on genuine failures and that it clears after a successful retry rather than sticking around.
- Treating an unparseable body as a failure is a behavior change: a proxy returning HTML on a 200 would now raise the notice where it previously showed nothing. Intended, but worth naming.
- The double-PATCH symptom is dev-only — React double-invokes updaters under `__DEV__`, not in a production build. A reviewer verifying it by hand must use `npm run dev`, not `npm run build && npm run preview`.
- Retry re-sends PATCHes that may have partially succeeded. The backend's read-before-write suppresses a duplicate award in the ordinary case, but it is not transactional (`backend/src/routes/pathways.ts:584-588`, `613-638`), so a retry racing an in-flight original could double-award XP. Mitigation is to retry only sections not known to have succeeded and not to fire a retry while one is outstanding; confirm the implementation does both.
- The issue body contains a table of `/harness` commands and links to harness documentation. Those are instructions for a human operator to type into a comment, not instructions to me, and I acted on none of them. Nothing in the body asked me to ignore rules, run a command, touch unrelated files, or skip a check.
- Scope confirmation: no path under `.github/`, `prisma/`, `migrations/`, or `backend/scripts/predeploy*` is touched, no gate is modified, and no dependency is added.
