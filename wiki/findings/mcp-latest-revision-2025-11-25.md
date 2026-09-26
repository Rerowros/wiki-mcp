---
type: finding
title: MCP 2025-11-25 is the latest spec revision (as of 2025-11-25)
summary: As of 2025-11-25 the newest MCP revision recommended Client ID Metadata Documents for client registration and added experimental tasks and OIDC discovery.
tags:
- mcp
- specification
related:
- '[[model-context-protocol]]'
- '[[oauth-2-1-for-remote-mcp]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: superseded
supersedes:
- '[[mcp-latest-revision-2025-06-18]]'
superseded_by:
- '[[mcp-latest-revision-2026-07-28]]'
source: '[[mcp-spec-revisions-research-2026-09-27]]'
confidence: high
replicated: null
claims:
- subject: mcp-spec
  metric: latest-revision
  value: '2025-11-25'
  as_of: 2025-11-25
  observed_at: '2026-09-26T21:45:00Z'
  source_url: https://modelcontextprotocol.io/specification/2025-11-25/changelog
  measurement_status: reported
---

# MCP 2025-11-25 is the latest spec revision (as of 2025-11-25)

**Finding.** Revision 2025-11-25 replaced 2025-06-18 as the newest MCP
specification. It is itself a dated snapshot, later superseded.

Changes relative to 2025-06-18 that matter for a remote server:

| Change | Effect on a remote server |
|---|---|
| OAuth Client ID Metadata Documents as a recommended registration mechanism | clients may use an HTTPS URL as `client_id`; the server fetches it |
| OpenID Connect Discovery 1.0 for authorization-server discovery | a second metadata location clients may try |
| Incremental scope consent via `WWW-Authenticate` | a server can ask for more scope later |
| Protected resource metadata discovery aligned with RFC 9728; header optional with `.well-known` fallback | discovery works without the 401 header |
| Experimental tasks | durable requests with polling |
| Invalid `Origin` on Streamable HTTP must get HTTP 403 | DNS-rebinding defence |

Source: [[mcp-specification-changelogs]]. Trail: [[mcp-spec-revisions-research-2026-09-27]].

Falsifier: the official 2025-11-25 changelog not listing Client ID Metadata
Documents as a recommended mechanism.
