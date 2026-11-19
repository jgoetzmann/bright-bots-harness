# Plan: i18n DataDashSortDiscoverGame (full-component, G3-5)

- For: https://github.com/jgoetzmann/bright-bots-harness/issues/174
- Answers: https://github.com/Bright-Bots-Initiative/brightboost/issues/642
- Written by claude-opus-5-5 on claude-day, against `main`

## Summary
`DataDashSortDiscoverGame` (G3‑5, gameKey `data_dash_sort_discover`) doesn't use i18n at all. Every string it shows is hardcoded English. The default challenge's chart questions are hardcoded too, in `dataDashAuthoring.ts`. This plan sends all of it through `t("games.dataDash.*", { defaultValue })`: title, briefing, phase copy, attribute names, attribute values, plant names and chart questions. The English goes into `src/locales/en/common.json` only and nothing is machine-translated. English players see the same text, with one deliberate fix: the Phase B buttons now show "Seed type" instead of the raw identifier `seedType`.

## Diagnosis
- `src/components/games/DataDashSortDiscoverGame.tsx:1-5` never imports `react-i18next`, so nothing on the page is translatable. The hardcoded copy:
  - `:37-41`: `SORT_RULES` labels ("Sunlight need", "Water need", "Leaf type").
  - `:78-89`: the module-level `BRIEFING` const (title, story, three tips, `chapterLabel` "Life Science Data", the keyboard/button control instructions). It is a module constant, so it can't call `t()` and must move into the component, as Maze Maps does (`MazeMapsGame.tsx:1068-1104`) and Rhyme Ride does (`RhymeRideGame.tsx:974-1001`).
  - `:149`: "Phase A/B/C: Sort by Attribute / Infer the Rule / Read the Chart", assembled from ternaries.
  - `:154` "Rule: Sort all cards by **{label}**."; `:159` "Bed … • Sun: … • Water: … • Leaf: … • Seed: … • Growth: …"; `:160` "Assigned bin:"; `:167` `Place in "{value}" bin`; `:170/:193/:220` "Hint"; `:172/:195` "Submit Phase A/B"; `:221` "Complete Mission".
  - `:178` the infer prompt; `:182` "Group {label}"; `:201` "Chart from your Phase A sort ({label}):"; `:205` the chart rows.
  - `:237`: the `title="Data Dash: Sort & Discover"` prop.
- Raw data values reach the screen as-is: plant names (`card.name`, `:158`, `:184`), attribute values such as `full`, `pod`, `medium` (`:159`, `:160`, `:167`, `:182`, `:205`), and at `:191` the infer options. Those options are camelCase keys (`dataDashAuthoring.ts:163` gives `["sunlightNeed","waterNeed","seedType","growthSpeed"]`), so children currently see buttons labelled `seedType`.
- The chart questions rendered at `:212` and `:215` are English, built in `src/components/games/dataDashAuthoring.ts`. The default questions are at `:164-175` and the derived prompt template is at `:141`. The comment at `:32-34` says outright that `SORT_RULES` "is not yet i18n'd".
- Things that limit the approach:
  - Plant names can't become a per-locale map on `DataCard`, which is how TankTrek handles its content with `pickLocale()`. Adding a field to `DataCard` breaks the frontend/backend pool guard (`src/test/dataDashPoolSync.test.ts:28` and `:68-80`, `src/components/games/dataDashAttrsExhaustiveness.ts:23`). Names have to be translated by card `id`.
  - `SORT_RULES[rule].label` is also used by `src/pages/CreateChallenge.tsx:57` to build the creation **title saved to the backend**, and as the English default in `dataDashAuthoring.ts:141`. Its shape and value should stay as they are.
  - `GameInstructionCoverage.test.tsx:14` mocks `t` as `defaultValue ?? key` and asserts the English control instructions (`:33-38`). Every new `t()` call therefore needs a `defaultValue`.
- No typed i18next resources exist (no `CustomTypeOptions`), so template-literal keys compile; `SkyShieldGame.tsx:185-186` already does this. `src/i18n.ts:56` sets `fallbackLng: "en"`, so es/vi/zh-CN render the English entries when a key is missing.

## Approach
- **`t()` with `defaultValue` for everything, including content data.** I considered TankTrek's `pickLocale()`, but it needs per-locale maps in code (there are no translations to put in them) and a new field on `DataCard`, which breaks the pool sync guard. Keying by `id` or value avoids both:
  - `games.dataDash.plants.<id>`
  - `games.dataDash.attributes.<attr>`
  - `games.dataDash.values.<attr>.<value>` (per attribute, because `medium` appears under both `waterNeed` and `growthSpeed` and translators may need different words)
