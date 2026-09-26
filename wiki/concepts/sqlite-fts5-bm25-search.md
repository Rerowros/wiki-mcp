---
type: concept
title: Full-text search with SQLite FTS5 and BM25
summary: Search here is SQLite FTS5 over heading-sized chunks, ranked by field-weighted bm25(), with a symbols column so versioned identifiers like oauth-2.1 rank as one rare token.
tags:
- search
- sqlite
- fts5
- retrieval
related:
- '[[retrieval-evaluation-golden-set]]'
- '[[model-context-protocol]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
---

# Full-text search with SQLite FTS5 and BM25

## What it is

**FTS5** is SQLite's full-text search extension: a virtual table with an
inverted index over one or more text columns, queried with `MATCH`. Its
built-in `bm25()` function scores rows with Okapi BM25 — term frequency
saturated by document length, weighted by inverse document frequency — and
accepts one weight per column. `bm25()` returns *lower is better*, so the
code negates it before comparing.

Cloudflare D1 is SQLite, so the same schema and SQL run locally
(`tools/query.py`) and in the Worker (`worker/src/search.ts`).

## How this wiki uses it

- **Chunks, not pages.** `tools/chunk.py` splits each page on `##`–`####`
  headings. A table row means nothing alone; inside a chunk that carries the
  page title and summary it is precise. Chunks also keep every SQL statement far
  below D1's statement-size limit.
- **Five weighted columns:** `title, summary, heading, symbols, text`.
- **Passage scoring.** A page scores as its single best chunk. Summing chunk
  scores rewards long pages for being long.
- **Type prior.** Answer-shaped pages (finding, comparison, thesis, synthesis)
  get a small boost over working notes (research, source, log).
- **One hop over curated links.** A page linked from a top hit through
  `related`, `source`, `supersedes` or `entities` inherits a tenth of its score.
- **Status filter.** Only `status: current` pages are searched unless the caller
  asks for superseded ones.

## The symbols column

The `unicode61` tokenizer splits on every non-alphanumeric character, so
`oauth-2.1` becomes `oauth`, `2`, `1`. The page is still findable, but its
ranking is destroyed: `2` and `1` are among the commonest tokens in any corpus
of versions, so they carry almost no IDF. The exporter therefore also writes each
dotted or hyphenated identifier that contains a digit into a `symbols` column,
in both forms — `oauth-2.1` and `oauth21` — and the query builder matches the
collapsed form. It costs one column and leaves prose tokenization alone.

## Query safety

FTS5 `MATCH` is a query language with operators. Every user term is emitted as a
quoted phrase and terms are joined with `OR`, so user input is data and cannot
change what the query means or fail to parse.

## What it is not

Not semantic search. BM25 cannot tell two dated pages on the same subject apart
by which one is still true — that is what `status`, `supersedes` and the claims
table are for.
