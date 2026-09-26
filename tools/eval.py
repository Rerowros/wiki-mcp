#!/usr/bin/env python3
"""Measure retrieval against tests/golden.yaml.

Two numbers matter, and the second one more than the first.

**recall@k** — did the right page come back at all.

**staleness rate** — how often the top answer is a page this vault already
knows is out of date. A miss is a dead end the reader notices. A stale hit is
a confident wrong number the reader does not, and on a corpus where half the
pages are point-in-time snapshots it is the failure that decides whether this
vault is safe to ask.

Without this file every change to ranking is a matter of taste. With it, a
change either moves a number or it does not.

    python tools/eval.py                  # against the local index
    python tools/eval.py --verbose        # per-question detail
    python tools/eval.py --min-recall5 0.7 --max-staleness 0.1    # for CI
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

import yaml

import query as Q
import wikilib as W

GOLDEN = os.path.join(W.ROOT, "tests", "golden.yaml")


class RemoteIndex:
    """The deployed MCP server, behind the same .search() shape as Q.Index.

    Local and served now run the same SQL over the same index, so this should
    agree with the local run. It exists to prove that rather than assume it —
    the two used to differ by 0.20 recall@5 and nothing said so.
    """

    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token
        self._call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "eval.py", "version": "1"},
        })

    def _call(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers={
                "authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                # Cloudflare's managed rules answer the default Python-urllib
                # agent with 1010 browser_signature_banned before the request
                # reaches the Worker at all — a 403 that looks like an auth
                # failure and is not one.
                "user-agent": "wiki-mcp-eval/1.0",
            })
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
        # Streamable HTTP frames the reply as SSE even for a single response.
        for line in raw.splitlines():
            line = line[6:] if line.startswith("data: ") else line
            if line.startswith("{"):
                body = json.loads(line)
                if "error" in body:
                    raise RuntimeError(body["error"])
                return body.get("result", {})
        raise RuntimeError(f"no JSON in reply: {raw[:200]}")

    def search(self, query: str, limit: int = 10, ptype: str = "",
               status: str = "", include_superseded: bool = False) -> list[dict]:
        args: dict = {"query": query, "limit": limit}
        if ptype:
            args["type"] = ptype
        if include_superseded:
            args["include_superseded"] = True
        result = self._call("tools/call", {"name": "search", "arguments": args})
        rows = json.loads(result["content"][0]["text"])
        # The tool returns `id` because that is what the MCP search contract
        # names; the local index calls the same thing `slug`.
        for row in rows:
            row.setdefault("slug", row.get("id", ""))
        return rows


def load_golden(path: str = GOLDEN) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def validate(golden: list[dict], pages: dict[str, W.Page]) -> list[str]:
    """Every slug named in the golden set must exist.

    A golden set that quietly references a renamed page reports a permanent
    miss and sends you tuning the ranker to fix a typo.
    """
    problems = []
    for item in golden:
        for field in ("expect", "stale"):
            for slug in item.get(field) or []:
                if slug not in pages:
                    problems.append(f"{item['q'][:50]!r}: {field} names a missing page {slug!r}")
        if not item.get("expect"):
            problems.append(f"{item['q'][:50]!r}: no expected page")
    return problems


def evaluate(golden: list[dict], index, k: int = 5) -> dict:
    rows = []
    for item in golden:
        expect = set(item["expect"])
        stale = set(item.get("stale") or [])
        hits = index.search(item["q"], limit=max(k, 10))
        slugs = [h["slug"] for h in hits]

        rank = next((i + 1 for i, s in enumerate(slugs) if s in expect), 0)
        rows.append({
            "q": item["q"],
            "time_sensitive": bool(item.get("time_sensitive")),
            "has_stale": bool(stale),
            "rank": rank,
            "hit_at_k": bool(rank and rank <= k),
            "top": slugs[0] if slugs else "",
            "top_is_stale": bool(slugs and slugs[0] in stale),
            "stale_in_k": sorted(stale.intersection(slugs[:k])),
            "expect": sorted(expect),
        })

    timed = [r for r in rows if r["time_sensitive"]]
    graded = [r for r in rows if r["rank"]]

    def rate(subset: list[dict], field: str) -> float:
        return (sum(1 for r in subset if r[field]) / len(subset)) if subset else 0.0

    return {
        "questions": len(rows),
        "recall_at_k": rate(rows, "hit_at_k"),
        "recall_at_k_time_sensitive": rate(timed, "hit_at_k"),
        "mrr": (sum(1 / r["rank"] for r in graded) / len(rows)) if rows else 0.0,
        # Only a question that names known-stale pages can score on this, so
        # they are the denominator. Counting all thirty would report a
        # reassuring number that mostly measures how few are annotated.
        "staleness_rate": rate([r for r in rows if r["has_stale"]], "top_is_stale"),
        "staleness_scorable": sum(1 for r in rows if r["has_stale"]),
        "k": k,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure retrieval against the golden set.")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-recall5", type=float, default=None)
    ap.add_argument("--max-staleness", type=float, default=None)
    ap.add_argument("--endpoint", metavar="URL",
                    help="Measure the deployed MCP server instead of the local index. "
                         "Token from $WIKI_MCP_TOKEN.")
    args = ap.parse_args()

    pages = W.load_pages()
    golden = load_golden()

    problems = validate(golden, pages)
    if problems:
        print("golden set does not match the vault:")
        for p in problems:
            print(f"  {p}")
        return 1

    if args.endpoint:
        token = os.environ.get("WIKI_MCP_TOKEN", "")
        if not token:
            print("set WIKI_MCP_TOKEN to measure a deployed endpoint")
            return 1
        index = RemoteIndex(args.endpoint, token)
        print(f"measuring {args.endpoint}\n")
    else:
        index = Q.Index(Q.ensure_db())
    result = evaluate(golden, index, args.k)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.verbose:
        for r in sorted(result["rows"], key=lambda r: (r["rank"] == 0, r["rank"]), reverse=True):
            mark = "MISS" if not r["rank"] else (f"#{r['rank']}" if r["hit_at_k"] else f"#{r['rank']} (below k)")
            flag = "  STALE-TOP" if r["top_is_stale"] else ""
            tag = "T" if r["time_sensitive"] else " "
            print(f"  {tag} {mark:<14}{flag:<12} {r['q'][:62]}")
            if not r["hit_at_k"]:
                print(f"      wanted {', '.join(r['expect'])}")
                print(f"      got    {r['top']}")
        print()

    print(f"  questions            {result['questions']}  ({sum(r['time_sensitive'] for r in result['rows'])} time-sensitive)")
    print(f"  recall@{result['k']}             {result['recall_at_k']:.2f}")
    print(f"  recall@{result['k']} time-sens.  {result['recall_at_k_time_sensitive']:.2f}")
    print(f"  MRR                  {result['mrr']:.3f}")
    print(f"  staleness rate       {result['staleness_rate']:.2f}   "
          f"(top answer is a known-outdated page; over the "
          f"{result['staleness_scorable']} questions that name one)")
    print()

    failed = False
    if args.min_recall5 is not None and result["recall_at_k"] < args.min_recall5:
        print(f"FAIL recall@{result['k']} {result['recall_at_k']:.2f} < {args.min_recall5}")
        failed = True
    if args.max_staleness is not None and result["staleness_rate"] > args.max_staleness:
        print(f"FAIL staleness {result['staleness_rate']:.2f} > {args.max_staleness}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
