---
issue: 109
upstream_issue: null
title: "test(shared): make IconButton click test fail when the button is absent"
kind: test
slices: 1
risk: low
touched_paths:
  - "src/components/shared/__tests__/IconButton.test.tsx"
depends_on: []
estimated_turns: 10
gate_expectation: green
baseline_red: []
---

# test(shared): make IconButton click test fail when the button is absent

## Issue

Harness issue #109, which tracks finding 15 of audit jgoetzmann/bright-bots-harness#61 (`audit:61:15`). There is no product-repository issue. The audit rated it medium and pointed at `querySelector`, `null`, `expect(null).toBeDefined()`, `expect(handleClick).toHaveBeenCalledTimes(1)` and `if (button)`. In the reporter's terms: the `IconButton` click test passes even when the button is not there.

## Diagnosis

The defect is in the test, `src/components/shared/__tests__/IconButton.test.tsx`, in the case `"fires onClick handler when clicked"` (lines 40–56). The component is fine.

- Line 49: `const button = container.querySelector("button");`. When nothing matches, `querySelector` returns `null`, not `undefined`.
- Line 50: `expect(button).toBeDefined();`. `toBeDefined` only checks `!== undefined`, so it passes when `button` is `null`. The existence check can never fail.
- Lines 52–55: `if (button) { fireEvent.click(button); expect(handleClick).toHaveBeenCalledTimes(1); }`. The test's only meaningful assertion sits inside this guard. With no button, the guard is skipped, no assertion fails, and Vitest reports a pass.

So if `IconButton` rendered nothing, the test would pass without asserting anything. The same happens if it rendered a non-`<button>` element with `role="button"`: `querySelector("button")` would miss it and the test would pass silently. The test only goes red when a `<button>` is present and the click wiring is broken.

Today the button is present. `IconButton.tsx:30-41` renders a `<button>` with `onClick={onClick}` (line 33) and `aria-label={ariaLabel || title}` (line 35). With the default `showTooltip = true` (line 25), it is wrapped in `<TooltipTrigger asChild>` (lines 43–51). The sibling test at `IconButton.test.tsx:36` already finds this button with `screen.getByRole("button", { name: "Edit item" })` in the same tooltip-enabled setup. So the guarded branch runs today, and the problem is that the test cannot catch the button going missing.

Only this test has the pattern: a search of `src/` for `querySelector("button")` and `if (button)` matches only lines 49 and 52 of this file. The other `toBeDefined()` calls in this file (lines 25, 65, 79, 89) come after `getAllBy*`/`getBy*` queries, which throw when nothing matches. Those checks are redundant, but they can't pass vacuously. Coverage thresholds in `vitest.config.ts:33-52` don't include `src/components/shared/**`, so this change has no effect on coverage gates.

## Approach

Rewrite the body of that one test so that a missing button fails it and the click assertion always runs:

- Replace `container.querySelector("button")` with `screen.getByRole("button", { name: "Edit item" })`. `getByRole` throws a descriptive error when no match exists. It is also the query the neighbouring tests already use (lines 36, 64, 78), so it matches the file's conventions and queries by accessible role and name rather than tag.
- Remove `expect(button).toBeDefined()`, which checks nothing.
- Remove the `if (button)` guard. Call `fireEvent.click(button)` and `expect(handleClick).toHaveBeenCalledTimes(1)` directly in the test body.
- Stop destructuring `{ container }` from `renderWithTooltip(...)`, since it is no longer used.
- Keep the default tooltip-enabled render, so the test still clicks through the Radix `TooltipTrigger asChild` wrapper as it does now.

Run Prettier on this one file only. Proposed commit header: `test(shared): make IconButton click test fail when the button is absent`.

## Slices

1. In `src/components/shared/__tests__/IconButton.test.tsx`, rewrite `"fires onClick handler when clicked"` to get the button with `screen.getByRole("button", { name: "Edit item" })`, drop the `toBeDefined` check and the `if (button)` guard, click it, and assert `handleClick` was called once, unconditionally.

## Behaviors

1. With `IconButton` rendered using the default tooltip and `title="Edit item"`, clicking the button named "Edit item" calls `onClick` exactly once, and the test passes.
2. If no element with role `button` and accessible name "Edit item" is rendered, the test fails with a Testing Library "Unable to find" error instead of passing.
3. `expect(handleClick).toHaveBeenCalledTimes(1)` runs on every run of the test, not behind a condition.
4. The other five tests in `IconButton.test.tsx` are unchanged and still pass.

