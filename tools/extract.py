#!/usr/bin/env python3
"""Propose structure the vault has earned but does not yet record.

Every job here **proposes and never writes**. Output is a report for a human or
a later cycle to act on. An extractor that edits `wiki/` directly is a way to
manufacture confident nonsense at scale.

    python tools/extract.py                # everything
    python tools/extract.py concepts       # one job
    python tools/extract.py --json         # machine-readable, for a write path

Jobs:
  concepts      terms carrying weight across many cycles with no concept page
  supersession  claims a newer page replaced without saying so
  claims        findings holding numbers but no machine-readable claims
  stale         hubs whose inbound findings are newer than the hub itself
  gaps          recorded unknowns and open questions, collected
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

import wikilib as W

HUB_TYPES = {"entity", "concept"}
ANSWER_TYPES = {"finding", "comparison", "synthesis", "thesis"}

CLEAN = re.compile(r"[^a-z0-9+.\- ]")
NUMERIC_TABLE_ROW = re.compile(r"^\|.*\|\s*\**\d[\d.,]*\**\s*\|", re.M)


def normalise(phrase: str) -> str:
    phrase = phrase.lower().replace("’", "'")
    phrase = re.sub(r"\[\[|\]\]|`", " ", phrase)
    phrase = CLEAN.sub(" ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    return phrase


def known_terms(pages: dict[str, W.Page]) -> set[str]:
    """Everything the vault already has a *page* for, in comparable form.

    Deliberately excludes tags: a tag is the candidate being tested, so folding
    tags in here would make every candidate match itself.
    """
    known = set()
    for page in pages.values():
        known.add(normalise(page.slug.replace("-", " ")))
        known.add(normalise(str(page.fm.get("title", ""))))
    return {k for k in known if k}


# Tags that describe the filing, not the subject.
STRUCTURAL_TAGS = set(W.TYPE_DIR) | {
    "sources", "wiki-plan", "research-cycle", "demo",
}


def job_concepts(pages: dict[str, W.Page], limit: int = 25) -> list[dict]:
    """Subjects carried across many cycles with no page of their own.

    The signal is the author's own tags, not phrases mined from the prose. Bold
    text was tried first and does not work on this corpus: the writing bolds
    SKUs and negations for emphasis ("**not** GA", "**3.8 Flash**"), so a
    bold-phrase miner returns model names and stopword pairs, never concepts.

    Ranked by spread, not frequency: a tag on eight pages from six different
    dates is a subject the vault keeps returning to; a tag on eight pages
    written the same day is one cycle's vocabulary.
    """
    known = known_terms(pages)

    pages_by_tag: dict[str, set[str]] = defaultdict(set)
    dates_by_tag: dict[str, set[str]] = defaultdict(set)
    for page in pages.values():
        for tag in page.fm.get("tags") or []:
            tag = str(tag).lower().strip()
            if not tag or tag in STRUCTURAL_TAGS:
                continue
            if any(ch.isdigit() for ch in tag):
                continue  # model-5.6, lib-3.7 — versioned ids belong to an entity page
            pages_by_tag[tag].add(page.slug)
            dates_by_tag[tag].add(str(page.fm.get("created", "")))

    out = []
    for tag, slugs in pages_by_tag.items():
        term = normalise(tag.replace("-", " "))
        if not term or term in known or any(term in k for k in known):
            continue
        # Recurring across cycles, or heavily used within one: a sweep that
        # produced eight pages in a day is still a subject that earned a page.
        if not (len(slugs) >= 4 and len(dates_by_tag[tag]) >= 2 or len(slugs) >= 6):
            continue
        out.append({
            "term": term,
            "tag": tag,
            "pages": len(slugs),
            "distinct_dates": len(dates_by_tag[tag]),
            "examples": sorted(slugs)[:4],
        })
    out.sort(key=lambda d: (d["distinct_dates"], d["pages"]), reverse=True)
    return out[:limit]


def job_supersession(pages: dict[str, W.Page]) -> list[dict]:
    """A newer claim replacing an older one, where nobody said `supersedes`."""
    by_key: dict[tuple, list] = defaultdict(list)
    for page in pages.values():
        for claim in page.fm.get("claims") or []:
            if isinstance(claim, dict):
                by_key[W.claim_key(claim)].append((str(claim.get("as_of", "")), claim.get("value"), page))

    out = []
    for key, entries in by_key.items():
        entries.sort(key=lambda e: e[0], reverse=True)
        newest_date, newest_value, newest_page = entries[0]
        declared = set()
        for item in newest_page.fm.get("supersedes") or []:
            declared.update(W.link_target(m.group(1)) for m in W.LINK_RE.finditer(str(item)))
        for old_date, old_value, old_page in entries[1:]:
            if old_page.slug == newest_page.slug or str(old_value) == str(newest_value):
                continue
            if old_page.slug in declared:
                continue
            out.append({
                "subject": key[0], "metric": key[1], "qualifier": key[2],
                "newer": newest_page.slug, "newer_value": newest_value, "newer_as_of": newest_date,
                "older": old_page.slug, "older_value": old_value, "older_as_of": old_date,
                "fix": f"add supersedes: [[{old_page.slug}]] to {newest_page.slug}",
            })
    return out


def job_claims(pages: dict[str, W.Page], limit: int = 30) -> list[dict]:
    """Findings that state numbers in a table but carry no claims block."""
    out = []
    for page in pages.values():
        if page.type != "finding" or page.fm.get("claims"):
            continue
        rows = len(NUMERIC_TABLE_ROW.findall(page.body))
        if rows < 2:
            continue
        out.append({"page": page.slug, "numeric_rows": rows, "summary": str(page.fm.get("summary", ""))[:90]})
    out.sort(key=lambda d: d["numeric_rows"], reverse=True)
    return out[:limit]


def job_stale(pages: dict[str, W.Page], limit: int = 20) -> list[dict]:
    """Hub pages whose inbound findings are newer than the hub itself."""
    inbound: dict[str, list[W.Page]] = defaultdict(list)
    for page in pages.values():
        for target in page.links():
            if target in pages:
                inbound[target].append(page)

    out = []
    for slug, page in pages.items():
        if page.type not in HUB_TYPES:
            continue
        updated = str(page.fm.get("updated", ""))
        newer = [p for p in inbound.get(slug, ())
                 if p.type in ANSWER_TYPES and str(p.fm.get("created", "")) > updated]
        if not newer:
            continue
        out.append({
            "hub": slug, "updated": updated, "newer_inbound": len(newer),
            "newest": max(str(p.fm.get("created", "")) for p in newer),
            "examples": sorted(p.slug for p in newer)[:3],
        })
    out.sort(key=lambda d: d["newer_inbound"], reverse=True)
    return out[:limit]


def job_gaps(pages: dict[str, W.Page], limit: int = 25) -> list[dict]:
    """Recorded unknowns and still-open questions, collected in one place."""
    out = []
    for page in pages.values():
        hits = len(re.findall(r"unknown\s*/\s*not public", page.body, re.I))
        if hits:
            out.append({"page": page.slug, "type": page.type, "unknowns": hits})
    for page in pages.values():
        if page.type == "query" and page.fm.get("status") == "current":
            out.append({"page": page.slug, "type": "query", "unknowns": None})
    out.sort(key=lambda d: (d["unknowns"] or 0), reverse=True)
    return out[:limit]


JOBS = {
    "concepts": (job_concepts, "Concept candidates — weight across cycles, no page yet"),
    "supersession": (job_supersession, "Undeclared supersession between claims"),
    "claims": (job_claims, "Findings with numbers but no claims block"),
    "stale": (job_stale, "Hubs older than the findings pointing at them"),
    "gaps": (job_gaps, "Recorded unknowns and open questions"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="*", choices=sorted(JOBS) + [], default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pages = W.load_pages()
    selected = args.jobs or sorted(JOBS)
    results = {name: JOBS[name][0](pages) for name in selected}

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    for name in selected:
        rows = results[name]
        print(f"\n## {JOBS[name][1]}  ({len(rows)})\n")
        if not rows:
            print("  nothing to propose")
            continue
        for row in rows:
            if name == "concepts":
                print(f"  {row['term']:<42} {row['pages']} pages / {row['distinct_dates']} dates"
                      f"   e.g. {', '.join(row['examples'][:2])}")
            elif name == "supersession":
                print(f"  {row['subject']}/{row['metric']}: {row['older_value']} ({row['older_as_of']}) "
                      f"-> {row['newer_value']} ({row['newer_as_of']})\n      {row['fix']}")
            elif name == "claims":
                print(f"  {row['page']:<52} {row['numeric_rows']} numeric rows")
            elif name == "stale":
                print(f"  {row['hub']:<42} updated {row['updated']}, "
                      f"{row['newer_inbound']} newer inbound (to {row['newest']})")
            else:
                mark = f"{row['unknowns']} unknowns" if row["unknowns"] else "open query"
                print(f"  {row['page']:<52} {mark}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
