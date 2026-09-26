---
type: finding
title: MCP 2025-06-18 is the latest spec revision (as of 2025-06-18)
summary: As of 2025-06-18 the newest MCP revision classified servers as OAuth resource servers, required RFC 8707 resource indicators and removed JSON-RPC batching.
tags:
- mcp
- specification
entities: []
related:
- '[[model-context-protocol]]'
- '[[oauth-2-1-for-remote-mcp]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: superseded
superseded_by:
- '[[mcp-latest-revision-2025-11-25]]'
source: '[[mcp-spec-revisions-research-2026-09-27]]'
confidence: high
replicated: null
claims:
- subject: mcp-spec
  metric: latest-revision
  value: '2025-06-18'
  as_of: 2025-06-18
  observed_at: '2026-09-26T21:45:00Z'
  source_url: https://modelcontextprotocol.io/specification/2025-06-18/changelog
  measurement_status: reported
---

# MCP 2025-06-18 is the latest spec revision (as of 2025-06-18)

**Finding.** On its release date, revision 2025-06-18 was the newest MCP
specification. This is a dated snapshot kept for history — later revisions
exist; see the pages that supersede it.

Changes relative to 2025-03-26 that matter for a remote server:

| Change | Effect on a remote server |
|---|---|
| Servers classified as OAuth resource servers, with protected resource metadata | serve RFC 9728 metadata so clients find the authorization server |
| Clients must send RFC 8707 resource indicators | tokens are bound to one server's `resource` URL |
| JSON-RPC batching removed | one request per message |
| Structured tool output and resource links added | tools may return typed JSON |
| Elicitation added | servers may ask the user for input mid-call |
| `MCP-Protocol-Version` header required on HTTP after negotiation | transport must accept it |

Source: [[mcp-specification-changelogs]]. Trail: [[mcp-spec-revisions-research-2026-09-27]].

Falsifier: the official 2025-06-18 changelog listing a different predecessor or
omitting the resource-server classification.
