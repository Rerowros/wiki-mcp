#!/usr/bin/env python3
"""Search the vault from the command line, the way the server does.

This used to carry its own BM25 in Python while the Worker used SQLite's
`bm25()`. They were never going to agree, and they didn't: the local index
scored recall@5 0.87 on tests/golden.yaml while the deployed one scored 0.67 on
the same questions, so every ranking decision measured locally was measuring a
ranker nobody queried.

So there is one ranker now. `tools/export_index.py --sqlite` builds the same
index D1 serves, and this queries it with the same SQL and the same aggregation
as `worker/src/search.ts`. When those two files disagree, that is the bug.

    python tools/query.py search "latest mcp revision" --type finding
    python tools/query.py state --subject mcp-spec
    python tools/query.py backlinks mcp-latest-revision-2025-11-25
    python tools/query.py threads
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict

import chunk as C
import wikilib as W
import measurements as M

DB_PATH = os.path.join(W.ROOT, "dist", "wiki.db")

# title, summary, heading, symbols, text — the column order of chunks_fts.
# Swept against tests/golden.yaml rather than chosen by taste: against the
# obvious-looking (8, 6, 3, 10, 1) this moves recall@5 from 0.67 to 0.80 and MRR
# from 0.543 to 0.627. The heavy title and summary weights turned out to be
# actively harmful — on a corpus where dozens of pages are about the same
# subject, they reward a page for being *about* the topic rather than for
# answering the question.
WEIGHTS = (2.0, 2.0, 1.0, 4.0, 1.0)

# Chunks scanned before aggregating to pages. A broad OR query matches ~2500 of
# 3921 chunks and the best chunk of a correct answer has been seen at rank 2, so
# this is slack rather than a cutoff.
CHUNK_SCAN = 400

# docs/retrieval.md: "an answer-shaped question prefers finding, comparison,
# thesis; an evidence-shaped question prefers research and source". Nothing
# implemented it, and trails kept outranking the findings they produced — asked
# what a subscription plan includes, the vault returned the working notes rather
# than the conclusion. Swept with tools/tune.py: MRR 0.627 -> 0.737.
TYPE_PRIOR = {
    "finding": 1.2, "comparison": 1.2, "thesis": 1.2, "synthesis": 1.2,
    "research": 0.7, "source": 0.7, "log": 0.7,
}

# One hop over hand-placed edges: a page linked by a strong hit inherits a
# tenth of its score. Those 6544 edges were placed deliberately by whoever
# wrote the page, which is a stronger signal than co-occurrence — but only
# just: 0.2 is measurably worse than 0.1 and 0.3 is worse than nothing, so this
# is a nudge and not a mechanism. Body prose links are excluded; only curated
# frontmatter edges count.
GRAPH_BOOST = 0.1
GRAPH_SEEDS = 5
CURATED_KINDS = ("related", "source", "supersedes", "superseded_by", "entities")


def ensure_db(rebuild: bool = False) -> str:
    """Build the index if it is missing or older than the newest page."""
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(W.WIKI):
        dirnames[:] = [d for d in dirnames if d != "assets"]
        for fn in filenames:
            if fn.endswith(".md"):
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, fn)))

    if rebuild or not os.path.exists(DB_PATH) or os.path.getmtime(DB_PATH) < newest:
        W.eprint("building the local index ...")
        subprocess.run(
            [sys.executable, os.path.join(W.ROOT, "tools", "export_index.py"),
             "--full", "--out", os.path.join(W.ROOT, "dist", "d1"), "--sqlite", DB_PATH],
            check=True, stdout=subprocess.DEVNULL,
        )
    return DB_PATH


def build_match(query: str) -> str:
    """Mirror of buildMatch in worker/src/search.ts.

    Every term is emitted as a quoted phrase. FTS5 MATCH is a query language
    with its own operators, so unquoted user input can change what a query means
    or fail to parse outright; quoting makes the input data.
    """
    terms = set(C.tokens(query))
    clauses: list[str] = []
    for m in re.finditer(C.SYMBOL_RE, query.lower()):
        raw = m.group(0)
        if not any(ch.isdigit() for ch in raw):
            continue
        clauses.append('symbols:"%s"' % re.sub(r"[-.]", "", raw))
        clauses.append('"%s"' % raw)
        for part in re.split(r"[-.]", raw):
            terms.discard(part)
    clauses.extend('"%s"' % t for t in sorted(terms) if len(t) > 1)
    return " OR ".join(clauses)


class Index:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row

    def search(self, query: str, limit: int = 10, ptype: str = "",
               status: str = "", include_superseded: bool = False) -> list[dict]:
        match = build_match(query)
        if not match:
            return []

        weights = ", ".join(str(w) for w in WEIGHTS)
        sql = f"""
            SELECT c.slug, c.heading, p.type, p.title, p.summary,
                   p.page_date, p.status,
                   bm25(chunks_fts, {weights}) AS score
              FROM chunks_fts f
              JOIN chunks c ON c.id = f.rowid
              JOIN pages  p ON p.slug = c.slug
             WHERE chunks_fts MATCH ?
               {"AND p.type = ?" if ptype else ""}
               {"AND p.status = ?" if status else ""}
               {"" if status else ("AND p.status != 'draft'" if include_superseded else "AND p.status = 'current'")}
             ORDER BY score ASC LIMIT {CHUNK_SCAN}"""
        binds = [match] + ([ptype] if ptype else []) + ([status] if status else [])
        rows = self.db.execute(sql, binds).fetchall()

        # A page scores as its single best chunk. Nothing else.
        #
        # Two corroboration bonuses were tried and both made it worse. Adding a
        # tenth of EVERY matching chunk let a 200-chunk page bank 20x its own
        # best score, so the 89 KB benchmark catalogue won queries where its own
        # best chunk ranked 1624th. Narrowing it to a tenth of the second-best
        # still reordered pages whose best chunks were 56 ranks apart, because
        # bm25 values sit in a narrow band and a tenth of one score is
        # comparable to the gap between good and mediocre. Dropping it held
        # recall and moved MRR 0.594 -> 0.641.
        #
        # This is passage retrieval: the best passage decides which page
        # answers. Kept identical to worker/src/search.ts.
        best: dict[str, dict] = {}
        for row in rows:
            score = -row["score"]  # bm25 is negative; lower is better
            score *= TYPE_PRIOR.get(row["type"], 1.0)
            if score > best.get(row["slug"], {}).get("score", float("-inf")):
                best[row["slug"]] = {
                    "slug": row["slug"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    # Currency travels with every result: 45% of slugs are dated
                    # and almost nothing is marked superseded, so `status` alone
                    # tells a reader nothing about whether this is still true.
                    "date": row["page_date"] or "",
                    "status": row["status"],
                    "heading": row["heading"] or "",
                    "score": round(score, 3),
                }
        self._expand(best)
        return sorted(best.values(), key=lambda d: (-d["score"], d["slug"]))[:limit]

    def _expand(self, best: dict[str, dict]) -> None:
        """One hop over curated links, from the strongest hits only."""
        if not best or not GRAPH_BOOST:
            return
        seeds = sorted(best.values(), key=lambda d: -d["score"])[:GRAPH_SEEDS]
        marks = ", ".join("?" for _ in CURATED_KINDS)
        for seed in seeds:
            rows = self.db.execute(
                f"SELECT dst FROM links WHERE src = ? AND kind IN ({marks})",
                [seed["slug"], *CURATED_KINDS]).fetchall()
            for (dst,) in rows:
                if dst in best:
                    best[dst]["score"] += seed["score"] * GRAPH_BOOST


def claims_coverage(pages: dict[str, W.Page]) -> dict:
    """How much of the vault the claims table actually covers.

    Shipped with every state lookup, hit or miss. An empty result reads to a
    model as "the vault does not know this", which is the exact inversion of the
    truth: the vault knows a great deal and has recorded a fraction of it in
    machine-readable form.
    """
    findings = [p for p in pages.values() if p.type == "finding"]
    with_claims = [p for p in findings if p.fm.get("claims")]
    subjects = sorted({
        W.claim_key(c)[0]
        for p in pages.values() for c in (p.fm.get("claims") or []) if isinstance(c, dict)
    })
    total = sum(len(p.fm.get("claims") or []) for p in pages.values())
    return {
        "claims_total": total,
        "subjects_total": len(subjects),
        "findings_with_claims": len(with_claims),
        "findings_total": len(findings),
        "known_subjects": subjects,
        "note": (
            "The claims table is a partial index over findings, not over the "
            f"corpus. {len(findings) - len(with_claims)} of {len(findings)} findings "
            "have no structured claims (some findings are qualitative). A miss here means NOT RECORDED "
            "AS A CLAIM, not NOT KNOWN - fall through to search."
        ),
    }


def state(pages: dict[str, W.Page], subject: str = "", metric: str = "",
          version: str | None = None, include_history: bool = False) -> dict:
    return {**M.resolve(M.records_from_pages(pages), subject, metric, version, include_history),
            "coverage": claims_coverage(pages)}


def backlinks(pages: dict[str, W.Page], slug: str) -> dict:
    inbound: dict[str, list[str]] = defaultdict(list)
    for page in pages.values():
        for key in W.LINK_FM_KEYS:
            if slug in page.fm_links(key):
                inbound[key].append(page.slug)
        if slug in page.body_links():
            inbound["body"].append(page.slug)
    return {"slug": slug, "inbound": {k: sorted(v) for k, v in sorted(inbound.items())}}


def threads(pages: dict[str, W.Page], limit: int = 20) -> list[dict]:
    """Where cycles were left off, newest first.

    This is how an agent in one client picks up what an agent in another
    started: `next` is the only place a cycle records its unfinished business.
    """
    logs = [p for p in pages.values() if p.type == "log"]
    logs.sort(key=lambda p: (str(p.fm.get("date", "")), p.slug), reverse=True)
    out = []
    for page in logs:
        nxt = str(page.fm.get("next", "") or "").strip()
        if not nxt or nxt.upper().startswith("CLOSED"):
            continue
        out.append({
            "slug": page.slug,
            "date": str(page.fm.get("date", "")),
            "question": str(page.fm.get("question", "") or ""),
            "next": nxt,
        })
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Search the wiki the way the server does.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="force an index rebuild")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", "--limit", type=int, default=10)
    s.add_argument("--type", default="")
    s.add_argument("--status", default="")
    s.add_argument("--include-superseded", action="store_true")

    st = sub.add_parser("state")
    st.add_argument("--subject", default="")
    st.add_argument("--metric", default="")
    st.add_argument("--version", help="Exact benchmark version; never mix versions")
    st.add_argument("--include-history", action="store_true")

    bl = sub.add_parser("backlinks")
    bl.add_argument("slug")

    th = sub.add_parser("threads")
    th.add_argument("-k", "--limit", type=int, default=20)

    args = ap.parse_args()

    if args.cmd == "search":
        rows = Index(ensure_db(args.rebuild)).search(
            args.query, args.limit, args.type, args.status, args.include_superseded)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
            return 0
        if not rows:
            print("no matches")
            return 0
        for r in rows:
            mark = "  <- superseded" if r["status"] == "superseded" else ""
            dated = f" - {r['date']}" if r["date"] else ""
            print(f"\n{r['score']:8.2f}  {r['slug']}  [{r['type']}{dated}]{mark}")
            print(f"          {r['summary'][:100]}")
            if r["heading"]:
                print(f"          > {r['heading'][:80]}")
        print()
        return 0

    pages = W.load_pages()

    if args.cmd == "state":
        result = state(pages, args.subject, args.metric, args.version, args.include_history)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        if not result["matched"]:
            print("\nno claim recorded for that key.")
        for m in result["missing_current"]:
            print(f"  MISSING {m['subject']} / {m['expected_metric']} [{m['qualifier']}]: collect this version")
        for m in result["matched"]:
            qual = f" [{m['qualifier']}]" if m["qualifier"] else ""
            print(f"  {m['subject']} / {m['metric']}{qual} = {m['value']}  "
                  f"({m['as_of']}, {m['source']})")
            for e in m["earlier"]:
                print(f"      was {e['value']} as of {e['as_of']} - {e['source']}")
        if args.include_history:
            for m in result["historical"]:
                print(f"  HISTORICAL {m['subject']} / {m['metric']} = {m['value']} ({m['as_of']}, {m['source']})")
        print(result["note"])
        cov = result["coverage"]
        print(f"\n  coverage: {cov['claims_total']} claims over {cov['subjects_total']} "
              f"subjects; {cov['findings_with_claims']}/{cov['findings_total']} findings "
              f"carry any.\n  {cov['note']}\n")

    elif args.cmd == "backlinks":
        result = backlinks(pages, args.slug)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not result["inbound"]:
            print("no inbound links")
        for kind, slugs in result["inbound"].items():
            print(f"\n{kind} ({len(slugs)})")
            for slug in slugs:
                print(f"  {slug}")
        print()

    elif args.cmd == "threads":
        rows = threads(pages, args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        for r in rows:
            print(f"\n{r['date']}  {r['slug']}")
            print(f"  next: {r['next'][:140]}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
