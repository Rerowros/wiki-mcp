---
type: concept
title: Agent writes via proposal branches and a CI gate
summary: Agents never push to main — propose_cycle validates a whole cycle, commits it to an agent/** branch, and the server fast-forwards main only after validate.yml passes on that exact commit.
tags:
- mcp
- ci
- write-path
- github-actions
related:
- '[[model-context-protocol]]'
- '[[research-cycle-idea-to-thesis]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
---

# Agent writes via proposal branches and a CI gate

## What it is

The write path of the MCP server. An agent sends one **cycle** — a research
trail, zero or more outputs, exactly one log entry — to `propose_cycle`. The
server:

1. **Pre-validates** the cycle against the schema constants generated from
   `tools/wikilib.py` and against the live index: unique slugs, `supersedes`
   targets that exist, claims that do not contradict a recorded value, no
   generated-file marker, a non-empty `next`.
2. Takes a **merge lock** (a row in D1) and checks the index is an exact
   snapshot of `main`.
3. Writes every file as **one commit** on a new `agent/<date>-<slug>` branch via
   the Git Data API (blobs → tree → commit → ref), so a cycle cannot half-land.
4. Returns a `proposal_id` immediately.

`check_proposal` then reads GitHub Actions. Only a successful `validate.yml` run
for that exact SHA and branch counts. On green, the server fast-forwards `main`
with a non-forced ref update — if `main` moved in the meantime, GitHub refuses
it and the proposal must be resubmitted. On red, the linter's annotations come
back as errors.

## Why the gate is shaped this way

- **validate.yml holds no secrets and only `contents: read`.** It runs against
  branch content an agent wrote, and a workflow triggered from a branch runs
  that branch's definition. An agent branch that touches anything outside
  `wiki/` fails, as does one that carries generated files.
- **publish.yml runs only on `main`**, from `main`'s own definition. It is the
  single writer of derived files and the only job with a secret (the reindex
  token).
- **The Worker, not CI, merges.** A push made with Actions' own `GITHUB_TOKEN`
  does not trigger further workflows, so a CI-side merge would never run
  `publish.yml` and the index would silently drift from `main`. The Worker's
  token is a different identity; its push does trigger publishing, and
  publishing's own commit does not re-trigger it.
- **A cron sweep** deletes abandoned `agent/**` branches after seven days.

## What it is not

Not code review by a human. The gate checks the contract, not whether the
research is right; the human steers by reading `main` and reverting. It also
does not let an agent edit existing pages in place — supersession is a new page.
