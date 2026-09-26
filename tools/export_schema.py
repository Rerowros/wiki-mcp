#!/usr/bin/env python3
"""Emit the schema constants the Worker needs, as JSON.

The Worker pre-checks a proposal before spending a branch and a CI run on it.
Those checks need to know the page types, the directory each lives in, the
allowed statuses. Hand-writing them in TypeScript would create a second
declaration of the schema that drifts from `schema.md` silently — and this
repository has already been bitten once by two copies of one rule disagreeing.

So they are generated from wikilib, and CI fails if the checked-in file is
stale.

    python tools/export_schema.py
    python tools/export_schema.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import wikilib as W

OUT = os.path.join(W.ROOT, "worker", "src", "schema.generated.json")

# The Worker only pre-checks what is cheap and unambiguous. Everything
# type-specific — url-or-venue, stance, `replicated: null` counting as present —
# stays in lint.py, which CI runs against the branch. Duplicating those is how
# the two validators would start disagreeing.
PAYLOAD = {
    "_generated_by": "tools/export_schema.py",
    "_note": "Do not edit. Regenerate and commit when schema.md changes.",
    "typeDir": W.TYPE_DIR,
    "statuses": sorted(W.STATUSES),
    "cycleClasses": sorted(W.CYCLE_CLASSES),
    "confidence": sorted(W.CONFIDENCE),
    "commonRequired": W.COMMON_REQUIRED,
    "typeRequired": W.TYPE_REQUIRED,
    "fieldOrder": W.FIELD_ORDER,
    "generatedSlugs": sorted(W.GENERATED_SLUGS),
    "linkFmKeys": list(W.LINK_FM_KEYS),
    "loadBearingFmKeys": list(W.LOAD_BEARING_FM_KEYS),
    "slugPattern": W.SLUG_RE.pattern,
    "summaryMax": 200,
}


def render() -> str:
    return json.dumps(PAYLOAD, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    content = render()
    if args.check:
        current = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else None
        if current != content:
            print(f"stale: {os.path.relpath(OUT, W.ROOT)} — run python tools/export_schema.py")
            return 1
        print("worker schema constants are current")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if W.write_if_changed(OUT, content):
        print(f"wrote {os.path.relpath(OUT, W.ROOT)}")
    else:
        print("unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
