#!/usr/bin/env python3
"""Regression tests for invariants that failed silently once already.

Each test here corresponds to a bug that produced no error message: a
contradiction the linter did not report, a derived file that differed between
two machines, a page invisible to every tool. Silent failures do not announce
themselves on the next regression either, which is why they get tests and the
loud ones do not.

    python tests/test_invariants.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import build  # noqa: E402
import lint  # noqa: E402
import wikilib as W  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}{'  — ' + detail if detail else ''}")
        FAILURES.append(name)


def page(slug: str, **fm) -> W.Page:
    fm.setdefault("type", "finding")
    body = fm.pop("_body", "")
    path = os.path.join(W.WIKI, W.TYPE_DIR.get(fm["type"], ""), slug + ".md")
    return W.Page(slug=slug, path=path, fm=fm, body=body, raw="")


# --- claim identity ---------------------------------------------------------
# lint.py keyed an explicit `qualifier: null` as the string "None" while
# build.py keyed it as "", so a real contradiction between a null-qualifier
# claim and a no-qualifier claim was invisible to the linter and silently
# collapsed by build_state — dropping one of the two values from state.md.

def test_claim_key() -> None:
    absent = W.claim_key({"subject": "s", "metric": "m"})
    null = W.claim_key({"subject": "s", "metric": "m", "qualifier": None})
    empty = W.claim_key({"subject": "s", "metric": "m", "qualifier": ""})
    check("claim_key: absent == null qualifier", absent == null, f"{absent!r} vs {null!r}")
    check("claim_key: absent == empty qualifier", absent == empty, f"{absent!r} vs {empty!r}")
    check("claim_key: qualifier never stringified None", "None" not in null)

    rep = lint.Report()
    pages = {
        "a": page("a", claims=[{"subject": "s", "metric": "m", "value": 1, "as_of": "2026-09-01"}]),
        "b": page("b", claims=[{"subject": "s", "metric": "m", "qualifier": None,
                                "value": 2, "as_of": "2026-09-01"}]),
    }
    lint.check_claims(pages, rep)
    check("linter reports null-vs-absent contradiction",
          any("contradiction" in m for _, m in rep.errors), f"errors={rep.errors}")


# --- derived-file determinism ----------------------------------------------
# build_state sorted on as_of alone. Python's sort is stable, so ties kept
# os.walk order, which differs between a Windows worktree and ubuntu-latest:
# state.md would render differently on each and fail `build.py --check`.

def test_build_state_deterministic() -> None:
    claims = [{"subject": "s", "metric": "m", "value": "v", "as_of": "2026-09-01"}]
    pages = {c: page(c, claims=claims) for c in "abcdefgh"}

    renders = set()
    for _ in range(8):
        shuffled = list(pages.items())
        random.shuffle(shuffled)
        renders.add(build.build_state(dict(shuffled)))
    check("build_state is order-independent", len(renders) == 1,
          f"{len(renders)} distinct renders from the same pages")


# --- link severity ----------------------------------------------------------
# A broken `supersedes` target matches nothing, so the superseded page keeps
# status: current and retrieval goes on serving the stale number. That was a
# warning, which CI does not fail on.

def test_link_severity() -> None:
    rep = lint.Report()
    pages = {
        "new": page("new", supersedes=["[[typo-that-does-not-exist]]"]),
        "prose": page("prose", _body="see [[also-missing]] for context"),
    }
    lint.check_links(pages, rep)
    check("broken supersedes is an error",
          any("typo-that-does-not-exist" in m for _, m in rep.errors), f"errors={rep.errors}")
    check("broken body link stays a warning",
          any("also-missing" in m for _, m in rep.warnings), f"warnings={rep.warnings}")


# --- generated-marker suppression ------------------------------------------
# is_generated scans the first 2000 chars and load_pages skipped the file, so a
# page carrying the marker vanished from lint, build and every index while
# still looking correct in Obsidian.

def test_generated_marker_cannot_hide_a_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wiki = os.path.join(tmp, "wiki", "findings")
        os.makedirs(wiki)
        with open(os.path.join(wiki, "sneaky.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntype: finding\ntitle: x\n---\n\n<!-- generated: nope -->\nbody\n")
        real_root, real_wiki = W.ROOT, W.WIKI
        try:
            W.ROOT, W.WIKI = tmp, os.path.join(tmp, "wiki")
            pages = W.load_pages()
        finally:
            W.ROOT, W.WIKI = real_root, real_wiki

    check("page with a stray generated marker is still loaded", "sneaky" in pages)
    if "sneaky" in pages:
        check("and it carries an error",
              any("generated" in e for e in pages["sneaky"].errors), f"{pages['sneaky'].errors}")


def main() -> int:
    for fn in (test_claim_key, test_build_state_deterministic,
               test_link_severity, test_generated_marker_cannot_hide_a_page):
        print(f"\n{fn.__name__}")
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
