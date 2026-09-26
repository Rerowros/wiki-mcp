---
type: methodology
title: Retrieval evaluation with a golden set — recall@k, MRR, staleness
summary: How search quality is measured here — a hand-verified golden set of questions scored by recall@5, MRR and staleness rate, run in CI so ranking changes move numbers, not opinions.
tags:
- method
- retrieval
- evaluation
- rag
related:
- '[[sqlite-fts5-bm25-search]]'
- '[[research-cycle-idea-to-thesis]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
---

# Retrieval evaluation with a golden set

A retrieval-augmented agent is only as good as the page it is handed. Without a
fixed test set every ranking change is a matter of taste, and two changes that
"obviously help" can each make things measurably worse. So this wiki keeps a
**golden set** — `tests/golden.yaml` — and `tools/eval.py` scores the ranker
against it on every CI run.

## The golden set

Each entry is a natural-language question, the page slugs that answer it, and
optionally the slugs that *used to* answer it but are now out of date:

```yaml
- q: what is the latest MCP specification revision
  expect: [mcp-latest-revision-2026-07-28]
  stale: [mcp-latest-revision-2025-11-25, mcp-latest-revision-2025-06-18]
  time_sensitive: true
```

Rules that keep the set honest:

- Verify every expected page by reading it. A question whose answer you have not
  checked is worse than no question.
- The harness fails if a slug in the set does not exist, so a renamed page
  cannot masquerade as a ranking regression.
- Mark `time_sensitive` questions — the class where naive retrieval fails,
  because several near-identical dated pages compete.

## The metrics

**recall@k** — the share of questions where an expected page appears in the
top *k* results (k = 5 here). The headline number; CI fails below a threshold.

**MRR** (mean reciprocal rank) — the average of 1/rank of the first expected
hit, 0 for a miss. Moves when the right page climbs from #4 to #1, which
recall@5 cannot see.

**staleness rate** — among questions that name `stale` pages, how often the
*top* result is one of them. A miss is a dead end the reader notices; a stale
top hit is a confident wrong answer the reader does not. That is why it is
reported separately and gated in CI.

## Local and served must agree

`tools/eval.py` runs against the local SQLite index built by
`tools/export_index.py`, which is the same schema and SQL the Worker serves from
D1. `python tools/eval.py --endpoint https://<worker>/cli/mcp` runs the same set
against the deployed server. The two numbers should match; when they do not,
the Python and TypeScript rankers have drifted, and that drift is the bug.

## Tuning

`tools/tune.py` sweeps field weights, per-type priors and the link-graph boost
against the golden set. Adopt a change only when it moves a number.

## Failure modes

- A golden set that only contains easy questions reports a reassuring recall
  that predicts nothing. Add the questions that failed in real use.
- Tuning on the same set you report on overfits it. Keep a few questions out of
  tuning, or refresh the set as the corpus grows.
- Recall on a ten-page corpus is close to meaningless; the harness matters once
  the corpus has many pages about the same subject.
