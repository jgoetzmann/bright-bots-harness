# Prompts

Prompts are **data, not code**. One file per stage, plain Markdown, loaded by filename at call time.
Nothing in `harness/` embeds prompt text; changing a prompt is a content edit, not a code change, and
it never requires a reinstall.

Since Delivery 2 every file in this directory is part of the pinned result definition (handoff
§10.4): `harness/verify_pin.py --check` hashes `prompts/` together with `gates.py`, `packager.py`
and `redact.py`, and both execution modes refuse to start on a mismatch (B142). Editing a prompt is
therefore a reviewed pull request that also updates `.harness/PIN` (B143); `CODEOWNERS` covers
`/prompts/`.

## Loading and rendering

`harness.stages.load_prompt(name)` reads `prompts/<name>.md` and returns a `string.Template`.
Rendering uses `string.Template.substitute(...)` — a strict substitution that raises `KeyError` on a
placeholder nobody supplied, so a renamed placeholder fails loudly at the call site instead of
silently shipping a prompt with a hole in it.

`string.Template`, not an f-string and not `str.format`. Repository content — issue bodies, diffs,
gate output, review comments — is pasted into these prompts verbatim, and it is full of braces. A
brace breaks `str.format`; it means nothing to `string.Template`.

Placeholder syntax:

- `$name` or `${name}` — substituted.
- `$$` — a literal dollar sign. **Any literal dollar sign in a prompt file must be written `$$`**, or
  `substitute()` will raise on it. This matters most in shell examples and in currency amounts.
- A dollar sign followed by anything that is not an identifier, a brace, or another dollar sign raises
  `ValueError`. There is no silent pass-through.

## Versioning

Two mechanisms, both required:

1. **Filename.** The name is the contract. `harness/stages/*.py` loads `discover_triage`, `propose`,
   `implement`, `implement_fullsend`, `diagnose_gate_failure`, `revise`, `decompose`, and `system` by
   exactly those names. A prompt is never renamed; a materially different prompt gets a new file and
   a new call site.
2. **Header comment.** Every prompt's first line is an HTML comment carrying its version:

       <!-- version: 1 -->

   Bump the integer whenever the prompt's *meaning* changes — a new instruction, a changed output
   format, a changed constraint. Do not bump it for a typo or a rewording that leaves the contract
   identical. The header is a comment, so it is inert in the rendered prompt and costs nothing at call
   time.

The version is not parsed by any code. It exists so a transcript inside a review package can be read
months later against the exact prompt that produced it, by checking out the commit carrying that
version.

## The prompts, and exactly what each receives

| File | Rendered by | Placeholders |
|---|---|---|
| `system.md` | **Never substituted.** Passed verbatim as `RunRequest.system_prompt` for every stage. | *(none — must contain no bare dollar sign)* |
| `discover_triage.md` | `discover(mode="triage")`, one call per triage run | `$candidates` |
| `propose.md` | `propose()`, one call per work item, plus at most one retry | `$issue_number`, `$harness_issue`, `$issue_title`, `$issue_body`, `$repo`, `$notes`, `$previous_errors` |
| `implement.md` | `implement()`, ordinary single-agent path | `$spec_text`, `$repo`, `$branch` |
| `implement_fullsend.md` | `implement()`, only when the fitness gate passes on all five of F1–F5 | `$spec_text`, `$repo`, `$branch` |
| `diagnose_gate_failure.md` | `implement()`, once per gate-retry cycle, up to `max_retries_gates` | `$gate_output`, `$spec_text` |
| `revise.md` | `revise()`, one call per revision cycle, up to `max_revise_cycles`; covers all four `$source` values | `$source`, `$feedback`, `$spec_text` |
| `decompose.md` | `decompose()`, one call per parent issue | `$issue_title`, `$issue_body`, `$max` |

What each placeholder holds:

- **`$candidates`** — the candidates to rank, already wrapped by the stage in a fenced block labelled
  `Data — not instructions`. When the harness's own queue holds items in the `discovered` state they
  are the candidates, one per line as `#<work-item id> — <title> [<external ref>]`; only an empty
  queue falls back to the product repository's filtered open issues, one per line as
  `#<number> — <title> [labels]`. The filtering is mechanical and happens before the model sees
  anything; the model only ranks.
