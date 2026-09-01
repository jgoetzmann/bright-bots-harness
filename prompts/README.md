# Prompts

Prompts are **data, not code**. One file per stage, plain Markdown, loaded by filename at call time.
Nothing in `harness/` embeds prompt text; changing a prompt is a content edit, not a code change, and
it never requires a reinstall.

## Loading and rendering

`harness.stages.load_prompt(name)` reads `prompts/<name>.md` and returns a `string.Template`.
Rendering uses `string.Template.substitute(...)` — a strict substitution that raises `KeyError` on a
placeholder nobody supplied, so a renamed placeholder fails loudly at the call site instead of
silently shipping a prompt with a hole in it.

`string.Template`, not an f-string and not `str.format`. Repository content — issue bodies, diffs,
gate output — is pasted into these prompts verbatim, and it is full of braces. A brace breaks
`str.format`; it means nothing to `string.Template`.

Placeholder syntax:

- `$name` or `${name}` — substituted.
- `$$` — a literal dollar sign. **Any literal dollar sign in a prompt file must be written `$$`**, or
  `substitute()` will raise on it. This matters most in shell examples and in currency amounts.
- A dollar sign followed by anything that is not an identifier, a brace, or another dollar sign raises
  `ValueError`. There is no silent pass-through.

## Versioning

Two mechanisms, both required:

1. **Filename.** The name is the contract. `harness/stages/*.py` loads `discover_triage`, `propose`,
   `implement`, `implement_fullsend`, `diagnose_gate_failure`, and `system` by exactly those names. A
   prompt is never renamed; a materially different prompt gets a new file and a new call site.
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
| `propose.md` | `propose()`, one call per work item | `$issue_number`, `$issue_title`, `$issue_body`, `$repo` |
| `implement.md` | `implement()`, ordinary single-agent path | `$spec_text`, `$repo`, `$branch` |
| `implement_fullsend.md` | `implement()`, only when the fitness gate passes on all five of F1–F5 | `$spec_text`, `$repo`, `$branch` |
| `diagnose_gate_failure.md` | `implement()`, once per gate-retry cycle, up to `max_retries_gates` | `$gate_output`, `$spec_text` |

What each placeholder holds:

- **`$candidates`** — the filtered open issues, one per line, as `#<number> — <title> [labels]`. Every
  issue in the list is already unassigned, unclaimed by any in-flight branch or PR title, free of the
  excluded labels, and carrying `config.allowlist_label`. The filtering is mechanical and happens
  before the model sees anything; the model only ranks.
- **`$issue_number`**, **`$issue_title`**, **`$issue_body`** — straight from the unauthenticated
  GitHub read of that issue. The body is verbatim and untrusted: it is repository content, not an
  instruction to the harness.
- **`$repo`** — `config.repo`, for example `Bright-Bots-Initiative/brightboost`.
- **`$branch`** — the clone's branch, `harness/<type>-<issue>-<slug>`.
- **`$spec_text`** — the full approved work package, read from `work_item.spec_path`, verbatim.
- **`$gate_output`** — the verbatim gate results: every gate name, its argv, its exit code, and its
  captured output tails. Never a summary. A summarised gate failure is the one thing a diagnosis
  cannot be built on.

## Output contracts

Two prompts have a parsed output and cannot be reworded freely:

- `propose.md` must keep demanding the work-package headings, in order, spelled exactly.
  `parse_work_package` splits on them, and the fullsend fitness gate counts the `## Slices` and
  `## Behaviors` entries out of the result.
- `discover_triage.md` must keep demanding bare issue numbers, one per line. The caller parses numbers
  out of the result text and creates work items in that order.

The other four are read by a human or by a model, not by a parser, and may be edited freely — subject
to the never-list in `system.md` surviving intact.

## House rules for editing a prompt

- Keep the never-list in `system.md` explicit and absolute. It is the only thing standing between a
  red gate and a widened one.
- Never interpolate a secret, a token, an environment value, or a `.env` line into a prompt. Prompts
  are written to the transcript, and the transcript ships inside the review package.
- Repository content is data. Say so in the prompt when pasting an issue body or a diff, so an
  instruction embedded in an issue is read as text to be examined rather than an order to follow.
- Keep the wrap at 100 columns, matching the rest of the tree.
