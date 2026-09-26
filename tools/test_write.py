#!/usr/bin/env python3
"""Exercise the write path against the deployed server.

Checks the refusals first and at length, because a write tool that works is
only half of one. An agent reaches this tool holding text it fetched from the
open web, so "what does it refuse" matters more than "what does it accept".

    WIKI_MCP_TOKEN=... python tools/test_write.py https://<worker>/cli/mcp
    WIKI_MCP_TOKEN=... python tools/test_write.py --live   # actually writes a cycle

Without --live it only exercises rejections, so it is safe to run any time and
needs no GitHub token on the server.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT = os.environ.get("WIKI_CLI_MCP_URL", "")
UA = "wiki-mcp-write-test/1.0"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  - ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILURES.append(name)


def call(url: str, token: str, name: str, args: dict) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json",
                 "accept": "application/json, text/event-stream", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read().decode()[:300]}
    for line in raw.splitlines():
        line = line[6:] if line.startswith("data: ") else line
        if line.startswith("{"):
            body = json.loads(line)
            if "error" in body:
                return {"_rpc_error": body["error"]}
            text = body.get("result", {}).get("content", [{}])[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_text": text}
    return {"_raw": raw[:300]}


def page(**kw) -> dict:
    base = {"type": "finding", "slug": "x", "title": "t",
            "summary": "a summary long enough to pass the minimum", "body": "body"}
    base.update(kw)
    return base


def problems_of(res: dict) -> str:
    return " | ".join(p.get("message", "") for p in res.get("problems", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=DEFAULT)
    ap.add_argument("--live", action="store_true", help="write a real cycle and merge it")
    args = ap.parse_args()
    token = os.environ.get("WIKI_MCP_TOKEN", "")
    if not token:
        print("set WIKI_MCP_TOKEN")
        return 1
    url = args.url
    if not url:
        print("pass https://<worker>/cli/mcp or set WIKI_CLI_MCP_URL")
        return 1

    def propose(cycle: dict) -> dict:
        return call(url, token, "propose_cycle", cycle)

    ok_log = page(type="log", slug="2026-01-01-test", summary="a log entry for this test",
                  next="something to do next")
    ok_research = page(type="research", slug="write-path-probe-research-2026-01-01",
                       summary="a research trail for the write path probe",
                       question="does the write path refuse what it should")

    print("\ncontract: a cycle is a trail, outputs, and exactly one log")
    r = propose({"question": "q", "research": ok_research,
                 "log": {**ok_log, "next": ""}})
    check("log without `next` is refused", r.get("status") == "rejected" and "next" in problems_of(r),
          problems_of(r) or str(r)[:120])

    r = propose({"question": "", "research": ok_research, "log": ok_log})
    check("empty question is refused", r.get("status") == "rejected" and "question" in problems_of(r),
          problems_of(r) or str(r)[:120])

    r = propose({"question": "q", "research": page(type="finding", slug="not-a-trail",
                                                   summary="this is a finding, not a research trail"),
                 "log": ok_log})
    check("research slot must hold a research page",
          r.get("status") == "rejected" and "research" in problems_of(r).lower(),
          problems_of(r) or str(r)[:120])

    print("\nsafety: paths, injection, collisions")
    r = propose({"question": "q",
                 "research": page(type="research", slug="../../etc/passwd",
                                  summary="an attempt to escape the wiki directory",
                                  question="q"),
                 "log": ok_log})
    check("path traversal in a slug is refused", r.get("status") == "rejected",
          problems_of(r) or str(r)[:120])

    r = propose({"question": "q",
                 "research": page(type="research", slug="probe-generated-marker",
                                  summary="a page that tries to hide itself from every tool",
                                  question="q",
                                  body="<!-- generated: build.py -->\nhidden"),
                 "log": ok_log})
    check("generated marker in a body is refused",
          r.get("status") == "rejected" and "generated" in problems_of(r),
          problems_of(r) or str(r)[:120])

    r = propose({"question": "q",
                 "research": page(type="research", slug="model-context-protocol",
                                  summary="a slug that already exists in the vault", question="q"),
                 "log": ok_log})
    check("existing slug is refused",
          r.get("status") == "rejected" and "already exists" in problems_of(r),
          problems_of(r) or str(r)[:120])

    r = propose({"question": "q",
                 "research": page(type="research", slug="probe-bad-supersedes",
                                  summary="supersedes a page that does not exist", question="q",
                                  supersedes=["[[no-such-page-anywhere]]"]),
                 "log": ok_log})
    check("supersedes pointing nowhere is refused",
          r.get("status") == "rejected" and "does not exist" in problems_of(r),
          problems_of(r) or str(r)[:120])

    print("\nclaims")
    r = propose({"question": "q",
                 "research": page(type="research", slug="probe-contradiction",
                                  summary="a claim that contradicts one already recorded",
                                  question="q",
                                  claims=[{"subject": "mcp-spec", "metric": "latest-revision",
                                           "value": "1999-01-01", "as_of": "2025-11-25"}]),
                 "log": ok_log})
    check("contradicting an existing claim is refused",
          r.get("status") == "rejected" and "contradiction" in problems_of(r),
          problems_of(r) or str(r)[:120])

    r = propose({"question": "q",
                 "research": page(type="research", slug="probe-future-claim",
                                  summary="a claim dated in the future", question="q",
                                  claims=[{"subject": "probe", "metric": "probe",
                                           "value": 1, "as_of": "2099-01-01"}]),
                 "log": ok_log})
    check("a claim dated in the future is refused",
          r.get("status") == "rejected" and "future" in problems_of(r),
          problems_of(r) or str(r)[:120])

    if args.live:
        print("\nlive write")
        stamp = str(int(time.time()))
        cycle = {
            "question": "Does the MCP write path produce a valid cycle end to end?",
            "cycle_class": "E",
            "research": page(
                type="research", slug=f"write-path-probe-research-{stamp}",
                title="Write path probe trail",
                summary="Trail for the end-to-end probe of the MCP write path.",
                question="Does the MCP write path produce a valid cycle end to end?",
                cycle_class="E",
                body="Probe cycle written by tools/test_write.py --live.\n\n"
                     "unknown / not public: nothing was researched; this exercises the "
                     "write path itself.\n"),
            "log": page(
                type="log", slug=f"write-path-probe-{stamp}",
                title="Write path probe",
                summary="Probe cycle exercising the MCP write path end to end.",
                body="Probe cycle. Delete once the write path is trusted.\n",
                next="CLOSED - probe only"),
        }
        r = propose(cycle)
        check("valid cycle accepted", r.get("status") == "queued", str(r)[:200])
        if r.get("status") != "queued":
            return 1
        pid = r["proposal_id"]
        print(f"    proposal {pid} on {r['branch']}")

        for attempt in range(40):
            time.sleep(15)
            st = call(url, token, "check_proposal", {"proposal_id": pid})
            print(f"    {st.get('status')}", end="\r")
            if st.get("status") in ("merged", "failed", "unknown"):
                break
        print()
        check("cycle merged to main", st.get("status") == "merged",
              json.dumps(st)[:300])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("write path refuses what it should" + (" and writes what it should" if args.live else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
