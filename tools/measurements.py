"""Resolve dated measurements without silently mixing benchmark versions.

The policy itself is markdown data: a claim on subject <metric family>, metric
active-version, identifies the version observed at the publisher. No deployment
is needed when that observation changes. Keep in sync with worker/measurements.ts
using tests/fixtures/measurement-cases.json.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

VERSIONED = re.compile(r"^(.+)-v(\d+(?:\.\d+)*)$")

def observation(value):
    return str(value or "").replace(" ", "T").replace("+00:00", "Z")


# "owner/name" of the repository holding wiki/, for citation links.
REPO = os.environ.get("WIKI_GITHUB_REPO", "your-org/wiki-mcp")


def records_from_pages(pages):
    return [dict(c, evidence_url=c.get("source_url"), source=p.slug, path=p.rel,
                 source_url=f"https://github.com/{REPO}/blob/main/" + quote(p.rel, safe="/"),
                 page_status=p.fm.get("status", "current"),
                 review_after=str(p.fm.get("review_after") or ""))
            for p in pages.values() for c in (p.fm.get("claims") or []) if isinstance(c, dict)]


def resolve(records, subject="", metric="", version=None, include_history=False, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    records = [dict(r, observed_at=observation(r["observed_at"])) if r.get("observed_at") else r for r in records]
    active = {}
    ordered = sorted(records, key=lambda r: (str(r.get("as_of", "")),
                      str(r.get("observed_at", "")), r.get("source", "")), reverse=True)
    for r in ordered:
        if (r.get("metric") == "active-version" and r.get("page_status", "current") == "current"
                and str(r.get("as_of", "")) <= today and str(r.get("observed_at", ""))[:10] <= today):
            active.setdefault(r["subject"], {"version": str(r["value"]), "as_of": str(r["as_of"]),
                "source": r["source"], "source_url": r.get("source_url"),
                "review_due": bool(r.get("review_after") and r["review_after"] <= today)})

    requested = VERSIONED.fullmatch(metric)
    wanted_version = version or (requested.group(2) if requested else None)
    matched, historical, missing = [], [], {}
    groups = {}
    for r in ordered:
        key = (str(r.get("subject", "")), str(r.get("metric", "")), str(r.get("qualifier") or ""))
        if subject and subject.lower() not in key[0].lower():
            continue
        if metric and metric.lower() not in key[1].lower():
            continue
        groups.setdefault(key, []).append(r)
    current_cells = set()
    for (sub, met, qual), entries in sorted(groups.items()):
        eligible = [r for r in entries if r.get("page_status", "current") == "current"
                    and str(r.get("as_of", "")) <= today and str(r.get("observed_at", ""))[:10] <= today]
        row = dict((eligible or entries)[0])
        row["qualifier"] = qual
        row["as_of"] = str(row.get("as_of", ""))
        row["observed_at"] = str(row["observed_at"]) if row.get("observed_at") else None
        row["measurement_status"] = row.get("measurement_status", "unspecified")
        row["freshness"] = "review_due" if row.get("review_after") and row["review_after"] <= today else "dated_snapshot"
        parsed = VERSIONED.fullmatch(met)
        family = parsed.group(1) if parsed else (met if met in active else None)
        measured_version = parsed.group(2) if parsed else None
        row["version"] = measured_version
        row["earlier"] = [{"value": r.get("value"), "as_of": str(r.get("as_of", "")),
                           "observed_at": r.get("observed_at"), "source": r["source"],
                           "source_url": r.get("source_url"), "evidence_url": r.get("evidence_url"),
                           "page_status": r.get("page_status", "current")}
                          for r in entries if r is not (eligible or entries)[0]]
        expected = wanted_version or (active.get(family, {}).get("version") if family else None)
        if not eligible:
            row["exclusion_reason"] = "page_not_current_or_future"
        elif family and (not expected or measured_version != expected):
            row["exclusion_reason"] = "version_not_active" if expected else "active_version_not_recorded"
        elif wanted_version and not family:
            row["exclusion_reason"] = "not_a_versioned_metric"
        else:
            matched.append(row)
            if family:
                current_cells.add((sub, family, qual))
            continue
        historical.append(row)
        if family and expected:
            missing[(sub, family, qual)] = {"subject": sub, "family": family, "qualifier": qual,
                "expected_version": expected, "expected_metric": f"{family}-v{expected}",
                "reason": "No current measurement for this version/configuration; do not reuse another version."}
    return {"matched": matched, "active_versions": active,
            "missing_current": [v for k, v in sorted(missing.items()) if k not in current_cells],
            "historical": historical if include_history else [], "historical_count": len(historical),
            "note": "Latest recorded observations, not a live provider check. Compare only the same version and configuration. "
                    "Use an explicit version or include_history for older measurements; review_due requires rechecking."}
