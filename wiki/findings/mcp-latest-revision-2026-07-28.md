---
type: finding
title: MCP 2026-07-28 is the current spec revision (checked 2026-09-27)
summary: MCP 2026-07-28 is the current revision — stateless (no initialize handshake, no session header), server/discover added, Dynamic Client Registration deprecated in favour of CIMD.
tags:
- mcp
- specification
related:
- '[[model-context-protocol]]'
- '[[oauth-2-1-for-remote-mcp]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
supersedes:
- '[[mcp-latest-revision-2025-11-25]]'
source: '[[mcp-spec-revisions-research-2026-09-27]]'
confidence: high
replicated: null
review_after: 2027-01-15
claims:
- subject: mcp-spec
  metric: latest-revision
  value: '2026-07-28'
  as_of: 2026-07-28
  observed_at: '2026-09-26T21:45:00Z'
  source_url: https://modelcontextprotocol.io/specification/versioning
  measurement_status: reported
- subject: mcp-spec
  metric: dynamic-client-registration-status
  value: deprecated
  as_of: 2026-07-28
  observed_at: '2026-09-26T21:45:00Z'
  source_url: https://modelcontextprotocol.io/specification/2026-07-28/changelog
  measurement_status: reported
- subject: mcp-spec
  metric: protocol-sessions
  value: removed
  as_of: 2026-07-28
  observed_at: '2026-09-26T21:45:00Z'
  source_url: https://modelcontextprotocol.io/specification/2026-07-28/changelog
  measurement_status: reported
---

# MCP 2026-07-28 is the current spec revision (checked 2026-09-27)

**Finding.** The official versioning page named 2026-07-28 as the current MCP
revision when read on 2026-09-27. "Current" means it may still receive
backwards-compatible changes; `review_after` asks for a recheck, it does not
promise the answer holds until then.

Changes relative to 2025-11-25 that matter for a remote server:

| Change | Effect on a remote server |
|---|---|
| Protocol-level sessions and the `Mcp-Session-Id` header removed | no per-connection state; a fresh server per request is fine |
| `initialize` handshake removed; version and capabilities travel in each request's `_meta` | every request is self-describing |
| `server/discover` added and mandatory for servers | one call returns versions, capabilities, identity |
| SSE stream resumability (`Last-Event-ID`) removed | a broken stream means the client re-issues the request |
| Dynamic Client Registration (RFC 7591) deprecated in favour of Client ID Metadata Documents | keep DCR only for backwards compatibility |
| Authorization servers should send `iss` (RFC 9207); clients must validate it | mix-up attack defence |
| Tasks moved out of the core into an official extension | optional capability |

The Worker in this repository keeps both CIMD and DCR enabled, because several
clients still register dynamically.

Source: [[mcp-specification-changelogs]]. Trail: [[mcp-spec-revisions-research-2026-09-27]].

Falsifier: the versioning page naming a different current revision, or the
2026-07-28 changelog not listing DCR under Deprecated.
