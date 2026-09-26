#!/usr/bin/env python3
"""Validate the wiki against schema.md.

Errors fail CI. Warnings are advisory: they mark pages that are still findable
but harder to retrieve than they should be.

    python tools/lint.py
    python tools/lint.py --fix     # normalise frontmatter order and quoting
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
import math
import measurements as M

import wikilib as W

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUMMARY_MAX = 200

def valid_date(value):
    try:
        return bool(DATE_RE.fullmatch(str(value))) and bool(date.fromisoformat(str(value)))
    except (ValueError, TypeError):
        return False

def valid_observation(value):
    if valid_date(value):
        return True
    try:
        text = M.observation(value)
        return text.endswith("Z") and bool(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return False


class Report:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append((where, msg))

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append((where, msg))


def check_frontmatter(page: W.Page, rep: Report) -> None:
    fm, where = page.fm, page.rel

    for err in page.errors:
        rep.error(where, err)
    if not fm:
        return

    ptype = fm.get("type")
    if ptype not in W.TYPE_DIR:
        rep.error(where, f"unknown type: {ptype!r}")
        return

    expected_dir = W.TYPE_DIR[ptype]
    actual_dir = page.dirname if page.dirname != "wiki" else ""
    # sources/obsidian/ and other nesting is allowed under the type's own root
    if expected_dir and expected_dir not in page.rel.split("/"):
        rep.error(where, f"type {ptype!r} belongs under wiki/{expected_dir}/")

    # `replicated: null` is a real value — "we do not know" — so presence, not
    # truthiness, is what a required field means here.
    must_be_nonempty = {"type", "title", "summary", "created", "updated", "author", "status", "url", "question"}
    for fieldname in W.COMMON_REQUIRED + W.TYPE_REQUIRED.get(ptype, []):
        if fieldname not in fm:
            rep.error(where, f"missing required field: {fieldname}")
        elif fieldname in must_be_nonempty and not str(fm[fieldname] or "").strip():
            rep.error(where, f"required field is empty: {fieldname}")

    for fieldname in ("created", "updated"):
        value = fm.get(fieldname)
        if value is not None and not valid_date(value):
            rep.error(where, f"{fieldname} must be YYYY-MM-DD, got {value!r}")
    if fm.get("created") and fm.get("updated") and str(fm["updated"]) < str(fm["created"]):
        rep.error(where, "updated is earlier than created")
    if fm.get("review_after") is not None and not valid_date(fm["review_after"]):
        rep.error(where, "review_after must be a real YYYY-MM-DD date")

    if fm.get("status") not in W.STATUSES and "status" in fm:
        rep.error(where, f"status must be one of {sorted(W.STATUSES)}, got {fm['status']!r}")

    summary = fm.get("summary")
    if isinstance(summary, str):
        if len(summary) > SUMMARY_MAX:
            rep.error(where, f"summary is {len(summary)} chars, max {SUMMARY_MAX}")
        elif len(summary) < 20:
            rep.warn(where, "summary is too short to be a useful search snippet")
        elif summary.strip().lower() == str(fm.get("title", "")).strip().lower():
            rep.warn(where, "summary just repeats the title — say what the page establishes")

    if ptype == "source":
        # What makes a page a source is external authorship, not a URL: a book, a
        # screenshot or a private note is still someone else's artifact. So a
        # source must be citable — by url, or failing that by a named venue.
        if not str(fm.get("url", "")).strip():
            if not str(fm.get("venue", "")).strip():
                rep.error(where, "a source needs url or venue; our own notes belong in wiki/research/")
            else:
                rep.warn(where, "no url — cite the venue precisely enough to find the artifact again")
    if "cycle_class" in fm and fm["cycle_class"] not in W.CYCLE_CLASSES:
        rep.error(where, f"cycle_class must be one of {sorted(W.CYCLE_CLASSES)}, got {fm['cycle_class']!r}")
    if ptype == "research" and "cycle_class" not in fm:
        rep.warn(where, "no cycle_class — the class decides what output the cycle owes")
    if ptype == "finding" and fm.get("confidence") not in W.CONFIDENCE:
        rep.error(where, f"confidence must be one of {sorted(W.CONFIDENCE)}")
    if ptype == "thesis":
        if fm.get("confidence") not in W.CONFIDENCE:
            rep.error(where, f"confidence must be one of {sorted(W.CONFIDENCE)}")
        if fm.get("stance") not in {"speculative", "supported", "refuted", "settled"}:
            rep.error(where, "thesis needs stance: speculative | supported | refuted | settled")
    if ptype == "log":
        if not str(fm.get("next", "")).strip():
            rep.error(where, "log entry has no `next` — an unfinished cycle stopped at search")
        if not DATE_RE.match(str(fm.get("date", ""))):
            rep.error(where, "log entry needs date: YYYY-MM-DD")

    if "superseded_by" in fm and fm["superseded_by"] and fm.get("status") != "superseded":
        rep.error(where, "page is superseded_by something but status is not `superseded`")


def check_slug(page: W.Page, rep: Report) -> None:
    if not W.SLUG_RE.match(page.slug):
        rep.warn(page.rel, f"slug is not kebab-case: {page.slug!r}")


def resolves(target: str, pages: dict[str, W.Page]) -> bool:
    if target in pages or target in W.GENERATED_SLUGS:
        return True
    return target.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".gif"))


def check_links(pages: dict[str, W.Page], rep: Report) -> None:
    """Broken links, at two severities.

    A dead link in prose is a dead end — annoying, findable, a warning. A dead
    link in `supersedes`/`superseded_by`/`source` is worse than a dead end: the
    build silently matches nothing, the superseded page keeps `status: current`,
    and retrieval goes on serving a number this vault already knows is stale.
    """
    for page in pages.values():
        load_bearing = page.fm_links(*W.LOAD_BEARING_FM_KEYS)
        for key in W.LOAD_BEARING_FM_KEYS:
            for target in page.fm_links(key):
                if not resolves(target, pages):
                    rep.error(
                        page.rel,
                        f"{key}: [[{target}]] does not exist — a target that "
                        f"matches nothing leaves the old page marked current",
                    )
        for target in page.links() - load_bearing:
            if not resolves(target, pages):
                rep.warn(page.rel, f"link to a page that does not exist: [[{target}]]")


def check_claims(pages: dict[str, W.Page], rep: Report) -> None:
    by_key: dict[tuple, list] = defaultdict(list)
    for page in pages.values():
        claims = page.fm.get("claims") or []
        if claims and not isinstance(claims, list):
            rep.error(page.rel, "claims must be a list")
            continue
        for i, claim in enumerate(claims):
            where = f"{page.rel} claim[{i}]"
            if not isinstance(claim, dict):
                rep.error(where, "claim must be a mapping")
                continue
            for required in ("subject", "metric", "value", "as_of"):
                if claim.get(required) in (None, ""):
                    rep.error(where, f"claim missing {required}")
            for field in ("subject", "metric"):
                if not isinstance(claim.get(field), str) or not claim[field].strip():
                    rep.error(where, f"claim {field} must be a non-empty string")
            if claim.get("qualifier") is not None and not isinstance(claim["qualifier"], str):
                rep.error(where, "claim qualifier must be a string or null")
            if not isinstance(claim.get("value"), (str, int, float, bool)) or (isinstance(claim.get("value"), float) and not math.isfinite(claim["value"])):
                rep.error(where, "claim value must be a finite scalar; null means unknown and must not become a claim")
            if claim.get("as_of") and not valid_date(claim["as_of"]):
                rep.error(where, f"as_of must be YYYY-MM-DD, got {claim['as_of']!r}")
            if str(claim.get("as_of", "")) > datetime.now(timezone.utc).date().isoformat():
                rep.error(where, "claim as_of is in the future")
            if "observed_at" in claim:
                if not valid_observation(claim["observed_at"]):
                    rep.error(where, "observed_at must be a date or UTC ISO timestamp ending Z")
                elif str(claim["observed_at"])[:10] < str(claim.get("as_of", "")):
                    rep.error(where, "observed_at cannot precede as_of")
                elif M.observation(claim["observed_at"]) > datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"):
                    rep.error(where, "observed_at is in the future")
            if "source_url" in claim:
                try:
                    url = urlsplit(str(claim["source_url"]))
                    valid_url = url.scheme in {"http", "https"} and bool(url.netloc) and not url.username and not url.password
                except ValueError:
                    valid_url = False
                if not valid_url:
                    rep.error(where, "source_url must be an HTTP(S) evidence URL without credentials")
            if "snapshot_sha256" in claim and not re.fullmatch(r"[0-9a-f]{64}", str(claim["snapshot_sha256"])):
                rep.error(where, "snapshot_sha256 must be 64 lowercase hex characters")
            if str(claim.get("measurement_status", "unspecified")) not in {"measured", "reported", "estimated", "unspecified"}:
                rep.error(where, "measurement_status must be measured, reported, estimated or unspecified")
            if claim.get("metric") == "active-version":
                if not isinstance(claim.get("value"), str) or not re.fullmatch(r"\d+(?:\.\d+)+", claim["value"]):
                    rep.error(where, "active-version needs a quoted dotted version string, e.g. '4.3'")
                if not claim.get("source_url") or not claim.get("observed_at"):
                    rep.error(where, "active-version requires source_url and observed_at")
            by_key[W.claim_key(claim)].append(
                (str(claim.get("as_of", "")), claim.get("value"), page.slug, M.observation(claim.get("observed_at")))
            )

    for key, entries in by_key.items():
        same_date: dict[str, set] = defaultdict(set)
        for as_of, value, slug, observed in entries:
            same_date[as_of].add((str(value), slug, observed))
        for as_of, values in same_date.items():
            distinct = {v for v, _, _ in values}
            conflict = any(a[0] != b[0] and (not a[2] or not b[2] or a[2] == b[2]) for a in values for b in values)
            if conflict:
                subject, metric, qualifier = key
                pages_involved = ", ".join(sorted(slug for _, slug, _ in values))
                rep.error(
                    "claims",
                    f"contradiction on {subject}/{metric}"
                    f"{'/' + qualifier if qualifier else ''} as of {as_of}: "
                    f"{sorted(distinct)} in {pages_involved}",
                )


def fix(pages: dict[str, W.Page]) -> int:
    changed = 0
    for page in pages.values():
        if not page.fm or page.errors:
            continue
        new = W.render(page)
        if W.write_if_changed(page.path, new):
            changed += 1
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="normalise frontmatter field order and quoting")
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = ap.parse_args()

    pages = W.load_pages()
    if args.fix:
        n = fix(pages)
        print(f"normalised frontmatter in {n} pages")
        pages = W.load_pages()

    rep = Report()
    for page in pages.values():
        check_frontmatter(page, rep)
        check_slug(page, rep)
    check_links(pages, rep)
    check_claims(pages, rep)

    if not args.quiet:
        for where, msg in sorted(rep.warnings):
            print(f"warning  {where}: {msg}")
        for where, msg in sorted(rep.errors):
            print(f"ERROR    {where}: {msg}")

    print(f"\n{len(pages)} pages · {len(rep.errors)} errors · {len(rep.warnings)} warnings")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
