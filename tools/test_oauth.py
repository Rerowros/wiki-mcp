#!/usr/bin/env python3
"""Walk the whole OAuth flow against the deployed server and call a tool.

Discovery documents that look correct prove nothing — the flow either issues a
token that opens /mcp or it does not. This does what claude.ai does: dynamic
client registration, an authorization request with PKCE, the consent form, the
code exchange, and one authenticated tool call.

    WIKI_PASSPHRASE=... python tools/test_oauth.py https://<worker>/mcp

Also checks the failure paths, because an auth layer that only works is not
the same as one that also refuses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "wiki-mcp-oauth-test/1.0"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  - ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILURES.append(name)


def request(url: str, method: str = "GET", data=None, headers=None, redirect=True,
            as_json: bool = False):
    """as_json picks the encoding: /register takes JSON, /token takes a form."""
    body = None
    hdrs = {"user-agent": UA, **(headers or {})}
    if data is not None:
        if as_json:
            body = json.dumps(data).encode()
            hdrs.setdefault("content-type", "application/json")
        else:
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("content-type", "application/x-www-form-urlencoded")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(*( [] if redirect else [NoRedirect] ))
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def main() -> int:
    mcp_url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WIKI_MCP_URL", "")
    if not mcp_url:
        print("pass https://<worker>/mcp or set WIKI_MCP_URL")
        return 1
    base = mcp_url.rsplit("/", 1)[0]
    passphrase = os.environ.get("WIKI_PASSPHRASE", "")
    if not passphrase:
        print("set WIKI_PASSPHRASE")
        return 1

    print("\ndiscovery")
    status, headers, _ = request(mcp_url, "POST", as_json=True,
                                 data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    check("unauthenticated /mcp is 401", status == 401, f"got {status}")
    www = headers.get("WWW-Authenticate", "")
    check("401 carries resource_metadata", "resource_metadata=" in www, www[:80])

    prm_url = www.split('resource_metadata="', 1)[1].split('"', 1)[0]
    _, _, prm_raw = request(prm_url)
    prm = json.loads(prm_raw)
    check("PRM resource matches the URL exactly", prm.get("resource") == mcp_url,
          f"{prm.get('resource')} vs {mcp_url}")

    as_url = prm["authorization_servers"][0].rstrip("/") + "/.well-known/oauth-authorization-server"
    _, _, as_raw = request(as_url)
    meta = json.loads(as_raw)
    check("S256 PKCE advertised", "S256" in (meta.get("code_challenge_methods_supported") or []))
    check("CIMD advertised", meta.get("client_id_metadata_document_supported") is True)
    check("public clients allowed", "none" in (meta.get("token_endpoint_auth_methods_supported") or []))
    check("refresh tokens offered", "refresh_token" in (meta.get("grant_types_supported") or []))

    print("\nregistration")
    redirect_uri = "https://claude.ai/api/mcp/auth_callback"
    status, _, raw = request(meta["registration_endpoint"], "POST", as_json=True, data={
        "client_name": "oauth flow test",
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    })
    check("dynamic registration accepted", status in (200, 201), f"got {status}: {raw[:120]}")
    if status not in (200, 201):
        return 1
    client_id = json.loads(raw)["client_id"]

    print("\nauthorization")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    auth_url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": "wiki.read offline_access", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": mcp_url,
    })
    status, _, html = request(auth_url)
    check("consent page served", status == 200 and "passphrase" in html.lower(), f"got {status}")

    token_req = html.split('name="req" value="', 1)[1].split('"', 1)[0]

    status, _, _ = request(meta["authorization_endpoint"], "POST",
                           {"req": token_req, "passphrase": "definitely-wrong"})
    check("wrong passphrase refused", status == 401, f"got {status}")

    status, hdrs, _ = request(meta["authorization_endpoint"], "POST",
                              {"req": token_req, "passphrase": passphrase}, redirect=False)
    check("correct passphrase redirects", status in (301, 302), f"got {status}")
    location = hdrs.get("Location", "")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    check("state round-tripped", params.get("state", [""])[0] == state)
    code = params.get("code", [""])[0]
    check("authorization code issued", bool(code))

    print("\ntoken exchange")
    status, _, raw = request(meta["token_endpoint"], "POST", {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": "wrong-verifier-entirely",
    })
    check("wrong PKCE verifier refused", status >= 400, f"got {status}")

    status, _, raw = request(meta["token_endpoint"], "POST", {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier, "resource": mcp_url,
    })
    check("token issued", status == 200, f"got {status}: {raw[:160]}")
    if status != 200:
        return 1
    tokens = json.loads(raw)
    check("refresh token issued", bool(tokens.get("refresh_token")))

    print("\nauthenticated call")
    status, _, raw = request(mcp_url, "POST", as_json=True,
                             data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"authorization": f"Bearer {tokens['access_token']}",
                                      "accept": "application/json, text/event-stream"})
    payload = next((json.loads(l[6:]) for l in raw.splitlines() if l.startswith("data: ")), {})
    names = [t["name"] for t in payload.get("result", {}).get("tools", [])]
    check("tools/list over OAuth", status == 200 and "search" in names, f"got {status}, {names}")

    status, _, raw = request(mcp_url, "POST", as_json=True, data={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_state", "arguments": {"subject": "mcp-spec", "metric": "latest-revision"}},
    }, headers={"authorization": f"Bearer {tokens['access_token']}",
                "accept": "application/json, text/event-stream"})
    payload = next((json.loads(l[6:]) for l in raw.splitlines() if l.startswith("data: ")), {})
    text = payload.get("result", {}).get("content", [{}])[0].get("text", "")
    check("get_state over OAuth", "2025-11-25" in text, text[:120])

    print("\nrefresh")
    status, _, raw = request(meta["token_endpoint"], "POST", {
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
    })
    check("refresh token exchanges", status == 200, f"got {status}: {raw[:160]}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("OAuth flow works end to end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
