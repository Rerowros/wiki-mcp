#!/usr/bin/env python3
"""Render the vault into SQL for D1.

The index is derived, exactly like `wiki/index.md`: markdown stays the source of
truth and this file is regenerated, never edited. If D1 disagrees with the
pages, D1 is wrong.

    python tools/export_index.py --full --out dist/d1
    python tools/export_index.py --since <sha> --out dist/d1
    python tools/export_index.py --full --sqlite dist/wiki.db   # local check

Output is several files rather than one because D1 can exceed its isolate CPU
limit on a large single import, and one statement per row rather than a
multi-row VALUES because D1 caps a single SQL statement at 100_000 bytes. The
largest page here is 88_990 bytes, so a whole-body column would sit 9% from
that cap and fail as an opaque import error the first time it grew. Bodies are
therefore stored per heading chunk, which is what docs/retrieval.md wants
anyway.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

import chunk as C
import wikilib as W

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS pages (
  slug        TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  title       TEXT NOT NULL,
  summary     TEXT NOT NULL,
  status      TEXT NOT NULL,
  author      TEXT,
  created     TEXT,
  updated     TEXT,
  page_date   TEXT,
  rel_path    TEXT NOT NULL,
  frontmatter TEXT NOT NULL,
  content_sha TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_type_status ON pages(type, status);
CREATE INDEX IF NOT EXISTS idx_pages_date ON pages(page_date DESC);

CREATE TABLE IF NOT EXISTS chunks (
  id      INTEGER PRIMARY KEY,
  slug    TEXT NOT NULL,
  ord     INTEGER NOT NULL,
  heading TEXT,
  text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_slug ON chunks(slug, ord);

-- Standalone rather than external-content: it duplicates ~4 MB of text, which
-- is nothing against D1's 5 GB, and it makes delete-then-reinsert trivial.
-- External content would need the original values on every delete.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  title, summary, heading, symbols, text,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS claims (
  page      TEXT NOT NULL,
  subject   TEXT NOT NULL,
  metric    TEXT NOT NULL,
  qualifier TEXT NOT NULL DEFAULT '',
  value     TEXT NOT NULL,
  as_of     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_key ON claims(subject, metric, qualifier, as_of DESC);

CREATE TABLE IF NOT EXISTS links (
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst, kind);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(src, kind);

CREATE TABLE IF NOT EXISTS threads (
  page     TEXT PRIMARY KEY,
  date     TEXT NOT NULL,
  question TEXT,
  next     TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_date ON threads(date DESC);
"""