- **The English defaults come from data that already exists**, so there's no second English table: `card.name`, the raw value string, and the existing `ATTR_LABELS` (`dataDashAuthoring.ts:35-41`). `ATTR_LABELS` is read at render time only, which is safe with the existing import cycle described at `dataDashAuthoring.ts:52-56`. `SORT_RULES` keeps its shape and English `label`, so `CreateChallenge` and the saved titles don't change. The game simply stops rendering `SORT_RULES[rule].label` directly.
- **Chart questions get optional keys.** `ChartQuestion` gains `promptKey?: string` and `choiceKeys?: string[]`. `prompt` and `choices` stay English, and they remain the React keys and the `defaultValue`, so scoring and `answerIndex` are untouched. The playfield translates a question when keys are present and falls back to the English otherwise.
- **The bold rule label stays without adding `<Trans>`.** `<Trans>` isn't used anywhere in `src/`. The sentence becomes a lead key followed by `<strong>{label}</strong>.`, matching the `createChallenge.titlePrefix` + label pattern.
- **Keys go in `en/common.json` only.** The issue says "English in place; other locales fall back … Do NOT machine-translate." This conflicts with `docs/agents/rules/20-i18n.md` ("en.json **and** es.json at minimum"); see Open questions.

## Steps
1. **Add `games.dataDash` to `src/locales/en/common.json`** inside the `games` object (starts at line 1515), for example right after `mazeMaps`. Text keys:
   - `title`: "Data Dash: Sort & Discover"
   - `briefingStory`: the story text from `:80`, unchanged
   - `chapterLabel`: "Life Science Data"
   - `tipOneAttribute`, `tipGroupShare`, `tipChartEvidence`: the three tips from `:82`
   - `controlsKeyboard`, `controlsSelect`, `controlsEvidence`: the control strings from `:86-87`
   - `phaseA`: "Phase A: Sort by Attribute"; `phaseB`: "Phase B: Infer the Rule"; `phaseC`: "Phase C: Read the Chart"
   - `sortRuleLead`: "Rule: Sort all cards by"
   - `cardDetails`: "Bed {{bed}} • Sun: {{sun}} • Water: {{water}} • Leaf: {{leaf}} • Seed: {{seed}} • Growth: {{growth}}"
   - `assignedBin`: "Assigned bin:"
   - `placeInBin`: `Place in "{{value}}" bin`
   - `hint`: "Hint"; `submitPhaseA`; `submitPhaseB`; `completeMission`
   - `inferPrompt`: the text from `:178`
   - `groupLabel`: "Group {{group}}"
   - `chartIntro`: "Chart from your Phase A sort ({{rule}}):"
   - `chartRow`: "{{value}}: {{count}}"

   Nested keys:
   - `attributes`: the five entries, identical to `ATTR_LABELS`.
   - `values.<attr>.<value>`: all 15 attribute/value pairs, English equal to the raw value.
   - `plants.<id>`: all 8 plants.
   - `chart`: `claimPrompt`, `claimFullSun`, `claimShade`, `claimPartialSun`, `evidencePrompt`, `evidenceFullSunBar`, `evidenceFernFronds`, `evidenceBedA` (text from `dataDashAuthoring.ts:166-172`), and `mostPlantsPrompt`: `Which "{{rule}}" group has the most plants?`.
2. **`dataDashAuthoring.ts`:**
   - Add the optional `promptKey` / `choiceKeys` to `ChartQuestion` (`:50`).
   - In `defaultResolved()` (`:164-175`), set the `games.dataDash.chart.*` keys, with `choiceKeys` in the same order as `choices`.
   - In `buildDerivedChartQuestion` (`:140-144`), set `promptKey: "games.dataDash.chart.mostPlantsPrompt"` and `choiceKeys: values.map(v => \`games.dataDash.values.${sortRule}.${v}\`)`.
   - Rewrite the stale comment at `:32-34`: the game now translates attribute labels, `ATTR_LABELS` are the English defaults, and the `CreateChallenge` chrome around them is still English.
