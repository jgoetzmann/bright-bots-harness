---
issue: 91
upstream_issue: null
title: "fix(i18n): keep document.documentElement.lang in sync with the active locale"
kind: fix
slices: 3
risk: medium
touched_paths:
  - "src/i18n.ts"
  - "src/components/tests/LanguageToggle.test.tsx"
  - "src/pages/Waterworks.tsx"
  - "src/pages/BiomeBuddy.tsx"
  - "src/pages/__tests__/Waterworks.test.tsx"
  - "src/pages/__tests__/BiomeBuddyPages.test.tsx"
depends_on: []
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# fix(i18n): keep document.documentElement.lang in sync with the active locale

## Issue

Harness issue [#91](https://github.com/jgoetzmann/bright-bots-harness/issues/91) tracks finding 5 of audit [#63](https://github.com/jgoetzmann/bright-bots-harness/issues/63) (`audit:63:5`). There is no product-repository issue. The finding says `document.documentElement.lang` is never updated for any of the four locales (en, es, vi, zh-CN). It is rated high severity and names `changeLanguage` and `localStorage` as the paths involved.

## Diagnosis

The finding is accurate for the app as a whole. Two unlisted pages already set the attribute themselves, and the way they undo it on leave conflicts with any app-wide fix.

- `index.html:2` hard-codes `<html lang="en">`. Nothing in the app shell changes it after that.
- **Loading the saved language.** `src/i18n.ts:27-28` reads `preferredLanguage` from `localStorage` and passes it as `lng` to `i18n.init(...)` at `src/i18n.ts:30-39`. Nothing in this path writes `document.documentElement.lang`. A user who reloads with es, vi or zh-CN saved gets `lang="en"`.
- **Switching language.** `changeLanguage()` at `src/i18n.ts:83-88` loads the bundle, calls `i18n.changeLanguage(canonical)`, and saves the choice with `localStorage.setItem`. It never touches the `lang` attribute. `LanguageToggle.tsx:73` is the only caller, so switching language never changes `lang`.
- **The only code that sets it.** Two page-scoped effects are the only writers of `document.documentElement.lang` in `src/`:
  - `src/pages/Waterworks.tsx:30-42`
  - `src/pages/BiomeBuddy.tsx:52-65`, inside `BiomeBuddyShell`. The shell is also used by `BiomeBuddyShare.tsx:55,115` and `BiomeBuddyReview.tsx:58`.

  Both say they exist because of this gap: `Waterworks.tsx:12-15` ("the global changeLanguage() doesn't set it — page-scoped fix, app-wide fix is a named follow-up"). `docs/games/waterworks-design.md:132-134` and `:169` list the global fix as a named follow-up.
- **Latent defect the fix must handle.** On unmount, both page effects put back the value they saw when the page opened (`Waterworks.tsx:31,40`; `BiomeBuddy.tsx:53,63`). Today that value is always the static `"en"`, so nothing looks wrong. Once something app-wide keeps `lang` current, that saved value is out of date whenever the user switches language on one of these pages, and both pages show a `LanguageToggle`. Leaving the page would then write the old language back over the correct one.
- **vi and zh-CN after a reload.** Only en and es are loaded up front (`src/i18n.ts:31-35`). With vi or zh-CN saved, `init` runs before that bundle exists. The bundle is then loaded without being awaited (`src/i18n.ts:78-80`), and `main.tsx:31` renders as soon as the module has loaded. No `languageChanged` event fires when the bundle arrives. So a fix that only listens for `languageChanged` would keep `lang` at whatever language i18next had settled on during `init`, which I expect to be `"en"`. I could not confirm this because `node_modules` is not installed in this clone (see Open questions).

## Approach

1. **Make `src/i18n.ts` the one owner of the attribute.**
   - Add a small internal function `syncDocumentLang()` that sets `document.documentElement.lang = i18n.resolvedLanguage || i18n.language || "en"`. This is the same expression `Waterworks.tsx:34` and `LanguageToggle.tsx:29` use.
   - Right after `init(...)`, register it with `i18n.on("languageChanged", syncDocumentLang)` and call it once. This covers the first load and every later change, whoever calls `i18n.changeLanguage`.
   - In the vi/zh-CN reload branch (`src/i18n.ts:78-80`), wait for `ensureLocaleLoaded(initialLang)` to finish, then call `i18n.changeLanguage(initialLang)`, but only if `i18n.language` is still `initialLang`. That makes i18next recalculate the resolved language and fire `languageChanged` once the bundle exists. The check stops it from overwriting a language the user picked while the bundle was still loading.
2. **Fix the undo step in the two page effects.** When the page closes, each effect should set `lang` to the app's current language (`i18n.resolvedLanguage || i18n.language || "en"`) instead of the value it saved on open. The saved `prior` variable goes away.
   - Waterworks otherwise keeps its effect unchanged. It is now redundant but harmless.
   - Biome Buddy keeps its page-level override: while open it shows `"en"` for vi/zh-CN, because its content exists only in en and es (`BiomeBuddy.tsx:22-25`). On leave it hands `lang` back to the app language.
3. **Tests.**
   - Add a new, non-skipped `describe` block in `src/components/tests/LanguageToggle.test.tsx` that exercises the real `@/i18n` module. That file already loads it through `LanguageToggle.tsx:3` and does not mock it.
   - Update the two page tests' leave assertions to the new behaviour, and add a leave assertion for Biome Buddy's vi override.

## Slices

1. `src/i18n.ts`: add `syncDocumentLang()`, subscribe it to `languageChanged` after `init` and call it once, and add the guarded `i18n.changeLanguage(initialLang)` after the vi/zh-CN reload preload finishes.
2. `src/components/tests/LanguageToggle.test.tsx`: add a live `describe` block (leaving the `describe.skip` block at lines 66-303 untouched) that imports the real `@/i18n` and covers:
   - each of the four locales through `changeLanguage`
   - the `"zh"` alias
   - reload with `es` saved and with `vi` saved, using `vi.resetModules()` and a dynamic import with the file's existing `localStorage` mock
   - a language picked before the vi preload finishes staying in effect
3. Page effects and their tests:
   - `Waterworks.tsx` and `BiomeBuddy.tsx` hand `lang` back to the app language on unmount.
   - Update the header comment at `Waterworks.tsx:12-15`.
   - Update `Waterworks.test.tsx:1-6,61,73` and `BiomeBuddyPages.test.tsx:96,108` to the hand-back behaviour.
   - Add an unmount assertion to the vi test at `BiomeBuddyPages.test.tsx:111-125`.

## Behaviors

1. With no saved language, `document.documentElement.lang` is `"en"` once `src/i18n.ts` has loaded.
2. With `preferredLanguage` saved as `"es"`, `document.documentElement.lang` is `"es"` once `src/i18n.ts` has loaded.
3. With `preferredLanguage` saved as `"vi"`, `document.documentElement.lang` becomes `"vi"` once the lazily loaded vi bundle has arrived.
4. After `changeLanguage(code)` resolves, `document.documentElement.lang` equals `code`, for each of `en`, `es`, `vi` and `zh-CN`.
5. After `changeLanguage("zh")` resolves, `document.documentElement.lang` is `"zh-CN"`.
6. With `"vi"` saved, calling `changeLanguage("en")` before the vi preload finishes leaves both `i18n.language` and `document.documentElement.lang` at `"en"` after the preload finishes.
7. Switching language to zh-CN on `/waterworks` and then leaving leaves `document.documentElement.lang` at `"zh-CN"`, not the value from before the page opened.
8. Switching language to es on `/biome-buddy` and then leaving leaves `document.documentElement.lang` at `"es"`.
9. With the app set to vi, `/biome-buddy` shows `lang="en"` while open and `lang="vi"` after leaving.

## Acceptance criteria

- `src/i18n.ts` registers exactly one `languageChanged` listener that writes `document.documentElement.lang`, and calls it once right after `init(...)`.
- The vi/zh-CN reload branch in `src/i18n.ts` calls `i18n.changeLanguage(initialLang)` only after `ensureLocaleLoaded(initialLang)` finishes, and only when `i18n.language === initialLang`.
- `src/i18n.ts` has no new exports; its exports are still `changeLanguage`, `SUPPORTED_LANGUAGES` and the default `i18n`.
- `Waterworks.tsx` and `BiomeBuddy.tsx` no longer save a `prior` value, and their cleanup sets `lang` from `i18n.resolvedLanguage || i18n.language || "en"`.
- `Waterworks.tsx:12-15` no longer says that the global `changeLanguage()` does not set `lang`.
- The new `describe` block in `LanguageToggle.test.tsx` has no `.skip(` or `.only(` and covers Behaviors 1–6. The existing `describe.skip` block (lines 57-303) is unchanged.
- The only assertion values changed in `Waterworks.test.tsx` and `BiomeBuddyPages.test.tsx` are the two post-unmount `lang` expectations (`:73` → `"zh-CN"`, `:108` → `"es"`). The only other change is one added unmount assertion (`"vi"`) in the vi test. No other assertion in those files is removed or weakened.
- `index.html` and `package.json` are unchanged.
- The diff touches only the six paths under `## Touched paths`; nothing under `.github/` or `prisma/`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass on the changed tree.

## Decisions

- **Listen to i18next's `languageChanged` event rather than add a line to the exported `changeLanguage()` (`src/i18n.ts:83-88`).** Rejected: `changeLanguage()` never runs on a reload (`src/i18n.ts:27-39`), so a line there would miss the saved-language path the finding names.
- **Register the listener after `init` and also call it once, rather than register before `init` and rely on the event `init` fires.** Rejected: that depends on i18next internals (whether listeners survive `init`, whether `init` fires synchronously with inline resources) that I cannot read in this clone. Calling it after `init` works either way.
- **Take the value from `i18n.resolvedLanguage || i18n.language || "en"`, matching `Waterworks.tsx:34` and `LanguageToggle.tsx:29`.** Rejected: the raw `lng` argument. `normalizeLocale` returns unknown saved values as-is in lower case (`src/i18n.ts:24`), so `lang` would name a language the text is not actually in.
- **Include a guarded re-apply after the vi/zh-CN reload preload.** Rejected: only re-running `syncDocumentLang()` once the bundle loads. Whether that picks up "vi" depends on whether i18next recalculates `resolvedLanguage` when a bundle is added, which I could not verify. Also rejected: leaving it out, which would leave the finding unfixed for vi and zh-CN after a reload.
- **Guard the re-apply with `i18n.language === initialLang`.** Rejected: re-applying unconditionally, which would overwrite a language the user picked while the bundle was loading.
- **Keep both page-scoped effects and change only their cleanup.** Rejected: deleting the Waterworks effect, which would mean deleting or rewriting its page test's mirroring assertions (`Waterworks.test.tsx:64,70`). The effect is harmless next to the app-wide listener. Also rejected: leaving the pages untouched, which leaves the stale restore described in Diagnosis.
- **Write the page cleanups with the `i18n` object from `useTranslation()`, not a new export from `@/i18n`.** Rejected: a new export would be `undefined` under the mocks in `Waterworks.test.tsx:33-39`, `BiomeBuddyPages.test.tsx:55-63` and `K2InstantFeedbackQuiz.test.tsx:18`, which provide only `changeLanguage` and `SUPPORTED_LANGUAGES`.
- **Put the tests for `src/i18n.ts` in `src/components/tests/LanguageToggle.test.tsx`.** Rejected: a new `src/__tests__/i18n.documentLang.test.ts`, because `touched_paths` may list only existing paths. The chosen file is the only existing test that loads the real `@/i18n` without mocking it, and it already has a `localStorage` mock (`:11-28`) that returns `"es"` for `preferredLanguage`.
- **Do not touch the quarantined `describe.skip` in `LanguageToggle.test.tsx:57-66`.** Rewriting it is separate work that its own TODO describes.
- **Test `changeLanguage()` directly rather than by clicking the toggle.** The toggle only calls `changeLanguage(lang.code)` (`LanguageToggle.tsx:73`). Rejected: a render-and-click test, which would duplicate that coverage and mix the mocked `useTranslation` instance with the real module.
- **Leave `index.html:2` `lang="en"` as is.** It is the right default before JavaScript runs.
- **No `typeof document` guard in `src/i18n.ts`.** This matches the unguarded module-level `localStorage` access at `src/i18n.ts:27`.
- **Do not edit `docs/games/waterworks-design.md`.** It is a dated, approved design record (§9, "approved 2026-07-09"), and its follow-up entry at `:169` describes what was true at the time.

## Open questions

- I could not verify two i18next 25 details because `node_modules` is absent in this clone: whether `init` with inline resources fires `languageChanged` synchronously, and whether `resolvedLanguage` is recalculated on `addResourceBundle`. The plan does not depend on either, but the tests in slice 2 should confirm Behaviors 1–3 on a real install.
- The guarded re-apply in slice 1 also makes the whole UI re-render into vi/zh-CN once the lazy bundle arrives on a reload. My unverified reading is that the UI currently stays partly English until each component re-renders, because react-i18next is not bound to bundle-added events by default. Should that visible improvement ship in this package, or be split into its own item? If it is split out, `lang` will read `"en"` after a vi/zh-CN reload until the user next switches language.
- Should `docs/games/waterworks-design.md:169`, the "named fast-follow" for this global fix, be marked done in a separate docs change?

## Touched paths

- src/i18n.ts
- src/components/tests/LanguageToggle.test.tsx
- src/pages/Waterworks.tsx
- src/pages/BiomeBuddy.tsx
- src/pages/__tests__/Waterworks.test.tsx
- src/pages/__tests__/BiomeBuddyPages.test.tsx

## Risks

- **An extra `languageChanged` on reload for vi/zh-CN users.** The re-apply fires one more `languageChanged`, which re-renders every component that uses the translation hook. The existing listeners (`LanguageToggle.tsx:23`, `Waterworks.tsx:37`, `BiomeBuddy.tsx:60`) all just set state or an attribute and are safe to run twice. The reviewer should confirm no other listener has side effects.
- **The race guard.** If the user picks a language while the vi bundle is still loading, the preload must not overwrite it. Behavior 6's test passes whichever finishes first, so it is a regression check, not proof. The guard condition itself needs careful review.
- **Changed assertions in two existing tests.** `Waterworks.test.tsx:73` and `BiomeBuddyPages.test.tsx:108` change from "restore the value from before the page opened" to "hand back to the app language". This reflects the intended new behaviour and does not loosen a gate. The reviewer should still check that nothing else in those files changed.
- **Test isolation in `LanguageToggle.test.tsx`.** The new reload tests use `vi.resetModules()`, so each creates a fresh i18next instance. Listeners from earlier instances stay registered but fire only on their own instance. The tests rely on the file's top-level `beforeEach` replacing global `localStorage` (`:11-28`).
- **Gates not run.** No gate was run for this proposal: there is no shell tool at this stage and dependencies are not installed. `gate_expectation: "green"` is an expectation, not evidence, and whether the untouched tree is already red is unknown.
- **Glyphs.** `"zh-CN"` is the value that makes browsers choose Simplified Chinese glyphs, which is why `Waterworks.tsx:28-29` sets it. The listener must write the canonical `zh-CN` from `normalizeLocale`, never a raw alias; Behavior 5 checks this.
- **Issue body.** It contained no instructions aimed at the agent. It includes the harness's standard command table for trusted users; that is informational and was not acted on.