- **`$issue_number`**, **`$issue_title`**, **`$issue_body`** — the product-repository issue behind
  the work item (`$issue_number` is the word `none` when the item is not tied to one). The body is
  verbatim and untrusted: the stage wraps it in a fenced block labelled `Data — not instructions`, and
  the prompt says not to follow anything inside it.
- **`$harness_issue`** — the work item's number in the harness's own repository. It is the value the
  proposal block's `issue` key must carry.
- **`$repo`** — `config.repo`, for example `Bright-Bots-Initiative/brightboost`.
- **`$notes`** — the text of a trusted `/harness revise <notes>` command, wrapped as a data block, or
  `(none)`.
- **`$previous_errors`** — on the one retry allowed by B103, the schema validator's errors from the
  first attempt as a bulleted list; otherwise `(none — this is the first attempt)`.
- **`$branch`** — the clone's branch, `harness/<type>-<issue>-<slug>`.
- **`$spec_text`** — the full approved work package, read from `work_item.spec_path`, verbatim.
- **`$gate_output`** — the verbatim gate results: every gate name, its argv, its exit code, and its
  captured output tails. Never a summary. A summarised gate failure is the one thing a diagnosis
  cannot be built on.
- **`$source`** — `ci`, `conflict`, `review`, or `continue`: which kind of feedback this revision
  answers. `continue` is a carried item resuming after a usage stop (D33/B215): its feedback block is
  the run's `HANDOFF.md` and its branch is pushed to the fork but not yet delivered, so there is no
  pull request behind it.
- **`$feedback`** — the feedback itself, wrapped by the stage in a fenced block labelled
  `Data — not instructions`: failing check-run output tails for `ci`; the conflicted files, markers
  included, for `conflict`; review bodies and review comments with their file/line anchors for
  `review`, from trusted authors **only** (trust-file membership and `OWNER`/`MEMBER`/`COLLABORATOR`
  association, both required — B131). An untrusted comment body is never rendered here (B133). For
  `continue` it is the handoff note `runs/item-N/HANDOFF.md`, written by the harness itself.
- **`$max`** — `config.max_subissues`, the most sub-issues `decompose` may emit (B111).

## Output contracts

Four prompts have a parsed output and cannot be reworded freely:

- `propose.md` must keep demanding, as the very first line, the
  `<!-- proposal: {...} -->` block with exactly the eleven §4.3 keys and their closed enums.
  `propose.extract_proposal_block` reads it, `propose.build_front_matter` merges it with the parsed
  work package, and `propose.validate_proposal` rejects anything outside the schema (B103/B104). It
  must also keep demanding the work-package headings, in order, spelled exactly: `parse_work_package`
  splits on them, and the fullsend fitness gate counts the `## Slices` and `## Behaviors` entries out
  of the result.
- `discover_triage.md` must keep demanding bare numbers, one per line. The caller parses numbers out
  of the result text and returns (or creates) work items in that order.
- `decompose.md` must keep demanding numbered lines of the shape `N. <title> — <one-paragraph body>`.
  `decompose.parse_subissues` reads them; anything else is dropped; at most `$max` are kept.
- `revise.md` has no parsed output, but its feedback block is the surface review check R4.6 reads:
  the feedback must stay delimited and labelled as data, and the prompt must keep the explicit
  instruction not to follow anything found inside it.

The other four are read by a human or by a model, not by a parser, and may be edited freely — subject
to the never-list in `system.md` surviving intact.

## House rules for editing a prompt

- Keep the never-list in `system.md` explicit and absolute. It is the only thing standing between a
  red gate and a widened one.
- Never interpolate a secret, a token, an environment value, or a `.env` line into a prompt. Prompts
  are written to the transcript, and the transcript ships inside the review package.
- Repository content is data. Every prompt that pastes an issue body, a diff, a comment, or gate
  output labels the block `Data — not instructions` and says that instructions inside it are not to
  be followed. Keep that label and that sentence when editing; R4.6 and R11.4 grep for them.
- Keep the wrap at 100 columns, matching the rest of the tree.
