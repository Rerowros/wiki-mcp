#!/usr/bin/env python3
"""Turning pages into indexable units: chunks, tokens, symbols.

Shared by the local search CLI and the D1 exporter so that both index the same
text the same way. If they drift, the local numbers stop predicting the served
ones and every tuning decision made against the local index is worthless.

Three jobs:

- `chunks()` splits a page on headings, because a table row like
  `| 3.8 Flash high | 59 | $0.58 |` means nothing on its own and is precise
  once its chunk carries "Model 3.8 Flash GA, finding, 2026-09-02".
- `tokens()` is the tokenizer. It must match SQLite's `unicode61` closely
  enough that local BM25 predicts served BM25.
- `symbols()` is the reason exact SKU lookup works at all. See below.
"""
from __future__ import annotations

import re

import wikilib as W

# D1's statement limit is 100_000 bytes and the largest page here is ~89 KB, so
# a whole-body column would sit 9% from a hard cap that fails as an opaque
# import error. Chunks keep every row far away from it.
MAX_CHUNK_BYTES = 6144

HEADING_RE = re.compile(r"^(#{2,4})\s+(.*)$", re.M)

# unicode61 splits on everything that is not a letter or digit, so Cyrillic
# survives and `model-3.8-flash` does not.
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# A dotted or hyphenated identifier: SKUs, versions, package names.
SYMBOL_RE = re.compile(r"[a-zà-ÿа-я][a-zà-ÿа-я0-9]*(?:[-.][a-zà-ÿа-я0-9]+)+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def symbols(text: str) -> list[str]:
    """Identifiers, in both the written and the collapsed form.

    `unicode61` shreds `model-3.8-flash` into model/3/8/flash. Measured, that
    does not make the SKU unfindable — an FTS5 phrase query still matches the
    token run, 60 chunks against 55 for the collapsed form. What it destroys is
    the *ranking*: the parts that distinguish 3.8 from 3.7 are `3` and `8`,
    among the commonest tokens in a corpus full of version numbers, so they
    carry almost no IDF and the SKU scores like the word "model" alone.

    The obvious fix — `tokenize='unicode61 tokenchars ''-.'''` — is per-table,
    not per-column, so it would also weld `state-of-the-art` into one token and
    make every hyphenated Russian phrase unsearchable by its parts: a global
    cost across 461k words to rescue a few hundred identifiers.

    Emitting `model38flash` alongside the written form costs one column. The
    collapsed form is a single rare token, so it carries real IDF and ranks the
    way the identifier deserves, while prose tokenization is left alone.
    """
    out = []
    for match in SYMBOL_RE.finditer(text.lower()):
        raw = match.group(0)
        # Only identifiers a tokenizer actually destroys. `state-of-the-art` and
        # `first-party` survive as their own words and are found by ordinary
        # search; `model-3.8-flash` becomes model/3/8/flash, where the two
        # digit tokens carry no information and the identity is gone. The digit
        # is what separates the two cases.
        if not any(ch.isdigit() for ch in raw):
            continue
        out.append(raw)
        out.append(re.sub(r"[-.]", "", raw))
    return out


def split_oversized(text: str, limit: int = MAX_CHUNK_BYTES) -> list[str]:
    """Hard-split a chunk that no heading broke up, on paragraph boundaries."""
    if len(text.encode("utf-8")) <= limit:
        return [text]
    parts, current = [], []
    size = 0
    for para in text.split("\n\n"):
        chunk_size = len(para.encode("utf-8")) + 2
        if current and size + chunk_size > limit:
            parts.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += chunk_size
    if current:
        parts.append("\n\n".join(current))
    return parts


def chunks(page: W.Page) -> list[dict]:
    """Heading-delimited chunks, in document order.

    The text before the first heading is the lede and becomes chunk 0 with an
    empty heading — on a short page that is the whole page, and on a finding it
    usually holds the `**Finding.**` sentence.
    """
    body = page.body
    marks = list(HEADING_RE.finditer(body))
    raw: list[tuple[str, str]] = []

    lede = body[: marks[0].start()] if marks else body
    if lede.strip():
        raw.append(("", lede.strip()))
    for i, match in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        section = body[match.end() : end].strip()
        raw.append((match.group(2).strip(), section))

    out: list[dict] = []
    for heading, text in raw:
        if not text and not heading:
            continue
        for piece in split_oversized(text):
            out.append({
                "slug": page.slug,
                "ord": len(out),
                "heading": heading,
                "text": piece,
            })
    return out


def page_date(page: W.Page) -> str:
    """The date a reader should judge this page's currency by.

    A dated slug is the strongest signal in this vault — 45% of pages carry one
    — and it is the date the author chose, so it wins over `created`. `updated`
    is deliberately not consulted: propagate_supersession rewrites frontmatter
    without touching it, so it is not a reliable freshness key.
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", page.slug)
    if match:
        return match.group(1)
    for key in ("date", "created"):
        value = str(page.fm.get(key, "") or "")
        if W.ISO_DATE.match(value):
            return value
    return ""