3. **`DataDashSortDiscoverGame.tsx`: title and briefing.** Import `useTranslation` and `ATTR_LABELS`. Delete the module-level `BRIEFING` and build the briefing inside the default export with `t("games.dataDash.*", { defaultValue: <current English> })`. Use the same key for the `title` prop. Leave `icon` and `themeColor` unchanged.
4. **`DataDashPlayfield`: add `const { t } = useTranslation()`** and three local helpers, in the style of `SkyShieldGame.tsx:185`:
   - `attrLabel(attr)` → `t(\`games.dataDash.attributes.${attr}\`, { defaultValue: ATTR_LABELS[attr] ?? attr })`
   - `valueLabel(attr, value)` → `t(\`games.dataDash.values.${attr}.${value}\`, { defaultValue: value })`
   - `plantName(card)` → `t(\`games.dataDash.plants.${card.id}\`, { defaultValue: card.name })`
5. **Replace every literal listed in the Diagnosis (`:149`–`:221`)** with the matching key, passing `defaultValue` equal to the English template.
   - Keep the rendered English text identical. Use `{" "}` where JSX text used to run into `<strong>`, and keep the `—` placeholder.
   - Assignments, `evaluateSortAssignment`, `inferChoice === inferRule` and the React `key`s keep using the raw values and option keys; only what is displayed changes.
   - The infer buttons (`:191`) render `attrLabel(option)`.
   - Chart prompts render `q.promptKey ? t(q.promptKey, { rule: attrLabel(rule), defaultValue: q.prompt }) : q.prompt`. Choices are handled the same way with `q.choiceKeys?.[idx]`.
6. **Leave `SORT_RULES`, `DATA_DASH_CARDS`, `DataCard`, the scoring and the payload helpers unchanged.**
7. **Run all the checks** (install, prisma generate, lint, typecheck, backend typecheck, drift script, `test:unit`, build).

## Tests
**New file: `src/components/games/__tests__/DataDashSortDiscoverI18n.test.tsx`**

Setup:
- **Translation mock.** Mock `react-i18next` from `enMock()` (`src/test/i18nMock.ts`). Its `t` wraps `enTranslate` and records every `(key, opts)` call into a `vi.hoisted` array that is cleared in `beforeEach`. `enTranslate` returns the **raw key** when a key is missing from `en/common.json` (`i18nMock.ts:47-49`).
- **GameShell mock.** Use the same pattern as `MoveMeasureLocales.test.tsx:22-25`. It renders `title`, `briefing.title`, `story`, `chapterLabel`, `tips`, `controlInstructions.keyboard`/`buttons`, then `children({ onFinish: onComplete, reducedEffects: false })`.

Cases:
1. **"default challenge requests every string through `games.dataDash` keys".** Render with no config and play through all three phases:
   - Phase A: for each card, click `getByText(name).closest("button")`, then `Place in "full" bin`, then Submit Phase A.
   - Phase B: click "Seed type", then Submit Phase B.
   - Phase C: pick both answers, then Complete Mission.

   It asserts:
   - **Keys were requested.** The recorded calls include an explicit list: `title`, `briefingStory`, `chapterLabel`, the three tips, the three control strings, `phaseA/B/C`, `sortRuleLead`, `cardDetails`, `assignedBin`, `placeInBin`, `hint`, `submitPhaseA/B`, `completeMission`, `inferPrompt`, `groupLabel`, `chartIntro`, `chartRow`, `attributes.seedType`, `values.sunlightNeed.full`, `values.seedType.pod`, `plants.bean`, `chart.claimPrompt`, `chart.evidencePrompt`, `chart.claimFullSun` (all under `games.dataDash.`). **This fails on today's code**, which calls `t` with none of these keys.
   - **No raw identifiers on the buttons.** No button is named `seedType`, and buttons "Sunlight need" and "Seed type" exist. **This fails today** (`:191` renders raw keys).
   - **English is unchanged.** These are visible: "Data Dash: Sort & Discover", the story, "Phase A: Sort by Attribute", "Rule: Sort all cards by", `Place in "full" bin`, "Group pod", "Chart from your Phase A sort (Sunlight need):", "full: 4", "Which claim is best supported by the chart?".
   - **No missing keys.** `document.body.textContent` never contains `games.dataDash.` in any phase, which proves every key exists in `en/common.json`.
   - **`defaultValue` matches `en.json`.** Every recorded `games.dataDash.*` call has `opts.defaultValue === enTranslate(key)`, so the English fallback can't drift from the JSON.
   - **Scoring is untouched.** `onComplete` receives `gameKey: "data_dash_sort_discover"` and `total: 11`.
