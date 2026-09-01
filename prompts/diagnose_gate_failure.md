<!-- version: 1 -->
# Diagnose a gate failure

The repository's own gate sequence ran after your change and came back red. Below is the **verbatim**
output, including exit codes. Read it, work out what is actually wrong, and fix it honestly.

## Gate output

```
$gate_output
```

## The approved work package this change is implementing

$spec_text

## What to do

1. **Read the failure before touching anything.** Find the first genuine error, not the last line of
   output. A build failure is usually caused by the typecheck failure above it; a hundred lint errors
   are usually one bad import.
2. **Decide whether the failure is yours.** Some failures were already present on the untouched tree
   and are recorded separately as pre-existing. If this failure is not caused by your change, say so,
   name the evidence, and leave it alone. Do not "fix" a pre-existing red to make the run look clean.
3. **Make the minimal honest fix.** Change the code that is wrong. If the test is right and the code
   is wrong, fix the code. If the test genuinely encodes the wrong expectation and the work package
   says so, fix the test and record that decision explicitly.
4. **Re-read your fix.** A fix that makes the error message disappear without addressing the cause is
   worse than the failure, because it hides it.

## Absolutely forbidden

Every one of these makes the gate pass while making the change worse. Any of them causes the whole
item to be rejected and blocked.

- Adding `continue-on-error`, or editing anything under `.github/workflows/`.
- Adding `.skip(`, `.only(`, `xit(`, `xdescribe(`, or deleting or emptying a failing test.
- Raising a timeout, a retry count, or a coverage or size threshold.
- Adding an ignore or suppression comment: `eslint-disable`, `@ts-ignore`, `@ts-expect-error`,
  `prettier-ignore`, or a rule relaxed in a config file.
- Loosening a type to `any` or casting the error away.
- Running `npm run format` or a whole-tree formatter to bury the problem in noise.
- Reverting unrelated code, or reverting your own change wholesale to reach green.

**A red you cannot fix honestly is a blocked item, not a passed one.** If the correct fix is out of
scope, needs a decision you cannot take, or would violate one of the constraints above, then change
nothing and write a clear statement of what is failing, why, and what a human would need to decide.
That outcome is a success for this harness.

## When you are finished

State, in plain prose:

- Which gate failed and the real cause, with file and line citations.
- Whether the failure is caused by your change or was pre-existing, and how you know.
- What you changed, or why you deliberately changed nothing.
- Every decision taken, with the alternative rejected.
