#!/usr/bin/env python3
"""Regenerate every derived file from page frontmatter.

    python tools/build.py           # rewrite derived files
    python tools/build.py --check   # fail if they are stale (CI)

Derived files carry a `<!-- generated: -->` marker and must never be hand-edited:
`wiki/index.md`, `wiki/log.md`, `wiki/state.md`, and the `superseded_by` field.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import wikilib as W
import measurements as M

INDEX = os.path.join(W.WIKI, "index.md")
LOG = os.path.join(W.WIKI, "log.md")
STATE = os.path.join(W.WIKI, "state.md")

INDEX_SECTIONS = [
    ("overview", "Overview"),
    ("entity", "Entities"),
    ("concept", "Concepts"),
    ("thesis", "Theses"),
    ("finding", "Findings"),
    ("comparison", "Comparisons"),
    ("synthesis", "Synthesis"),
    ("query", "Open questions"),
    ("methodology", "Methodology"),
    ("source", "Sources"),
    ("research", "Research trails"),
]


def link_slugs(value) -> list[str]:
    """Slugs out of a frontmatter field holding [[links]] or bare slugs."""
    items = value if isinstance(value, list) else [value] if value else []
    out = []
    for item in items:
        if not isinstance(item, str):
            continue
        found = [W.link_target(m.group(1)) for m in W.LINK_RE.finditer(item)]
        out.extend(found or [item.strip()])
    return [s for s in out if s]


def propagate_supersession(pages: dict[str, W.Page], write: bool) -> list[str]:
    """`supersedes` on a new page becomes `superseded_by` + status on the old one."""
    incoming: dict[str, set[str]] = defaultdict(set)
    for page in pages.values():
        for target in link_slugs(page.fm.get("supersedes")):
            incoming[target].add(page.slug)

    stale = []
    for slug, page in pages.items():
        want = sorted(f"[[{s}]]" for s in incoming.get(slug, ()))
        have = page.fm.get("superseded_by") or []
        want_status = "superseded" if want else page.fm.get("status")
        if have == want and page.fm.get("status") == want_status:
            continue
        stale.append(page.rel)
        if not write:
            continue
        if want:
            page.fm["superseded_by"] = want
            page.fm["status"] = "superseded"
        else:
            page.fm.pop("superseded_by", None)
        W.write_if_changed(page.path, W.render(page))
    return stale


def build_index(pages: dict[str, W.Page]) -> str:
    by_type: dict[str, list[W.Page]] = defaultdict(list)
    for page in pages.values():
        by_type[page.type].append(page)

    out = [W.generated_header("build.py"), "\n# Wiki Index\n"]
    out.append(
        "\nOne line per page, from its `summary` frontmatter. Superseded pages are\n"
        "listed with a marker — they are kept for history, not as current answers.\n"
    )
    for ptype, heading in INDEX_SECTIONS:
        group = sorted(by_type.get(ptype, []), key=lambda p: p.slug)
        if not group:
            continue
        current = [p for p in group if p.fm.get("status") != "superseded"]
        out.append(f"\n## {heading} ({len(current)})\n\n")
        for page in group:
            summary = str(page.fm.get("summary") or page.fm.get("title") or page.slug).strip()
            mark = " ⏴superseded" if page.fm.get("status") == "superseded" else ""
            out.append(f"- [[{page.slug}]] — {summary}{mark}\n")
    return "".join(out)


def build_log(pages: dict[str, W.Page]) -> str:
    entries = [p for p in pages.values() if p.type == "log"]
    entries.sort(key=lambda p: (str(p.fm.get("date", "")), p.slug), reverse=True)

    out = [W.generated_header("build.py"), "\n# Research Log\n"]
    out.append("\nOne bullet per cycle, newest first. Source files: `wiki/log/`.\n")
    current_date = None
    for page in entries:
        date = str(page.fm.get("date", "undated"))
        if date != current_date:
            out.append(f"\n## {date}\n\n")
            current_date = date
        fm = page.fm
        parts = [f"- **Cycle:** {str(fm.get('question', '')).strip()}"]
        if fm.get("cycle_class"):
            parts.append(f" Class {fm['cycle_class']}.")
        search = ", ".join(f"[[{s}]]" for s in link_slugs(fm.get("search")))
        if search:
            parts.append(f" Search: {search}.")
        body = page.body.strip()
        if body:
            parts.append(f" Analysis: {body.splitlines()[0].strip()}")
        output = ", ".join(f"[[{s}]]" for s in link_slugs(fm.get("output")))
        if output:
            parts.append(f" Output: {output}.")
        parts.append(f" Next: {str(fm.get('next', '')).strip()}")
        out.append("".join(parts).rstrip() + "\n")
    return "".join(out)


def build_state(pages: dict[str, W.Page]) -> str:
    """Current-version observations and explicitly separate historical measurements.

