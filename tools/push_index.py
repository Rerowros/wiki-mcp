#!/usr/bin/env python3
"""Push the generated index to the deployed Worker, which writes it to D1.

CI cannot talk to D1 with wrangler — minting a scoped Cloudflare API token needs
permissions this deployment does not have — so the SQL goes to the Worker, which
already holds the binding. One shared secret in GitHub instead of a Cloudflare
token, which is the smaller blast radius regardless.

Incremental by default: it asks the Worker which commit the index was built
from and diffs against that. The baseline comes from D1 rather than from HEAD~1
because GitHub Actions coalesces queued runs in a concurrency group, so the
previous commit is not reliably the previous *indexed* commit.

    WIKI_ADMIN_TOKEN=... python tools/push_index.py https://<worker>
    WIKI_ADMIN_TOKEN=... python tools/push_index.py https://<worker> --full
    WIKI_BASE_URL=https://<worker> WIKI_ADMIN_TOKEN=... python tools/push_index.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import wikilib as W

# One request per this many statements. Small enough to stay well inside a
# Worker invocation, large enough that a full rebuild is tens of requests
# rather than thousands.
BATCH = 32  # Reserve the rest of the Free plan's 50 queries for protocol bookkeeping.
UA = "wiki-mcp-push/1.0"


def post(url: str, token: str, payload: object) -> dict:
    body = json.dumps(payload, ensure_ascii=False)
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json",
                 "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"user-agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def reachable(sha: str) -> bool:
    if not sha:
        return False
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                          cwd=W.ROOT, capture_output=True).returncode == 0


def recovery_action(health: dict, now_ms: int | None = None) -> str:
    """Return ready, wait, abort_full, or full for the observed generation."""
    state = health.get("index_state", "ready")
    if state == "ready":
        return "ready"
    active = health.get("index_run_id") or ""
    lease_until = int(health.get("index_lease_until") or 0)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if state == "building" and active and lease_until > now_ms:
        return "wait"
    return "abort_full" if active else "full"


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the search index to the Worker.")
    ap.add_argument("base", nargs="?", default=os.environ.get("WIKI_BASE_URL", ""),
                    help="Worker origin, e.g. https://wiki-mcp.<account>.workers.dev (or $WIKI_BASE_URL)")
    ap.add_argument("--full", action="store_true", help="rebuild everything, ignoring meta.head_sha")
    ap.add_argument("--out", default=os.path.join(W.ROOT, "dist", "push"))
    args = ap.parse_args()

    token = os.environ.get("WIKI_ADMIN_TOKEN", "")
    if not token:
        print("set WIKI_ADMIN_TOKEN")
        return 1
    if not args.base:
        print("pass the Worker origin or set WIKI_BASE_URL")
        return 1

    base = args.base.rstrip("/")
    stored = ""
    health: dict = {}
    try:
        health = get(f"{base}/health")
        if not args.full:
            stored = health.get("head_sha") or ""
    except Exception as e:
        print(f"could not read /health ({e}); falling back to a full rebuild")
        args.full = True

    # A prior process may have died after mutating some tables but before the
    # finish operation.  That database is deliberately unreadable.  Release
    # its token and replace it from markdown in full; an incremental diff from
    # the old head cannot repair an unknown half-applied batch.
    state = health.get("index_state", "ready")
    recovery = recovery_action(health)
    if recovery != "ready":
        active = health.get("index_run_id") or ""
        lease_until = int(health.get("index_lease_until") or 0)
        if recovery == "wait":
            remaining = max(1, (lease_until - int(time.time() * 1000) + 999) // 1000)
            print(f"another rebuild is active (lease has {remaining}s left); retry later")
            return 1
        if recovery == "abort_full":
            res = post(f"{base}/admin/reindex", token, {
                "operation": "abort", "run_id": active,
            })
            if not res.get("ok"):
                print(f"could not abandon interrupted rebuild: {res.get('error')}")
                return 1
        print(f"recovering index left {state}; forcing a full rebuild")
        args.full = True

    mode = ["--full"] if args.full or not reachable(stored) else ["--since", stored]
    if mode[0] == "--since":
        print(f"incremental from {stored[:12]}")
    else:
        print("full rebuild")

    subprocess.run([sys.executable, os.path.join(W.ROOT, "tools", "export_index.py"),
                    *mode, "--out", args.out], check=True)

    # The exact statement list, not the .sql text. Splitting that text on
    # semicolons would be wrong: page bodies contain them inside string
    # literals, constantly.
    with open(os.path.join(args.out, "statements.json"), encoding="utf-8") as fh:
        statements: list[str] = json.load(fh)
    with open(os.path.join(args.out, "manifest.json"), encoding="utf-8") as fh:
        manifest: dict = json.load(fh)
    if manifest.get("statement_count") != len(statements):
        print("generated index manifest does not match statements.json")
        return 1

    run_id = str(uuid.uuid4())
    expected_batches = (len(statements) + BATCH - 1) // BATCH
    start = post(f"{base}/admin/reindex", token, {
        "operation": "start",
        "run_id": run_id,
        "target_sha": manifest["target_sha"],
        "base_sha": manifest["base_sha"],
        "mode": manifest["mode"],
        "expected_batches": expected_batches,
        "expected_statements": len(statements),
    })
    if not start.get("ok"):
        print(f"could not start rebuild: {start.get('error')}")
        return 1

    total = 0
    for i in range(0, len(statements), BATCH):
        chunk = statements[i:i + BATCH]
        res = post(f"{base}/admin/reindex", token, {
            "operation": "batch",
            "run_id": run_id,
            "batch_index": i // BATCH,
            "statements": chunk,
        })
        if not res.get("ok"):
            print(f"FAILED at statement {i}: {res.get('error')}")
            print("index remains unavailable; the next push will replace it with a full rebuild")
            return 1
        total += res.get("count") or 0
        print(f"  {min(i + BATCH, len(statements)):>6} / {len(statements)} statements", end="\r")

    finish = post(f"{base}/admin/reindex", token, {
        "operation": "finish",
        "run_id": run_id,
        "target_sha": manifest["target_sha"],
        "expected_batches": expected_batches,
        "expected_statements": len(statements),
    })
    if not finish.get("ok"):
        print(f"could not finish rebuild: {finish.get('error')}")
        return 1

    print(f"\npushed {len(statements)} statements, {total} executed")
    health = get(f"{base}/health")
    print(f"index now: {health.get('pages')} pages at {str(health.get('head_sha'))[:12]}")
    if health.get("index_state", "ready") != "ready" or health.get("head_sha") != manifest["target_sha"]:
        print("FAILED: server did not publish the requested index generation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