## Acceptance criteria

- The diff changes only `src/components/shared/__tests__/IconButton.test.tsx`, and only inside the `"fires onClick handler when clicked"` test (current lines 40–56).
- That test no longer contains `querySelector`, `toBeDefined`, or `if (button)`.
- That test gets its button with `screen.getByRole("button", { name: "Edit item" })`.
- `fireEvent.click(button)` and `expect(handleClick).toHaveBeenCalledTimes(1)` sit directly in the test body, not inside any conditional or callback.
- The test name is unchanged, and it still renders through `renderWithTooltip` with the tooltip enabled.
- No `.skip(`, `.only(`, timeout, ignore comment, or config change appears anywhere in the diff.
- `npm run test:unit` passes, with `IconButton.test.tsx` reporting 6 passing tests.
- `npm run lint` and `npm run typecheck` pass.
- Prettier was applied to the changed file only. No other file shows formatting changes.

## Decisions

- Use `screen.getByRole("button", { name: "Edit item" })`, not `getByRole("button")` without a name. The name pins the query to the `aria-label` that `IconButton.tsx:35` sets, and matches the sibling tests at lines 36/64/78. Rejected: an unnamed role query, which is weaker and could match an unrelated button.
- Use a throwing `getBy*` query, not keeping `querySelector` and swapping `toBeDefined()` for `not.toBeNull()` or `toBeInTheDocument()`. Rejected because the test would still query by tag, and because the fix would rely on remembering to drop the guard. A throwing query fixes the missing-button case by construction.
- Don't add `toBeInTheDocument()` after `getByRole`. Rejected as redundant: `getByRole` already throws on no match, and adding it would repeat the "assert what the query already guarantees" pattern.
- Keep the tooltip-enabled render, not switching to `showTooltip={false}`. Rejected because the tooltip path is the default and the more complex one: the `onClick` has to reach the button through `TooltipTrigger asChild` (`IconButton.tsx:46`). Dropping it would weaken what the test covers.
- Don't touch the redundant `toBeDefined()` calls after `getBy*` at lines 25, 65, 79, 89, or similar calls in other files. Rejected as out of scope: they can't pass vacuously, and changing them would be adjacent refactoring the finding didn't ask for.
- Don't add a permanent negative test (for example, rendering nothing and expecting a throw). Rejected because it would test Testing Library's behaviour, not `IconButton`'s. Reading the new test is enough to see that behavior 2 holds.
- Set `gate_expectation` to `green` with an empty `baseline_red`. This is an expectation, not evidence: this proposal step has no tool that can run commands, so no gate was run on the untouched tree.

## Open questions

- No gate was run on the untouched tree while writing this proposal, and `node_modules` is not installed in the clone. The implementation run has to establish the baseline before claiming `green`. If a gate is already red, this package's `gate_expectation` is wrong and should be corrected, not worked around.
- Should the redundant `toBeDefined()` calls after `getBy*` (this file's lines 25, 65, 79, 89; `LanguageToggle.test.tsx:88,113`; and others) be cleaned up as a separate item? They are harmless but misleading, and this package deliberately leaves them alone.

## Touched paths

- src/components/shared/__tests__/IconButton.test.tsx

## Risks

- **Removing the guard exposes the assertion for real.** I read the component and the wrapper at `src/components/ui/tooltip.tsx:10` (a direct re-export of `TooltipPrimitive.Trigger`). I could not read the Radix `Slot` / `TooltipTrigger` source, because `node_modules` is not installed. I expect Radix to call the child's `onClick` once and then its own, which would give `toHaveBeenCalledTimes(1)`. If the unguarded test shows a different count, that is a real defect the old guard would have hidden. The implementer must report it as blocked with the observed count, not re-add a guard or loosen the assertion.
- **What to check hardest:** that the click assertion is not inside any conditional, `try`, or callback, and that nothing else in the file changed apart from Prettier on this one file.
- **Low blast radius:** a single test file with no runtime, build, CI, `prisma/`, or migration change.
- **Issue body content:** the body includes the harness's steering-command table and links. That text is written for human maintainers, and I treated it as data. I found no instruction in it addressed to an AI agent asking to skip rules, run commands, or touch unrelated files.
