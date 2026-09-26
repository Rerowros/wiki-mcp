---
type: methodology
title: Research cycle — idea → search → analysis → finding → next idea
summary: The loop every agent runs — one decision-changing question per cycle, explicit stop rules, promote only what survives analysis, always leave a next step.
tags:
- method
- research-cycle
related:
- '[[proposal-branches-and-ci-gate]]'
- '[[retrieval-evaluation-golden-set]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
---

# Research cycle — idea → search → analysis → finding → next idea

`AGENTS.md` is the mechanics of writing to this wiki. This page is the method:
when an idea earns work, when to stop searching, and what may be promoted.

```text
idea (question) → search/study → analysis → finding/thesis → new ideas
       ↑                                                          |
       └──────────── queue the ones that would change a decision ─┘
```

One idea per cycle. Parallel agents may split the search phase of one idea; they
should not run twelve unrelated ideas and call it one cycle. Every cycle writes
exactly one log entry, and that entry says where it stopped.

## Phase 0 — Idea: a question, not a topic

Write one sentence that would change a decision if answered. "Read about MCP"
is a topic. "Does the current MCP revision still require dynamic client
registration?" is a question.

If the answer cannot land as a finding, a thesis update, a dated claim or a
flipped status, do not start.

Classify the question before searching:

| Class | Shape | Default output |
|---|---|---|
| **A. Public fact** | "What is the latest spec revision?" | dated finding + source |
| **B. Instrument verdict** | "Does this benchmark measure what it claims?" | finding on the instrument |
| **C. Mechanism** | "How does FTS5 rank a phrase match?" | concept page, then one practical sentence |
| **D. Local evaluation** | "Does this ranker beat that one on *our* golden set?" | measured rows; no invented score |
| **E. Never public** | internal details a vendor does not disclose | write `unknown / not public` and stop |

## Phase 1 — Search

Primary sources first: specifications, papers, official changelogs, dated
documentation. Create the `source` page before quoting it. Separate what was
confirmed from what stayed unknown.

Stop searching when:

- the question is answered, including "unknown / not public";
- two independent primary sources agree — or contradict, in which case stop
  fetching and move to analysis;
- the next source would add a footnote, not a fact anyone would act on;
- the question is Class E.

## Phase 2 — Analysis

A table is not analysis. Analysis is what the evidence **forbids you to say**.

- Every number and version carries a date and a source, or reads `unknown / not public`.
- Keep vendor, independent and this-wiki measurements in separate rows.
- Two sources disagreeing is a contradiction: open a `query`, never average.
- This wiki disagreeing with its own older page is supersession: write a new
  dated page with `supersedes`.

## Phase 3 — Findings and theses

Promote only what survived Phase 2.

| Type | When | Confidence |
|---|---|---|
| `finding` | Dated fact from named sources | high if primary source, medium otherwise |
| `thesis` | Working hypothesis that should steer later cycles | medium until independently confirmed |
| `synthesis` | Cross-cutting explanation | not a score |
| `query` | Still open | — |

Every finding ends with a `Falsifier:` line — the evidence that would kill it.
Every number a decision depends on is also a `claims:` entry.

## Phase 4 — Next ideas

List the questions the finding now makes urgent. The best one goes in the log
entry's `next`. If nothing is worth doing, write `CLOSED` — but say so
deliberately; an empty `next` means the cycle stopped at search.

## How a cycle is logged

One file under `wiki/log/`, rendered into `wiki/log.md` by `tools/build.py`:

```text
- Cycle: <question>. Class A/B/C/D/E. Search: [[research-slug]].
  Analysis: <one sentence>. Output: [[finding-slug]]. Next: <question or CLOSED>.
```

Worked example: [[mcp-spec-revisions-research-2026-09-27]].
