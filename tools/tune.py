#!/usr/bin/env python3
"""Sweep ranking parameters against tests/golden.yaml.

Every ranking change in this repo has to come through here. Two of the three
biggest wins so far looked obviously right and were measurably wrong — heavy
title weights, and a corroboration bonus that rewarded page length — so
"this should help" is not evidence.

    python tools/tune.py weights      # field weights for bm25()
    python tools/tune.py types        # per-type prior
    python tools/tune.py graph        # one-hop expansion over curated links
    python tools/tune.py all

Runs against the local SQLite index, which export_index.py builds from the same
source as D1 and query.py queries with the same SQL. A number moved here is a
number moved in production.
"""
from __future__ import annotations

import argparse
import itertools
import sys

import eval as E
import query as Q
import wikilib as W


class Ranker:
    """query.Index plus the reranks under test."""

    def __init__(self, db_path: str, weights=None, type_prior=None,
                 graph_boost: float = 0.0) -> None:
        self.index = Q.Index(db_path)
        self.weights = weights or Q.WEIGHTS
        self.type_prior = type_prior or {}
        self.graph_boost = graph_boost
        self._links: dict[str, set[str]] | None = None

    def outbound(self) -> dict[str, set[str]]:
        """Curated edges only. Body prose links are not a claim of relevance."""
        if self._links is None:
            rows = self.index.db.execute(
                "SELECT src, dst FROM links WHERE kind IN "
                "('related','source','supersedes','superseded_by','entities')"
            ).fetchall()
            out: dict[str, set[str]] = {}
            for src, dst in rows:
                out.setdefault(src, set()).add(dst)
            self._links = out
        return self._links

    def search(self, query: str, limit: int = 10, ptype: str = "",
               status: str = "", include_superseded: bool = False) -> list[dict]:
        saved = Q.WEIGHTS, Q.TYPE_PRIOR, Q.GRAPH_BOOST
        Q.WEIGHTS = self.weights
        Q.TYPE_PRIOR, Q.GRAPH_BOOST = {}, 0.0
        try:
            rows = self.index.search(query, limit=Q.CHUNK_SCAN, ptype=ptype,
                                     status=status, include_superseded=include_superseded)
        finally:
            Q.WEIGHTS, Q.TYPE_PRIOR, Q.GRAPH_BOOST = saved

        scored = {r["slug"]: dict(r) for r in rows}

        for r in scored.values():
            r["score"] *= self.type_prior.get(r["type"], 1.0)

        if self.graph_boost:
            # One hop over hand-placed edges. A page linked BY a strong hit
            # inherits a fraction of it: on this vault those 6544 edges were
            # placed deliberately, so they carry more signal than co-occurrence.
            edges = self.outbound()
            top = sorted(scored.values(), key=lambda d: -d["score"])[:5]
            for hit in top:
                for dst in edges.get(hit["slug"], ()):
                    if dst in scored:
                        scored[dst]["score"] += hit["score"] * self.graph_boost

        out = sorted(scored.values(), key=lambda d: (-d["score"], d["slug"]))
        return out[:limit]


def report(name: str, ranker: Ranker, golden, baseline=None) -> tuple:
    r = E.evaluate(golden, ranker, 5)
    row = (r["recall_at_k"], r["recall_at_k_time_sensitive"], r["mrr"], r["staleness_rate"])
    delta = ""
    if baseline:
        d = row[0] - baseline[0]
        delta = f"  {d:+.2f}" if abs(d) > 1e-9 else "   ="
    print(f"  {name:<34} {row[0]:.2f}  {row[1]:.2f}  {row[2]:.3f}  {row[3]:.2f}{delta}")
    return row


ANSWERS = ("finding", "comparison", "thesis", "synthesis")
TRAILS = ("research", "source", "log")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job", nargs="?", default="all",
                    choices=["weights", "types", "graph", "all"])
    args = ap.parse_args()

    db = Q.ensure_db()
    golden = E.load_golden()
    print("                                     r@5  time    MRR  stale")
    print("  " + "-" * 58)
    base = report("baseline", Ranker(db), golden)

    if args.job in ("weights", "all"):
        print("\n  field weights (title, summary, heading, symbols, text)")
        for w in [(2, 2, 1, 4, 1), (2, 2, 1, 6, 1), (3, 3, 1, 4, 1),
                  (2, 3, 1, 4, 1), (1, 2, 1, 4, 1), (2, 2, 2, 4, 1),
                  (2, 2, 1, 4, 2)]:
            report(str(w), Ranker(db, weights=tuple(float(x) for x in w)), golden, base)

    if args.job in ("types", "all"):
        # docs/retrieval.md: "an answer-shaped question prefers finding,
        # comparison, thesis; an evidence-shaped question prefers research and
        # source". Nothing implemented it, and trails keep beating the findings
        # they produced.
        print("\n  per-type prior (trails demoted)")
        for trail in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
            prior = {t: trail for t in TRAILS}
            report(f"research/source/log x{trail}", Ranker(db, type_prior=prior), golden, base)

        print("\n  answers promoted instead")
        for ans in (1.1, 1.2, 1.3, 1.5):
            prior = {t: ans for t in ANSWERS}
            report(f"finding/comparison x{ans}", Ranker(db, type_prior=prior), golden, base)

        print("\n  both")
        for ans, trail in itertools.product((1.2, 1.3), (0.8, 0.7, 0.6)):
            prior = {t: ans for t in ANSWERS} | {t: trail for t in TRAILS}
            report(f"answers x{ans}, trails x{trail}", Ranker(db, type_prior=prior), golden, base)

    if args.job in ("graph", "all"):
        print("\n  one-hop expansion over curated links")
        for g in (0.05, 0.1, 0.2, 0.3):
            report(f"graph boost {g}", Ranker(db, graph_boost=g), golden, base)

    return 0


if __name__ == "__main__":
    sys.exit(main())