Use the newest corpus date for stable generated output. Runtime tools calculate
review_due against today's UTC date; CI must not change files merely at midnight.
"""
    records = M.records_from_pages(pages)
    corpus_date = max((max(str(r.get("as_of", "")), str(r.get("observed_at", ""))[:10]) for r in records), default="1970-01-01")
    result = M.resolve(records, include_history=True, today=corpus_date)
    out = [W.generated_header("build.py"), "\n# Current state\n\n",
           "Latest recorded observations, not a live check. Benchmark versions are separate measurement scales.\n",
           "Use `query.py state` / `get_state` for review deadlines as of today. Older versions never fill missing current scores.\n"]
    for family, v in sorted(result["active_versions"].items()):
        out.append(f"\n- {family}: **v{v['version']}**, observed as of {v['as_of']} — [[{v['source']}]]\n")
    def table(rows):
        lines = ["\n| Subject | Metric | Qualifier | Value | As of | Observed at | Source |\n", "|---|---|---|---:|---|---|---|\n"]
        for r in rows:
            cells = [str(r[k]).replace("|", "\\|").replace("\n", " ") for k in ("subject", "metric", "qualifier", "value", "as_of")]
            observed = r.get('observed_at') or 'not recorded'
            lines.append(f"| {cells[0]} | {cells[1]} | {cells[2] or '—'} | **{cells[3]}** | {cells[4]} | {observed} | [[{r['source']}]] |\n")
        return lines
    out.extend(table(result["matched"]))
    if result["missing_current"]:
        out.append("\n## Current-version measurements not yet collected\n\n")
        for r in result["missing_current"]:
            out.append(f"- {r['subject']} / {r['expected_metric']} / {r['qualifier'] or 'unspecified configuration'} — unknown; no cross-version substitution.\n")
    if result["historical"]:
        out.append("\n## Historical or inactive versions — not current answers\n")
        out.extend(table(result["historical"]))
    earlier = [(r,e) for r in result["matched"] + result["historical"] for e in r["earlier"]]
    if earlier:
        out.append("\n## Earlier observations within each metric\n\n")
        for r,e in earlier:
            out.append(f"- {r['subject']} / {r['metric']} / {r['qualifier']}: {e['value']} as of {e['as_of']}; observed_at={e.get('observed_at') or 'not recorded'} — [[{e['source']}]]\n")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if derived files are stale")
    args = ap.parse_args()

    pages = W.load_pages()
    stale = propagate_supersession(pages, write=not args.check)
    if not args.check:
        pages = W.load_pages()

    targets = {INDEX: build_index(pages), LOG: build_log(pages), STATE: build_state(pages)}

    if args.check:
        bad = list(stale)
        for path, content in targets.items():
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if old != content:
                bad.append(os.path.relpath(path, W.ROOT).replace("\\", "/"))
        if bad:
            print("stale derived files — run `python tools/build.py`:")
            for b in sorted(set(bad)):
                print(f"  {b}")
            return 1
        print("derived files are up to date")
        return 0

    written = [p for p, c in targets.items() if W.write_if_changed(p, c)]
    for path in written:
        print(f"wrote {os.path.relpath(path, W.ROOT)}")
    if stale:
        print(f"updated supersession on {len(stale)} pages")
    if not written and not stale:
        print("nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