def q(value) -> str:
    """A SQL literal. Everything reaching D1 goes through here."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def chunk_symbols(piece: dict, title: str) -> str:
    """Identifiers from this chunk, plus the page title.

    Page-wide symbols were tried first and are worse. A SKU named once in the
    lede does govern the sections below it, so scoping per chunk loses signal —
    but page-wide scoping loses more: the 89 KB benchmark catalogue names
    hundreds of SKUs, so its every chunk matched nearly every model query, and
    with the symbols column weighted highest it won queries it had no business
    winning. Measured over tests/golden.yaml, per-chunk moves recall@5 from
    0.67 to 0.80 and MRR from 0.543 to 0.627.
    """
    return " ".join(C.symbols(piece["text"] + " " + piece["heading"] + " " + title))


def content_sha(page: W.Page) -> str:
    return hashlib.sha256(page.raw.encode("utf-8")).hexdigest()[:16]


def chunk_id(slug: str, order: int) -> int:
    """A stable SQLite rowid for one heading chunk.

    Incremental exports cannot allocate from a locally calculated ``next_id``:
    untouched rows already live in D1, and two independent exports otherwise
    choose the same integer.  Use the first 63 bits of a domain-separated hash
    instead.  New ids are negative so they cannot collide with the positive,
    sequential ids written by exporter versions before this one.  A hash
    collision fails the primary-key insert rather than attaching FTS text to
    the wrong page.
    """
    key = f"wiki-chunk-v1\0{slug}\0{order}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") & ((1 << 63) - 1)
    return -(value or 1)


def delete_rows(slug: str) -> list[str]:
    """Delete every derived row owned by one markdown page."""
    return [
        f"DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE slug = {q(slug)});",
        f"DELETE FROM chunks WHERE slug = {q(slug)};",
        f"DELETE FROM claims WHERE page = {q(slug)};",
        f"DELETE FROM links WHERE src = {q(slug)};",
        f"DELETE FROM threads WHERE page = {q(slug)};",
        f"DELETE FROM pages WHERE slug = {q(slug)};",
    ]


def page_rows(page: W.Page) -> list[str]:
    """Every statement describing one page. Deletes first, so re-running is safe."""
    slug = page.slug
    out = delete_rows(slug)

    fm = page.fm
    title = str(fm.get("title", "") or "")
    summary = str(fm.get("summary", "") or "")
    tags = " ".join(str(t) for t in (fm.get("tags") or []))
    out.append(
        "INSERT INTO pages (slug, type, title, summary, status, author, created, "
        "updated, page_date, rel_path, frontmatter, content_sha) VALUES ("
        f"{q(slug)}, {q(page.type)}, {q(title)}, {q(summary)}, "
        f"{q(str(fm.get('status', '') or ''))}, {q(str(fm.get('author', '') or ''))}, "
        f"{q(str(fm.get('created', '') or ''))}, {q(str(fm.get('updated', '') or ''))}, "
        f"{q(C.page_date(page))}, {q(page.rel)}, "
        f"{q(json.dumps(fm, ensure_ascii=False, default=str))}, {q(content_sha(page))});"
    )

    for piece in C.chunks(page):
        row_id = chunk_id(slug, piece["ord"])
        out.append(
            f"INSERT INTO chunks (id, slug, ord, heading, text) VALUES ("
            f"{row_id}, {q(slug)}, {piece['ord']}, {q(piece['heading'])}, {q(piece['text'])});"
        )
        out.append(
            "INSERT INTO chunks_fts (rowid, title, summary, heading, symbols, text) VALUES ("
            f"{row_id}, {q(title)}, {q(summary + ' ' + tags)}, "
            f"{q(piece['heading'])}, {q(chunk_symbols(piece, title))}, "
            f"{q(piece['text'])});"
        )

    for claim in fm.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        subject, metric, qualifier = W.claim_key(claim)
        out.append(
            "INSERT INTO claims (page, subject, metric, qualifier, value, as_of) VALUES ("
            f"{q(slug)}, {q(subject)}, {q(metric)}, {q(qualifier)}, "
            f"{q(str(claim.get('value', '')))}, {q(str(claim.get('as_of', '')))});"
        )

    # kind matters: docs/retrieval.md reranks on it, and "prefer pages whose
    # entities match the query" is not expressible over an undifferentiated edge.
    seen: set[tuple[str, str]] = set()
    for key in W.LINK_FM_KEYS:
        for dst in sorted(page.fm_links(key)):
            if (dst, key) not in seen:
                seen.add((dst, key))
                out.append(f"INSERT INTO links (src, dst, kind) VALUES ({q(slug)}, {q(dst)}, {q(key)});")
    for dst in sorted(page.body_links()):
        if (dst, "body") not in seen:
            seen.add((dst, "body"))
            out.append(f"INSERT INTO links (src, dst, kind) VALUES ({q(slug)}, {q(dst)}, 'body');")

    if page.type == "log":
        nxt = str(fm.get("next", "") or "").strip()
        if nxt and not nxt.upper().startswith("CLOSED"):
            out.append(
                "INSERT INTO threads (page, date, question, next) VALUES ("
                f"{q(slug)}, {q(str(fm.get('date', '') or ''))}, "
                f"{q(str(fm.get('question', '') or ''))}, {q(nxt)});"
            )
    return out


def head_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=W.ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def changed_since(sha: str) -> tuple[set[str], set[str]]:
    """(slugs touched, slugs deleted) between `sha` and HEAD."""
    res = subprocess.run(["git", "diff", "--name-status", sha, "HEAD", "--", "wiki/"],
                         cwd=W.ROOT, capture_output=True, text=True, check=True)
    touched, removed = set(), set()
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not path.endswith(".md"):
            continue
        slug = os.path.basename(path)[:-3]
        (removed if status.startswith("D") else touched).add(slug)
    return touched, removed


def write_files(out_dir: str, groups: dict[str, list[str]]) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, statements in groups.items():
        if not statements:
            continue
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(statements) + "\n")
        written.append(path)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the vault into SQL for D1.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--since", metavar="SHA")
    ap.add_argument("--out", default=os.path.join(W.ROOT, "dist", "d1"))
    ap.add_argument("--sqlite", metavar="PATH", help="also build a local .db and self-check")
    args = ap.parse_args()

    pages = W.load_pages()
    sha = head_sha()

    if args.since:
        touched, removed = changed_since(args.since)
        selected = {s: p for s, p in pages.items() if s in touched}
        print(f"incremental from {args.since[:12]}: {len(selected)} changed, {len(removed)} deleted")
    else:
        selected, removed = pages, set()
        print(f"full rebuild: {len(selected)} pages")

    body: list[str] = []
    for slug in sorted(removed):
        body += delete_rows(slug)
    for slug in sorted(selected):
        body += page_rows(selected[slug])

    # Statements are kept as a list all the way through. Splitting SQL text on
    # semicolons later would be wrong: page bodies contain them, inside string
    # literals, constantly.
    schema_statements = [st.strip() + ";" for st in SCHEMA.split(";") if st.strip()]
    groups = {
        "01_schema.sql": schema_statements,
        "02_reset.sql": ([] if args.since else [
            "DELETE FROM chunks_fts;", "DELETE FROM chunks;", "DELETE FROM claims;",
            "DELETE FROM links;", "DELETE FROM threads;", "DELETE FROM pages;",
        ]),
    }
    # Split the body so no single import file is large enough to trip D1's
    # per-request CPU limit.
    per_file = 4000
    parts = [body[i:i + per_file] for i in range(0, len(body), per_file)] or [[]]
    for i, part in enumerate(parts, start=3):
        groups[f"{i:02d}_data.sql"] = part
    written = write_files(args.out, groups)

    # The exact statement list, for tools/push_index.py. The .sql files stay
    # for `wrangler d1 execute --file` and for reading by eye.
    ordered: list[str] = []
    for name in sorted(groups):
        ordered.extend(groups[name])
    with open(os.path.join(args.out, "statements.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ordered, fh, ensure_ascii=False)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({
            "format": 1,
            "target_sha": sha,
            "mode": "incremental" if args.since else "full",
            "base_sha": args.since or "",
            "statement_count": len(ordered),
        }, fh, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in groups.values())
    biggest = max((len(s.encode("utf-8")) for s in body), default=0)
    print(f"{total} statements in {len(written)} files -> {args.out}")
    print(f"largest statement: {biggest} bytes (D1 caps one statement at 100000)")
    if biggest >= 100000:
        print("FAIL: a statement exceeds D1's limit")
        return 1

    if args.sqlite:
        return selfcheck(args.sqlite, written, pages)
    return 0


def selfcheck(db_path: str, files: list[str], pages: dict[str, W.Page]) -> int:
    """Load the generated SQL locally and assert the index matches the vault."""
    import sqlite3

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    db = sqlite3.connect(db_path)
    # executescript() commits and then runs in autocommit, so without an
    # explicit transaction these 18k statements cost 18k fsyncs — 1m41s against
    # about a second wrapped.
    for path in files:
        with open(path, encoding="utf-8") as fh:
            db.executescript("BEGIN;\n" + fh.read() + "\nCOMMIT;")
    db.commit()

    n_pages = db.execute("SELECT count(*) FROM pages").fetchone()[0]
    n_chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    n_fts = db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    n_claims = db.execute("SELECT count(*) FROM claims").fetchone()[0]
    n_links = db.execute("SELECT count(*) FROM links").fetchone()[0]

    want_claims = sum(len(p.fm.get("claims") or []) for p in pages.values())
    print(f"\n  pages   {n_pages:>6}  (vault {len(pages)})")
    print(f"  chunks  {n_chunks:>6}   fts {n_fts}")
    print(f"  claims  {n_claims:>6}  (vault {want_claims})")
    print(f"  links   {n_links:>6}")

    ok = True
    if n_pages != len(pages):
        print("  FAIL page count"); ok = False
    if n_fts != n_chunks:
        print("  FAIL fts rows do not match chunk rows"); ok = False
    if n_claims != want_claims:
        print("  FAIL claim count"); ok = False

    # The rowid join is what `search` depends on; a silent mismatch here would
    # return the wrong page's text under the right page's title.
    bad = db.execute(
        "SELECT count(*) FROM chunks c JOIN chunks_fts f ON f.rowid = c.id "
        "WHERE f.text <> c.text").fetchone()[0]
    if bad:
        print(f"  FAIL {bad} chunks whose fts row does not match"); ok = False

    # The symbols column is what makes versioned identifiers rank. Take any
    # collapsed symbol the corpus actually carries and prove it round-trips:
    # the chunk that emitted it must be findable through it.
    sample = db.execute(
        "SELECT c.slug, f.symbols FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
        "WHERE f.symbols <> '' ORDER BY c.slug, c.ord LIMIT 1").fetchone()
    if sample:
        symbol = sample[1].split()[-1]
        row = db.execute(
            "SELECT c.slug FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            "WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts, 8.0, 6.0, 3.0, 10.0, 1.0) ASC LIMIT 1",
            [f'symbols:"{symbol}"']).fetchone()
        print(f"  top hit for symbols:{symbol} -> {row[0] if row else 'NONE'}")
        if not row:
            print("  FAIL symbol lookup returned nothing"); ok = False
    else:
        print("  no versioned identifiers in the corpus; symbol check skipped")

    print("\n  self-check passed" if ok else "\n  self-check FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
