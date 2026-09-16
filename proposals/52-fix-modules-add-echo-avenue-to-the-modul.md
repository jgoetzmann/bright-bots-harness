---
issue: 52
upstream_issue: 862
title: "fix(modules): add Echo Avenue to the Modules-page Set-3 presentation maps"
kind: fix
slices: 2
risk: low
touched_paths:
  - "src/pages/Modules.tsx"
  - "src/pages/__tests__/Modules.test.tsx"
depends_on: []
estimated_turns: 12
gate_expectation: green
baseline_red: []
---

# fix(modules): add Echo Avenue to the Modules-page Set-3 presentation maps

## Issue

[Bright-Bots-Initiative/brightboost#862](https://github.com/Bright-Bots-Initiative/brightboost/issues/862) — `src/pages/Modules.tsx` knows `k2-stem-track-maker` but not `k2-stem-echo-avenue`, so the module is missing from `MODULE_THUMBNAILS`, `MODULE_ORDER` and `SLUG_TO_SET3_ID`. The reporter's terms: the module is seeded, registered and gated, so nothing is user-visible today, but "the moment Set 3 is ungated (#676) Echo Avenue renders half-dressed."

## Diagnosis

The reporter is right about the defect and its location; three details in the report are stale or imprecise, and one stated consequence does not hold.

Echo Avenue is complete in every canon-owning source: `src/constants/stemSets.ts:66` (`HIDDEN_MODULE_SLUGS`), `:105` (`STEM_SET_3_NAMES`), `:110` (`STEM_SET_3_STRANDS` → `"AI"`), `:116` (`STEM_SET_3_MODULE_SLUGS`); `shared/progression/stemSetIds.ts:72`; `prisma/seed.cjs:2562-2567` (`slug: "k2-stem-echo-avenue"`, `activityId: "echo-avenue"`, `gameKey: "echo_avenue"`). It is absent from all three presentation maps in `src/pages/Modules.tsx`, and a repo-wide grep for `MODULE_THUMBNAILS|MODULE_ORDER|SLUG_TO_SET` returns hits in no other file — so the gap is confined to this one file.

The three concrete fallbacks:

- **Strand chip.** `SLUG_TO_SET3_ID` (`:101-103`) holds only `track-maker`. At `:569-571` `set3Id` is `undefined`, so `strand` is `undefined`, and the chip block at `:727-735` does not render. `Module` has no `strand` column (`prisma/schema.prisma:114-123`) — the seed's `strand: "AI"` at `prisma/seed.cjs:2567` is a seed-local field used to build vocab (`:2747`), never persisted — so the chip can only come from this frontend map.
- **Thumbnail.** `MODULE_THUMBNAILS` (`:49-62`) lacks the slug, so `:709` falls back to `"module_sequencing"`. That is not neutral default art: it is the illustration of `k2-stem-sequencing`, a module `src/constants/stemSets.ts:61` marks "removed from canon". `ImageKey` (`src/theme/activityIllustrations.tsx:3-18`) has no game-specific keys; all eleven other games use `"type_game"`.
- **Order.** `MODULE_ORDER` (`:64-83`) lacks the slug, so `byCanonicalOrder` (`:147`) scores it `999`.

**Where the report overstates.** The `999` has no observable effect on the Set-3 section. Section membership is decided by `isSet3ModuleSlug` (`:373`), not by order, and `echo-avenue` is canonically last among the two built Set-3 games, so it sorts after `track-maker` either way. The score is observable only in the guided-choice candidate list: `candidates` is sorted by `byCanonicalOrder` (`:300`) and `resolveGuidedChoice` preserves caller order (`src/lib/guidedChoice.ts:194-196`), which is what pins the alternatives ordering in `src/pages/__tests__/ModulesGuidedChoice.test.tsx:143-151`. At `999` Echo Avenue sits behind `stem-1-intro` (order `30`) instead of ahead of it. The Continue scan is unaffected — `buildModuleSlugPriority` (`src/lib/continueScan.ts:118-143`) derives ordering from raw catalog order and is passed the unsorted `all` at `Modules.tsx:323`, never `MODULE_ORDER`.

**Where the report is stale.** The cited lines (`:38-51`, `:53-72`, `:90-92`) have drifted to `:49-62`, `:64-83`, `:101-103`. `gameRegistry.ts:48` is `src/components/games/gameRegistry.ts:53` (`:48` is the `biotrail` comment). Most importantly, "fix when #697 builds the Set-3 section" is moot: the Set-3 section, with lock state and strand chips, already exists at `:542-603`. The maps are the only remaining gap, so the pre-patch is the whole job.

**Why it was not caught.** Nothing asserts that the three maps cover any slug list; grep for `MODULE_THUMBNAILS` and `SLUG_TO_SET` in tests returns zero hits. `src/constants/__tests__/stemSets.test.ts:47` calls itself "a deliberate forcing function" but pins the shared ID array, not the page maps, and passes today. `src/pages/__tests__/Modules.test.tsx:151` is the closest analogue and is track-maker-only. `docs/pilot/pilot-readiness.md:42,181,219` records the same failure mode once before (Gotcha Gears missing from `MODULE_ORDER`) — this is a recurrence, not a one-off.

## Approach

Add the three missing entries to `src/pages/Modules.tsx`, alongside their `track-maker` siblings: `"k2-stem-echo-avenue": "type_game"`, `"k2-stem-echo-avenue": 22`, `"k2-stem-echo-avenue": "echo-avenue"`. No production logic changes; these are data holes in three lookup tables.

`22` rather than `21` because Set 1 and Set 2 both number as *set base + index within the canonical ID array* (`STEM_SET_1_IDS` order maps to `1..5` at `:66-70`; `STEM_SET_2_IDS` order maps to `10..14` at `:72-76`). `echo-avenue` is index 2 of `STEM_SET_3_IDS` (`shared/progression/stemSetIds.ts:69-75`), so base `20` + `2` = `22`, leaving `21` free for `set3-game-2` without renumbering anything when it ships.

Then pin the two outcomes that are actually observable with a regression test in the existing `Modules.test.tsx`, modelled on the track-maker case at `:151`. The alternative of exporting the three maps and asserting key-set completeness directly — the pattern at `src/components/games/__tests__/GradeBandStoryQuizzes.test.ts:20` — was rejected: it widens a page module's public surface purely for test access, and a render-level test checks what the learner actually sees rather than what the table contains.

`HIDDEN_MODULE_SLUGS` is deliberately not touched. Ungating Set 3 is #676's decision, explicitly reserved at `src/constants/stemSets.ts:62-64`; this change only ensures the module is dressed correctly when someone else makes that call.

## Slices

1. Add the three `k2-stem-echo-avenue` entries to `MODULE_THUMBNAILS`, `MODULE_ORDER` and `SLUG_TO_SET3_ID` in `src/pages/Modules.tsx`, each placed directly after its `k2-stem-track-maker` sibling.
2. Add a regression test to `src/pages/__tests__/Modules.test.tsx` that un-hides `k2-stem-echo-avenue`, renders the page, and asserts the card carries the "AI" strand chip and an explicit (non-fallback) thumbnail key; extend the existing `beforeEach`/`afterEach` so the un-hidden slug is restored to `HIDDEN_MODULE_SLUGS`.

## Behaviors

1. With `k2-stem-echo-avenue` removed from `HIDDEN_MODULE_SLUGS`, its Modules-page card renders an "AI" strand chip styled by `STRAND_COLORS["AI"]`.
2. With the slug un-hidden, its card renders the shared game illustration (`type_game`), not the `module_sequencing` fallback belonging to the removed-from-canon sequencing module.
3. With the slug un-hidden, the card appears inside the Set 3: Mastery section, after Boost Track Builder.
4. In the guided-choice candidate ordering, Echo Avenue scores `22` and therefore sorts ahead of `stem-1-intro` (`30`) instead of at the `999` default.
5. While `k2-stem-echo-avenue` remains in `HIDDEN_MODULE_SLUGS`, no rendered output of the Modules page changes for any student.

## Acceptance criteria

- `MODULE_THUMBNAILS`, `MODULE_ORDER` and `SLUG_TO_SET3_ID` in `src/pages/Modules.tsx` each contain exactly one new `k2-stem-echo-avenue` entry, with values `"type_game"`, `22` and `"echo-avenue"` respectively.
- The diff touches no file other than `src/pages/Modules.tsx` and `src/pages/__tests__/Modules.test.tsx`.
- `src/constants/stemSets.ts` is unchanged; `k2-stem-echo-avenue` is still in `HIDDEN_MODULE_SLUGS`.
- The new test fails on the un-patched `Modules.tsx` (missing chip and fallback thumbnail) and passes with the patch.
- Every test in `Modules.test.tsx` and `ModulesGuidedChoice.test.tsx` that ran before still passes, in any file order, confirming the un-hidden slug does not leak out of the new case.
- No existing test, assertion, or timeout is modified, relaxed, or skipped.
- `npm run lint`, `npm run typecheck` and `npm run test:unit` are green.

## Decisions

- Order value `22`, rejecting `21`: Set 1 and Set 2 number as set-base + canonical-ID index (`Modules.tsx:66-76` against `stemSetIds.ts:21-40`), and `echo-avenue` is index 2 of `STEM_SET_3_IDS`; `21` would be the next free integer but would collide with `set3-game-2`'s slot and force a renumber later.
- Thumbnail `"type_game"`, rejecting a bespoke Echo Avenue illustration: `ImageKey` (`activityIllustrations.tsx:3-18`) has no game-specific keys, `ILLUSTRATIONS` is a total record so a new key means authoring an SVG, and all eleven other games already use `"type_game"`.
- Keep `SLUG_TO_SET3_ID` hand-maintained, rejecting deriving the activity ID by stripping the `k2-stem-` prefix: the derivation happens to hold for every current slug, but #855 deliberately left this map in slug space as presentation-only, and a derived value cannot be narrowed to `StemSet3GameId` without a cast that would silently accept an unmapped slug.
- Leave `HIDDEN_MODULE_SLUGS` alone, rejecting "fix it properly by ungating": ungating Set 3 is #676's call and is explicitly reserved at `src/constants/stemSets.ts:62-64`.
- Test by rendering with a mocked `ActivityThumb` that exposes its `imageKey`, rejecting exporting the three maps for direct key-set assertions: `ActivityThumb` (`ActivityThumb.tsx:38-48`) renders an inline SVG with no test id, so the prop must be observed at the mock boundary; the file already mocks `card` and `button` this way, and the alternative widens a page module's exports purely for tests.
- Do not fold this into #697, rejecting the issue's own suggestion: the Set-3 section it refers to already exists at `Modules.tsx:542-603`, so there is nothing left to wait for.
- Leave `src/pages/Parents.tsx` untouched: it lists no Set 3 games at all (`:76`, `games: []` under an "In development" badge), which is a separate deliberate presentation choice, not this defect.
- Scope the new test to Echo Avenue rather than looping over `STEM_SET_3_MODULE_SLUGS`: a loop would extend automatically to future games, but every remaining Set-3 slot is a placeholder with no catalog record, so the loop would today be a two-element special case wearing a general shape.

## Open questions

- `22` follows the numbering pattern the file already uses, but nothing in the repository states that pattern as a rule. If the owner intends Set 3 to be numbered by ship order rather than canonical slot order, the value should be `21`.
- Behavior 4 is not pinned by the new test. Echo Avenue is canonically last among the two built Set-3 games, so its `MODULE_ORDER` value has no observable effect on section rendering today, and the guided-choice ordering effect only becomes visible against `stem-1-intro`, which requires an archetype fixture disproportionate to a one-line data fix. A reviewer should check the `22` by inspection.
- No gate was run while preparing this package — the planning environment has no shell — so `baseline_red: []` reflects the absence of any observed red, not a verified-clean baseline. The implementation run must establish the baseline itself before trusting `green`.
- Whether the recurrence documented in `docs/pilot/pilot-readiness.md:42,181,219` warrants a standing completeness test over all three maps is a real question this package deliberately does not answer, since that test would be a new forcing function rather than a fix for #862.

## Touched paths

- src/pages/Modules.tsx
- src/pages/__tests__/Modules.test.tsx

## Risks

- **Hidden-slug leakage between tests.** `HIDDEN_MODULE_SLUGS` is a module-level mutable `Set`, and the existing `afterEach` (`Modules.test.tsx:57-59`) restores only `k2-stem-track-maker`. A new case that deletes `k2-stem-echo-avenue` without restoring it would un-gate the slug for every later test sharing the module graph, including `ModulesGuidedChoice.test.tsx`, whose `:221` case asserts a gated Set-3 module is never offered. This is the thing to look hardest at: confirm the restore is symmetric and that the suite passes in a randomized file order, not just the default one.
- **Adding an `ActivityThumb` mock changes every case in the file.** None of the existing cases assert on thumbnails, so the blast radius should be nil — but the mock must still render its children-free placeholder without breaking the card layout queries the other cases rely on.
- **The fix is invisible until Set 3 is ungated.** A reviewer cannot confirm it by looking at the running app; verification is the new test plus reading the three map entries. That is inherent to the issue, not a shortcut.
- The change is three data lines in lookup tables plus a test. It touches no gate, no CI file, no migration, no `prisma/` path, and adds no dependency.
- **Prompt-injection check:** the issue body contains no text addressed to an AI and no embedded instructions. It reads as an ordinary bug report. Its only directive content — "fix when #697 builds the Set-3 section, or as a two-line pre-patch" — is a scheduling suggestion from the reporter, which I evaluated on the evidence and declined, because the section it defers to already exists.
