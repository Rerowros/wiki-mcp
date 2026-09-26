# Wiki Schema

Contract for every page under `wiki/`. `tools/lint.py` enforces it; CI runs the
linter on every push. The *reasoning* loop that decides what to write lives in
[[research-cycle-idea-to-thesis]] — this file is the shape, not the method.

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (people, tools, organizations, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena, frameworks |
| source | wiki/sources/ | **External** artifacts: papers, articles, talks, books, docs pages |
| research | wiki/research/ | **Internal** working trail for one cycle — what was fetched and read |
| query | wiki/queries/ | Open questions under active investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |
| overview | wiki/ | High-level project summary (one per project) |
| thesis | wiki/thesis/ | Working hypothesis and its evolution over time |
| methodology | wiki/methodology/ | Research methods, protocols, and study designs |
| finding | wiki/findings/ | Individual empirical results or observations |
| log | wiki/log/ | One cycle entry; `wiki/log.md` is generated from these |

`source` vs `research` is a hard split, because it decides what a retrieval
query means. A `source` is something **someone else published**; a `research`
page is this vault's own working notes for a cycle. Putting a working trail
under `sources/` makes "find the source for X" return our own notes as evidence
for themselves.

The split is about authorship, not about links. A source is normally cited by
`url`, but a book, a screenshot of a leaderboard, or a note from a private
channel is still someone else's artifact — those carry `venue` instead. A source
with neither is not citable, and the linter rejects it.

## Naming Conventions

- Files: `kebab-case.md`
- Entities: match official name where possible (e.g., `openai.md`, `gpt-4.md`)
- Concepts: descriptive noun phrases (e.g., `chain-of-thought.md`)
- Sources: `author-year-slug.md` (e.g., `wei-2022-cot.md`)
- Research trails: `<topic>-research-YYYY-MM-DD.md`
- Queries: question as slug (e.g., `does-scale-improve-reasoning.md`)
- Theses: hypothesis as slug (e.g., `scaling-improves-reasoning.md`)
- Methodologies: method name (e.g., `systematic-review.md`)
- Findings: descriptive slug, dated when the fact is time-bound
- Log entries: `YYYY-MM-DD-<short-slug>.md`

**Slugs are globally unique.** `[[wikilinks]]` resolve by filename, not path, so
two files sharing a stem in different directories make every link to them
ambiguous. The linter treats a duplicate slug as an error.

## Frontmatter

Every page:

```yaml
---
type: entity | concept | source | research | query | comparison | synthesis | overview | thesis | methodology | finding | log
title: Human-readable title
summary: One line, under 200 chars — what this page establishes
tags: []
entities: []          # optional: [[links]] to the entity pages this page is about
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
author: <model name> | user
status: current | superseded | draft
supersedes: []        # optional: [[links]] to pages this one replaces
superseded_by: []     # generated from `supersedes` — never write it by hand
---
```

`summary` is not decoration. It is the snippet a retrieval layer shows and the
line `wiki/index.md` is built from, so a page whose summary says nothing is a
page that will not be found. Write the claim, not the topic: "MCP 2025-11-25
recommends Client ID Metadata Documents" beats "notes on MCP auth".

`author` records which model or person wrote the page, as a field rather than a
tag, so a bad run can be filtered and reverted.

`status` is lifecycle, and it is what keeps a time-series corpus honest. A page
whose numbers have been replaced by a later snapshot is `superseded`, and the
page that replaced it lists it under `supersedes`. Retrieval defaults to
`current`; asking what was true on a past date is a separate, deliberate query.

Type-specific fields:

```yaml
# source — external artifacts
authors: []
year: YYYY
url: ""              # url or venue is required — see below
venue: ""

# research — internal cycle trail
question: ""         # the Phase 0 question, one sentence
cycle_class: A | B | C | D | E

# thesis
confidence: low | medium | high
stance: speculative | supported | refuted | settled

# finding
source: "[[source-or-research-slug]]"
confidence: low | medium | high
replicated: true | false | null
claims: []           # see below

# log
date: YYYY-MM-DD
cycle_class: A | B | C | D | E
question: ""
search: []           # [[links]] to research/source pages consulted
output: []           # [[links]] to pages this cycle produced
next: ""             # the next question, or CLOSED
```

`thesis.stance` was called `status` in v1. It was renamed because `status` now
means lifecycle on every page, and a thesis needs both: a `settled` thesis can
still be `superseded` by a better formulation.

## Claims

A `finding` may carry machine-readable assertions alongside its prose. This is
what lets a reader answer "what is the current number" without reading twelve
dated pages and guessing which one still holds.

```yaml
claims:
  - subject: example-model          # SKU, entity slug, or any stable id
    metric: bench-index-v2.1          # what is being measured
    qualifier: effort=high            # optional; distinguishes cells
    value: 59
    as_of: 2026-09-02                 # when the fact was true, not when written
```

The key is `(subject, metric, qualifier)`. `tools/build.py` resolves each key to
its newest `as_of` and writes `wiki/state.md` — a generated current-state table
with a citation per row. Two claims sharing a key **and** an `as_of` but
disagreeing on `value` are a contradiction, and the linter fails on them.

**The qualifier names the measurement cell, never the date or the provenance.**
`effort=high`, `US`, `per user` are qualifiers. `launch price`, `old`, `2026 Q1`
are not: they belong in `as_of` and in the prose. This matters more than it
looks. Two figures for the same thing are only recognised as a price change if
they share a key, so writing `qualifier: US` on one and `qualifier: US, launch
price` on the other splits them into two unrelated facts, and the change
silently disappears from `wiki/state.md` instead of appearing under
**Earlier values**. That failure is invisible: both rows look correct.

Where two instruments genuinely disagree, they are different metrics, not one
metric with two qualifiers — `list-price-usd-per-1m-input` measured against a
vendor's own book and against an aggregator's costing book are two measurements
of two things, and `AGENTS.md` forbids mixing them in one row either way.

Claims never replace the prose. The table, the caveats and the `Falsifier:` line
are the page; claims are an index over it.

### Changing measurements and benchmark versions

A metric such as `bench-index-v2.1` retains its exact identifier and scale.
A new benchmark methodology means a new metric (`bench-index-v2.2`,
`bench-index-v3`, etc.), not replacement of the old numbers. This also applies
to cost/token counts per benchmark task because the task mix changes. An
unversioned legacy metric has no machine-verifiable version and is historical
once a version policy exists.

The active version is an observed fact, stored on a dated finding:

```yaml
claims:
  - subject: bench-index
    metric: active-version
    value: '2.2'
    as_of: 2026-09-07
    observed_at: 2026-09-08
    source_url: https://example.org/bench-index/methodology-v2-2
    measurement_status: reported
```

`get_state` and `query.py state` use that observation when no version is
specified. Older versions remain available via `include_history` or an exact
version. If a configuration has no observation in the active version, it is
listed in `missing_current`; an older score is never substituted. This is a
conservative wiki comparison policy: compare the same version, model config,
effort and fallback policy. A score drop across methodology revisions is not
evidence that a model got worse.

Optional claim provenance fields:

- `observed_at`: date or UTC ISO timestamp ending `Z`; when the source was checked.
- `source_url`: HTTP(S) URL of the external evidence, without credentials.
- `snapshot_sha256`: SHA-256 of a preserved source response.
- `measurement_status`: `measured`, `reported`, `estimated` or `unspecified`.
  An API that does not distinguish estimates must be marked `reported`.

Optional page field `review_after: YYYY-MM-DD` requests a recheck. It does not
assert that a fact is valid until that date. The runtime refresh queue calculates
due dates; a stale deadline never becomes a new observation automatically.

Same key/date but different values still conflict. Two explicit, distinct
`observed_at` values allow successive source snapshots within one day; the latest
observation wins and the earlier one remains visible. Without those timestamps,
do not guess the sequence. A version selector requires `source_url` and
`observed_at`; versions must be quoted strings so `4.10` is not rounded to `4.1`.

Do not mark an entire mixed-topic finding superseded merely because one benchmark
version changed: its prices or other findings may still matter. Version selection
handles historical scores independently of page-level supersession.

## Index Format

`wiki/index.md` is **generated** by `tools/build.py` from page frontmatter
— never edited by hand. Entries come from `title` and `summary`. To change how a
page appears in the index, change its `summary`.

## Log Format

`wiki/log.md` is **generated** by `tools/build.py` from `wiki/log/*.md`, in
reverse chronological order, in the bullet shape
[[research-cycle-idea-to-thesis]] specifies:

```text
- Cycle: <question>. Class A/B/C/D/E. Search: [[slug]].
  Analysis: <one sentence>. Output: [[slug]]. Next: <new question or CLOSED>.
```

One file per cycle entry means two agents logging at the same time produce two
files instead of one lost entry. If you cannot fill `next`, the cycle is not
finished — you stopped at search.

## Cross-referencing Rules

- Use `[[page-slug]]` syntax to link between wiki pages
- Every entity and concept is reachable from `wiki/index.md` (generated)
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via `related:`
- Findings link back to their evidence via the `source:` frontmatter field
- Thesis pages reference supporting and refuting findings via `related:`
- Methodology pages are cited by the findings that used them

## Contradiction Handling

When sources contradict each other:

1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists

Never average two disagreeing numbers. A mean of a vendor score and an
independent score is a number no one measured.

When *this vault* contradicts itself across time, that is not a contradiction —
it is supersession. Mark it with `supersedes` / `status: superseded` so the
older page stops being served as current.

## Research-Specific Conventions

- Keep the thesis pages updated as evidence accumulates — they are living documents
- Every finding should assess replication status when known
- Methodology pages explain the *why* (rationale) not just the *how*
- Distinguish between direct evidence and inference in finding pages
- Numbers carry a date and a source, or they read `unknown / not public`
- Keep vendor, independent, and this-vault measurements in separate rows
