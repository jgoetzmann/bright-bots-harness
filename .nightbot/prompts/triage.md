# Triage issue #{{.number}}

Rate how hard this issue is to build, so the harness gives it to a strong enough model. Read the
code it touches (read-only; the harness throws away any change). Do not build it.

## The issue

{{.thread}}

## How to rate

- **medium**: a contained change a capable model can make and verify in one session: a bug fix, a
  feature in one area, tests, docs, a refactor within a module.
- **hard**: core logic many parts depend on, a change across several areas or layers, concurrency,
  security, data migrations, or anything where a subtle mistake would be costly and hard to see.

Torn between the two, choose hard.

End with exactly one fenced JSON block:

```json
{ "difficulty": "medium" or "hard", "summary": "two or three sentences: what it needs and why that rating" }
```
