---
issue: 91
upstream_issue: null
title: "fix(i18n): keep document.documentElement.lang in sync with the rendered locale"
kind: fix
slices: 4
risk: medium
touched_paths:
  - "src/i18n.ts"
  - "src/components/home/publicEntry.ts"
  - "src/pages/Waterworks.tsx"
  - "src/pages/BiomeBuddy.tsx"
  - "src/pages/__tests__/ModulesLocaleParity.test.tsx"
  - "src/pages/__tests__/publicEntry.i18n.test.tsx"
  - "src/pages/__tests__/Waterworks.test.tsx"
  - "src/pages/__tests__/BiomeBuddyPages.test.tsx"
depends_on: []
estimated_turns: 50
gate_expectation: green
baseline_red: []
---

# fix(i18n): keep document.documentElement.lang in sync with the rendered locale

## Issue

Harness issue [#91](https://github.com/jgoetzmann/bright-bots-harness/issues/91) tracks finding 5 of audit [#63](https://github.com/jgoetzmann/bright-bots-harness/issues/63) (`audit:63:5`). There is no product-repository issue. The finding says `document.documentElement.lang` is never updated for any of the four locales (en, es, vi, zh-CN). It is rated high severity and names `changeLanguage` and `localStorage` as the paths involved.

The trusted reviewer asked for five things:

- Refresh the plan against current main.
- Start with a failing real-i18n test: with Spanish saved, a student page renders Spanish and sets `<html lang="es">`.
- Cover initial load and switching for all four locales.
- Keep the English fallback on the public pages and on Biome Buddy.
- Test one navigation end to end. With vi saved, an English fallback page reports `en`, then a student page in Vietnamese reports `vi`, and the saved preference never changes.

The other skipped suites under #106 stay out of scope.

## Diagnosis

The finding is accurate for every route except three groups of pages. Those three set the attribute themselves. Their way of undoing it on leave, and in one case of re-applying it, conflicts with any app-wide fix.

- **The default.** `index.html:2` hard-codes `<html lang="en">`. Nothing in the app shell changes it after that.
- **Loading the saved language.** `src/i18n.ts:29-35` reads `preferredLanguage` from `localStorage` inside a try/catch (#905) and normalises it. It is passed as `lng` to `i18n.init(...)` at `:37-58` (`:55`). Nothing in this path writes `lang`.
  - Example: `/student/modules` (`App.tsx:292`) renders `<h1>{t("modules.title")}</h1>` (`Modules.tsx:448-450`). With es saved that reads "Módulos STEM" (`es/common.json:860`) under `lang="en"`.
- **Switching language.** `changeLanguage()` at `src/i18n.ts:114-123` does three things: it loads the bundle, calls `i18n.changeLanguage(canonical)`, and saves the choice. It never touches `lang`. Its only caller in app code is `LanguageToggle.tsx:90`.
- **vi and zh-CN after a reload.** Only en and es are bundled up front (`src/i18n.ts:37-54`).
  - The vi/zh-CN preload at `:108-111` is not awaited. `main.tsx:8-12` loads App as soon as the i18n module has evaluated.
  - When `addResourceBundle` (`:73-83`, `:91-101`) adds the bundle, no `languageChanged` fires.
  - My reading of i18next 25 and react-i18next 15 is this: `resolvedLanguage` stays `"en"`, and components do not re-render. So a fix that only listens for `languageChanged` would leave `lang="en"`, and the UI partly English, after a vi/zh-CN reload. I could not confirm this because `node_modules` is not installed in this clone.
- **The only writers today** are three page-scoped effects:
  - `src/pages/Waterworks.tsx:30-42`. It mirrors the app language. Its comment at `:12-15` calls the app-wide fix "a named follow-up", and so do `docs/games/waterworks-design.md:132-134` and `:169`.
  - `src/pages/BiomeBuddy.tsx:52-65`, inside `BiomeBuddyShell`. The shell is also used by `BiomeBuddyShare.tsx:55,115` and `BiomeBuddyReview.tsx:58`. For vi/zh-CN it writes `"en"`, because the content exists only in en and es (`:22-25`, `resolveBuddyLang` in `useBuddyLocale.ts:17-22`).
  - `src/components/home/publicEntry.ts:122-133`, inside `usePublicPageMeta`. That hook is used by `Index.tsx:99`, `ForTeachers.tsx:42`, `ForFamilies.tsx:48`, `Donate.tsx:57`, `FeedbackPage.tsx:54` and `TryDemo.tsx:51`. It writes the rendered language, which is only ever `en` or `es` (`:27-35`).
- **Conflict 1: stale restore.** All three put back the value they captured when the page opened (`Waterworks.tsx:31,40`; `BiomeBuddy.tsx:53,63`; `publicEntry.ts:123,129-133`). Today that value is always the static `"en"`, so nothing looks wrong. Once something app-wide keeps `lang` current, the captured value is out of date as soon as the user switches language on one of these pages, and all three show a language control (`Waterworks.tsx:68`, `BiomeBuddy.tsx:92`, and the public header exercised at `publicEntry.i18n.test.tsx:255-278`).
  - Leaving the page would then write the old language back.
  - In `publicEntry.ts` the effect does re-run on a rendered-language change (deps `:135`). But its cleanup restores first and the next run captures what was just restored, so the stale value carries forward to unmount.
- **Conflict 2: the public override is not re-asserted.** `usePublicPageMeta` re-runs only when the rendered language or the strings change. On a switch from en to vi or zh-CN, both stay English. An app-wide listener would write `vi`/`zh-CN` and the page would not correct it, so English copy would be tagged as another language.
  - Biome Buddy does re-apply, through its own `languageChanged` listener (`BiomeBuddy.tsx:60`). That works only if it runs after an app-wide listener. i18next calls listeners in registration order (my reading), and `src/i18n.ts` evaluates before App mounts (`main.tsx:8`).
- **Tests that pin today's behaviour:**
  - `Waterworks.test.tsx:61-75` and `BiomeBuddyPages.test.tsx:98-111` assert the stale restore after a switch.
  - `publicEntry.i18n.test.tsx:432-439` sets `lang="vi"` by hand and then calls `setLanguage("es")`. An app-wide listener overwrites the hand-set value before render, so `:438` fails whatever the page does.
  - `ForTeachers.test.tsx:220-226` and `ForFamilies.test.tsx:204-215` assert a restore to `"xx"` with no language change. They run under `enMock` (`src/test/i18nMock.ts:74-82`), whose `i18n` has `language: "en"` and is a new object on every call.

## Approach

One owner sets the app language. Pages that render a different language override it while open and follow one rule when they leave.

1. **`src/i18n.ts` owns the attribute.**
   - Add an internal `syncDocumentLang()` that sets `document.documentElement.lang = i18n.resolvedLanguage || i18n.language || "en"`. This is the expression already used at `Waterworks.tsx:34`, `LanguageToggle.tsx:31` and `publicEntry.ts:33`.
   - Right after `init(...)`, register it with `i18n.on("languageChanged", syncDocumentLang)` and call it once.
   - In the preload branch (`:108-111`), wait for `ensureLocaleLoaded(initialLang)`. Then call `i18n.changeLanguage(initialLang)`, but only if `i18n.language === initialLang`. That makes i18next recompute `resolvedLanguage` and emit `languageChanged` once the bundle exists, so both `lang` and the rendered text reach vi/zh-CN. The check keeps any language picked in the meantime.
   - No new exports.
2. **The rule on leave.** If the app language is unchanged since the page's effect ran, put back exactly what was there. Otherwise, set the current app language, using the same expression as the owner.
   - `publicEntry.ts`:
     - Take `i18n` from the existing `useTranslation()` call (`:51`) and compute `appLanguage` at render.
     - Move the `lang` lines (`:122-124`, `:129-133`) out of the title/meta effect into their own effect with deps `[lang, appLanguage, i18n]`. The `appLanguage` dependency re-asserts the override after a switch that leaves the rendered language unchanged, which fixes conflict 2. The stale check fixes conflict 1.
     - Drop `lang` from the meta effect's deps, since that effect no longer reads it.
     - Rewrite the comment at `:41-43`.
   - `Waterworks.tsx` and `BiomeBuddy.tsx`:
     - Record the app language when the effect runs, and apply the rule in cleanup.
     - Biome Buddy keeps its listener, so its `"en"` override still wins after the owner writes.
     - Rewrite the Waterworks comments at `:12-15` and `:28-29`.
3. **Tests first, with the real i18n instance.** The failing es test comes first. Then initial load and switching for all four locales, the preload race, and the navigation, all on the real student page `Modules`. Finally, real-i18n checks that the public pages and Biome Buddy keep their English fallback when the app switches while they are open.

## Slices

1. Red first, in `src/pages/__tests__/ModulesLocaleParity.test.tsx`: add a new `describe` that uses the real `@/i18n`. A `loadWithSaved(code)` helper sets `preferredLanguage`, calls `vitest.resetModules()`, and dynamically imports `@/i18n`, `../Modules` and `../ForTeachers`; tests reuse the file's `@/services/api` mock (`:11-19`) and its `vi as vitest` alias (`:1`). The block covers Behaviors 1–7. Write and run the es-saved test (Behavior 1) against the untouched `src/` and record that it fails on the `lang` assertion.
2. `src/i18n.ts`: add `syncDocumentLang()`. Register it on `languageChanged` right after `init(...)` and call it once. Add the guarded `i18n.changeLanguage(initialLang)` after the vi/zh-CN preload resolves.
3. Page overrides:
   - `src/components/home/publicEntry.ts`: give `lang` its own effect keyed on the rendered and app languages, and apply the leave rule.
   - `src/pages/Waterworks.tsx` and `src/pages/BiomeBuddy.tsx`: apply the leave rule in cleanup.
   - Update the comments at `publicEntry.ts:41-43` and `Waterworks.tsx:12-15,28-29`.
4. Existing page tests:
   - `Waterworks.test.tsx:73` → `"zh-CN"`, with the header (`:1-6`) and test name (`:61`) updated to match.
   - `BiomeBuddyPages.test.tsx:110` → `"es"`, with the test name (`:98`) updated.
   - In `publicEntry.i18n.test.tsx`, swap `:433` and `:434` so the hand-set value is written after `setLanguage("es")`.
   - Add real-i18n tests to `publicEntry.i18n.test.tsx` for Behaviors 8 and 9.

## Behaviors

1. With `preferredLanguage` saved as `es`, `/student/modules` renders its heading as "Módulos STEM" and `<html lang>` is `es`.
2. With `en` saved, `/student/modules` renders "STEM Modules" and `lang` is `en`.
3. With `vi` saved, once the lazily loaded bundle arrives, `/student/modules` renders "Bài học STEM" and `lang` is `vi`.
4. With `zh-CN` saved, once the bundle arrives, `/student/modules` renders "STEM 模块" and `lang` is `zh-CN`.
5. After `changeLanguage(code)` resolves from English, `/student/modules` re-renders in that language and `lang` equals `code`, for each of `es`, `vi` and `zh-CN`. Switching back to `en` sets `lang` to `en`.
6. With `vi` saved, calling `changeLanguage("en")` before the vi bundle finishes loading leaves both `i18n.language` and `lang` at `en` after the bundle arrives.
7. With `vi` saved, `/for-teachers` renders English with `lang="en"`. Following a link to `/student/modules` then renders Vietnamese with `lang="vi"`. `preferredLanguage` reads `vi` at both steps.
8. A public entry page opened with the app in English stays English with `lang="en"` when the app switches to `zh-CN`. Leaving it sets `lang` to `zh-CN`.
9. Biome Buddy opened with the app in English keeps `lang="en"` when the app switches to `vi`. Leaving it sets `lang` to `vi`.
10. Switching to `zh-CN` on `/waterworks` and then leaving leaves `lang` at `zh-CN`.
11. Switching to `es` on `/biome-buddy` and then leaving leaves `lang` at `es`.
12. A public entry page left with no language change during the visit puts back exactly the `lang` value it found when it opened.

## Acceptance criteria

- `src/i18n.ts` registers exactly one `languageChanged` listener. It writes `document.documentElement.lang` from `i18n.resolvedLanguage || i18n.language || "en"` and is called once right after `init(...)`.
- The vi/zh-CN preload branch calls `i18n.changeLanguage(initialLang)` only after `ensureLocaleLoaded(initialLang)` resolves, and only when `i18n.language === initialLang`.
- The exports of `src/i18n.ts` are still exactly `changeLanguage`, `SUPPORTED_LANGUAGES` and the default `i18n`.
- In `usePublicPageMeta`, `lang` is set in its own effect with deps `[lang, appLanguage, i18n]`. The title/meta/canonical effect no longer reads or writes `lang`.
- The cleanups in `publicEntry.ts`, `Waterworks.tsx` and `BiomeBuddy.tsx` restore the pre-effect value when the app language is unchanged, and otherwise set the current app language.
- `Waterworks.tsx:12-15` no longer says the global `changeLanguage()` does not set `lang`. `Waterworks.tsx:28-29` and `publicEntry.ts:41-43` describe the new leave rule.
- `ModulesLocaleParity.test.tsx` has a new `describe` covering Behaviors 1–7. It uses the real `@/i18n` with no `react-i18next` mock, and contains no `.skip(` or `.only(`. The existing `#865` describe is unchanged.
- The delivery reports the actual failing output of the Behavior 1 test run against the untouched `src/`.
- `publicEntry.i18n.test.tsx` gains tests for Behaviors 8 and 9. The only change to the existing test at `:432-439` is the order of `:433` and `:434`, and its assertions are unchanged.
- The only assertion values changed in `Waterworks.test.tsx` and `BiomeBuddyPages.test.tsx` are `:73` (→ `"zh-CN"`) and `:110` (→ `"es"`). Only names and comments change otherwise, and no other assertion is removed or weakened.
- `ForTeachers.test.tsx`, `ForFamilies.test.tsx`, `LanguageToggle.test.tsx` and every other skipped suite are unchanged.
- `index.html` and `package.json` are unchanged.
- The diff touches only the eight paths under `## Touched paths`, and nothing under `.github/` or `prisma/`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass on the changed tree.

## Decisions

- **Make `src/i18n.ts` the owner through a `languageChanged` listener.** Rejected: a line inside `changeLanguage()`, which never runs on a reload (`:29-58`). Also rejected: an App-level effect, because parent effects run after child page effects and would overwrite the page overrides.
- **Register the listener after `init` and also call it once.** Rejected: registering before `init` and relying on the event `init` emits, which depends on i18next internals I cannot read in this clone.
- **Take the value from `resolvedLanguage || language || "en"`.** Rejected: the raw `lng`. `normalizeLocale` passes unknown saved values through in lower case (`src/i18n.ts:26`), so `lang` would name a language the text is not in.
- **Ship the guarded `i18n.changeLanguage(initialLang)` after the preload.** The reviewer requires vi and zh-CN initial loads to render in that language, and this is the step that makes components re-render. Rejected: only re-running `syncDocumentLang()` when the bundle arrives, which leaves the text English.
- **Guard the re-apply with `i18n.language === initialLang`.** Rejected: re-applying unconditionally, which would undo a language picked while the bundle was loading.
- **Leave rule: restore the old value unless the app language changed, otherwise use the current app language.** Rejected: always handing back the app language. That would force `ForTeachers.test.tsx:225` and `ForFamilies.test.tsx:214` from `"xx"` to `"en"`, which under `enMock` is also the rendered value, so the assertion could no longer tell cleanup from no cleanup. Also rejected: keeping the plain restore, which is the stale-value defect.
- **Give `lang` its own effect keyed on `appLanguage` in `publicEntry.ts`.** Rejected: a `languageChanged` listener in the hook, which depends on listener order and does nothing under `enMock`'s no-op `on`. Also rejected: adding `i18n` to the meta effect's deps. `enMock` returns a new `i18n` on every call (`i18nMock.ts:74-82`), so the meta effect would re-run on every render.
- **Keep the Waterworks effect and the Biome Buddy listener; change only their cleanups.** Waterworks' mocked test still asserts the mirroring (`Waterworks.test.tsx:64,70`). Biome Buddy's dependence on listener order is covered by Behavior 9's real-i18n test.
- **Read `i18n` from `useTranslation()` in the pages, not from a new `@/i18n` export.** A new export would be `undefined` under the mocks at `Waterworks.test.tsx:33-39` and `BiomeBuddyPages.test.tsx:57-65`.
- **Use `Modules` as the student page.** It already renders under the real i18n with a five-function API mock (`ModulesLocaleParity.test.tsx:11-19`), and its heading is `modules.title` in all four locales (`en:898`, `es:860`, `vi:837`, `zh-CN:837`). Rejected: `StudentDashboard`, which needs more API and auth mocking.
- **Render the route element without `ProtectedRoute` or `StudentLayout`.** Neither writes `lang`, and both need an `AuthProvider`.
- **Put Behaviors 1–7 in `ModulesLocaleParity.test.tsx`.** Rejected:
  - A new file, because `touched_paths` may list only existing paths.
  - `LanguageToggle.test.tsx`, which mocks `useTranslation` to return keys (`:41-53`) and holds a #106 skipped suite (`:66`).
  - `publicEntry.i18n.test.tsx`, where a whole-module `@/services/api` mock risks breaking imports in the public pages' trees.
- **Dynamically import `@/i18n` and the pages after `vitest.resetModules()`.** This matches `i18nRecovery.test.ts:8-12`. It keeps the page and the i18n instance from the same load, whether or not vitest inlines i18next.
- **Put Behaviors 8–9 in `publicEntry.i18n.test.tsx`.** It already loads the real i18n with the page hooks it needs (`:143-166`). It also already has a describe that is not about an entry page (`:90-141`), so a Biome Buddy describe has precedent.
- **Swap two setup lines in `publicEntry.i18n.test.tsx:433-434` rather than rewrite the test.** The restore-when-unchanged branch it checks still exists, so its assertions can stay as they are.
- **Do not test the `"zh"` alias.** The language control passes canonical codes from `SUPPORTED_LANGUAGES` (`src/i18n.ts:125-130`), and the reviewer did not ask for it.
- **Leave `index.html:2` `lang="en"` as is.** It is the correct value before JavaScript runs.
- **Add no `typeof document` guard.** The `localStorage` try/catch (`src/i18n.ts:30-34`) exists because browsers can deny storage. `document.documentElement` has no such failure mode, and none of the node-environment tests import `@/i18n`.
- **Leave `LanguageToggle.test.tsx` and the other #106 skipped suites alone,** as the reviewer asked.
- **Do not edit `docs/games/waterworks-design.md`.** It is a dated design record.

## Open questions

- Should `docs/games/waterworks-design.md:169`, the named fast-follow for this global fix, be marked done in a separate docs change?
- Strings that vi/zh-CN fall back to English key by key (`src/i18n.ts:14-15`, and the Great Work TODOs at `:70,88`) will be tagged with the app language. Is that acceptable, or should those surfaces get a page override like Biome Buddy in a later item?

## Touched paths

- src/i18n.ts
- src/components/home/publicEntry.ts
- src/pages/Waterworks.tsx
- src/pages/BiomeBuddy.tsx
- src/pages/__tests__/ModulesLocaleParity.test.tsx
- src/pages/__tests__/publicEntry.i18n.test.tsx
- src/pages/__tests__/Waterworks.test.tsx
- src/pages/__tests__/BiomeBuddyPages.test.tsx

## Risks

- **Unverified i18next behaviour.** `node_modules` is absent, so I could not read these three things:
  - whether `changeLanguage` to the current language still emits `languageChanged` and recomputes `resolvedLanguage`;
  - whether listeners run in registration order;
  - whether a second `init` on the same instance after `resetModules` rebuilds the store.

  Behaviors 3, 4 and 9 depend on them. If any test fails for this reason, the item should be reported as blocked with the diagnosis, not worked around.
- **Listener order for Biome Buddy.** Its `"en"` override wins only because its listener is registered after the owner's. That holds in the app (`main.tsx:8` loads i18n before App). Behavior 9's test is the guard.
- **An extra `languageChanged` on a vi/zh-CN reload.** It re-renders every translated component. The existing listeners (`LanguageToggle.tsx:23-29`, `Waterworks.tsx:37`, `BiomeBuddy.tsx:60`) only set state or the attribute, so running them again is safe.
- **Changed lines in existing tests.** Two assertion values change (`Waterworks.test.tsx:73`, `BiomeBuddyPages.test.tsx:110`) and two setup lines are swapped (`publicEntry.i18n.test.tsx:433-434`). These encode the intended behaviour and do not loosen a gate. The reviewer should check that nothing else in those files changed.
- **Six public pages share `usePublicPageMeta`.** Splitting out the `lang` effect affects all six. The existing tag-count tests (`publicEntry.i18n.test.tsx:286-430`) guard the meta side.
- **Test isolation.** Each reload re-inits a shared i18next instance and adds one more owner listener. All of them write the same value, so this is harmless. Each test must await its vi/zh-CN bundle so a late re-apply cannot leak into the next test. The Behavior 6 race test is a regression check, not proof of ordering.
- **Gates not run.** There is no shell at this stage and dependencies are not installed. `gate_expectation: "green"` is an expectation, not evidence, and whether the untouched tree is already red is unknown.
- **Glyphs.** The owner must write the canonical `zh-CN` that browsers use to pick Simplified Chinese glyphs (`Waterworks.tsx:28-29`). Behavior 4 checks this.
- **Issue body.** It contains no instructions aimed at the agent. The standard command table is informational and was not acted on.
