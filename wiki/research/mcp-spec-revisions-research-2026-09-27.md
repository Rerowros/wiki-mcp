---
type: research
title: MCP spec revisions research 2026-09-27
summary: Trail for which MCP revision is current and what its authorization changes mean for a remote server — official versioning page and three changelogs read.
tags:
- mcp
- specification
related:
- '[[mcp-specification-changelogs]]'
- '[[model-context-protocol]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
question: Which MCP specification revision is current, and does it still expect remote servers to support Dynamic Client Registration?
cycle_class: A
---

# MCP spec revisions research 2026-09-27

## What was fetched

- <https://modelcontextprotocol.io/specification/versioning> — fetched
  2026-09-27 — names 2026-07-28 as the current revision; revisions are Draft,
  Current or Final.
- <https://modelcontextprotocol.io/specification/2025-06-18/changelog> —
  fetched 2026-09-27 — resource-server classification, RFC 8707, no batching.
- <https://modelcontextprotocol.io/specification/2025-11-25/changelog> —
  fetched 2026-09-27 — CIMD recommended, OIDC discovery, experimental tasks.
- <https://modelcontextprotocol.io/specification/2026-07-28/changelog> —
  fetched 2026-09-27 — stateless protocol, `server/discover`, DCR deprecated.

## Confirmed

- Current revision: 2026-07-28 → [[mcp-latest-revision-2026-07-28]].
- Earlier revisions recorded as dated, superseded snapshots:
  [[mcp-latest-revision-2025-11-25]], [[mcp-latest-revision-2025-06-18]].
- DCR is Deprecated, not Removed: it stays available for authorization servers
  that do not support Client ID Metadata Documents, and the deprecation window
  is at least twelve months under the spec's feature-lifecycle policy.

## Unknown / not public

- When the next revision will ship. Not stated on the pages read.
- Which clients already rely on CIMD only. Not in the specification; would need
  per-client documentation.

## Stop rule hit

Question answered from the primary source; the next page would add footnotes,
not change the decision (keep DCR enabled alongside CIMD).
