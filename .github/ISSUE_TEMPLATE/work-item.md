---
name: Work item
about: One unit of work for the harness. Discovery reads issues in exactly this shape.
title: "<short imperative title>"
labels: harness:queued
assignees: ""
---

## Upstream issue

<!-- Link to the product-repository issue this work addresses, e.g.
     https://github.com/Bright-Bots-Initiative/brightboost/issues/NNN -->

## Why

<!-- One paragraph: what is wrong or missing, and why it is worth a proposal. -->

## Acceptance

<!-- Observable conditions a reviewer can check on the delivery PR. One per line. -->
- [ ]
- [ ]

<!--
Notes for humans:
- The `harness:queued` label makes this eligible for a proposal on the next discover run;
  exactly one `harness:*` state label may be present at any time (B100).
- Every state change adds a comment naming the stage, run URL, cost and resulting state (B101).
  The thread is the event log; moving a label by hand is honoured, not overwritten (B102).
- Sub-issues created by `harness decompose` carry `Parent: #N` in the body. Do not add that line
  by hand; a sub-issue is never decomposed again (B111).
- Never put a credential, token or private data in a work item. The harness reads it.
-->