2. **"authored challenge translates the derived chart question".** Render with `config.challenge = { v: 1, cardIds: ["bean","fern","pine","moss"], sortRule: "waterNeed", inferRule: "growthSpeed" }` (the fixture from `backend/src/services/dataDashChallenge.test.ts:16-21`) and play to Phase C. It asserts:
   - `Which "Water need" group has the most plants?` and the buttons "low", "medium", "high" are visible.
   - A recorded call has key `games.dataDash.chart.mostPlantsPrompt` with `opts.rule === "Water need"`, and another has key `games.dataDash.values.waterNeed.high`. **This fails today**, because there is no `promptKey` and the game has no `t`.

**Existing tests, expected unchanged and passing:**
- `GameInstructionCoverage.test.tsx`: passes because the control strings keep `defaultValue`.
- `DataDashSortDiscover.test.ts` (pure helpers; `buildChartCounts` still labels by raw value).
- `dataDashPoolSync.test.ts` (`DataCard` and `SORT_RULES` keys are untouched).

## Acceptance criteria
- `DataDashSortDiscoverGame.tsx` imports `useTranslation`, and no English string literal is left in its JSX, briefing or `title` prop. Raw values and `—` are the only non-`t()` text.
- Every `t()` call in the component passes a `defaultValue` equal to the `en/common.json` value for that key.
- `src/locales/en/common.json` contains `games.dataDash` with every key listed in Step 1. `es`, `vi` and `zh-CN` are unchanged.
- In English, the briefing, all three phases and the chart questions render exactly the same text as today, except Phase B option buttons, which read "Sunlight need / Water need / Seed type / Growth speed" instead of camelCase keys.
- `SORT_RULES`, `DATA_DASH_CARDS`, `DataCard`, the scoring helpers and `src/pages/CreateChallenge.tsx` are unchanged.
- `ChartQuestion` gains only the optional `promptKey` and `choiceKeys`; `prompt`, `choices` and `answerIndex` are unchanged.
- The new test file exists, and both cases fail against the current `main` source and pass after the change.
- install, `npx prisma generate`, `npm run lint`, `npm run typecheck`, backend typecheck, `bash scripts/check-prisma-drift.sh`, `npm run test:unit` and `npm run build` all pass.

## Touched paths
- src/components/games/DataDashSortDiscoverGame.tsx
- src/components/games/dataDashAuthoring.ts
- src/locales/en/common.json
- src/components/games/__tests__/DataDashSortDiscoverI18n.test.tsx

## Risks
- **Visible UX change.** Phase B buttons stop showing camelCase identifiers. This is deliberate (an identifier can't be translated), but the reviewer should confirm `inferChoice === inferRule` still compares raw option keys and not the displayed labels.
- **Rendered text must stay identical.** Splitting `:154` and `:160` into a lead key plus `<strong>` can lose the space before the bold word; the reviewer should check that "Rule: Sort all cards by Sunlight need." and "Assigned bin: full" are unchanged. The split also fixes the English word order for future translators; switching to `<Trans>` later would fix that but would be a new pattern for this repo.
- **Import cycle.** `ATTR_LABELS` must only be read inside render, never at module-eval time in the game file (`dataDashAuthoring.ts:52-56` explains the cycle).
- **Pool sync guard.** Adding any field to `DataCard` (such as `names`) would break `dataDashPoolSync.test.ts:68-80` and the exhaustiveness type; the plan avoids touching it.
- **Non-English players still see English.** es/vi/zh-CN keep showing English through `fallbackLng`. That is expected under the issue, but it means this work doesn't move the Spanish pilot journey forward for Data Dash.
- **Nothing in the issue tried to instruct the bot.** It contained no instructions addressed to an AI and no requests to change unrelated files.

## Open questions
1. **Where should the new keys go?** The issue says to put English in place, let other locales fall back and not machine-translate. `docs/agents/rules/20-i18n.md` says new copy lands in `en` **and** `es` at minimum, and `docs/i18n.md` says to copy the English verbatim into all four locales. This plan follows the issue and adds keys to `en` only. Should the builder instead copy the English values verbatim into `es` (and `vi` / `zh-CN`) so translators can find the keys in place? That isn't machine translation and looks the same to users.
2. **Should `CreateChallenge` be a separate issue?** It still shows English attribute buttons (`ATTR_LABELS`, `CreateChallenge.tsx:170, 194`), English plant names (`:145`) and English validation errors (`:209`, from `validateDataDashChallenge`), and it saves an English title built from `SORT_RULES[sortRule].label` (`:57`). This plan leaves all of that alone. Should it be filed separately, and should saved creation titles ever be localized, given that other members of the group see them?
