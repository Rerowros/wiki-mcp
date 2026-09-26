---
type: log
title: 'Cycle 2026-09-27: which MCP revision is current'
summary: MCP 2026-07-28 is current and deprecates DCR, but deprecation is not removal, so the server keeps DCR alongside CIMD.
tags:
- mcp
related: []
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
question: Which MCP specification revision is current, and does it still expect remote servers to support Dynamic Client Registration?
cycle_class: A
date: 2026-09-27
next: Which MCP clients register only through Client ID Metadata Documents, so DCR could be switched off?
output:
- '[[mcp-latest-revision-2026-07-28]]'
- '[[mcp-latest-revision-2025-11-25]]'
- '[[mcp-latest-revision-2025-06-18]]'
search:
- '[[mcp-spec-revisions-research-2026-09-27]]'
---

The evidence forbids calling DCR removed: 2026-07-28 deprecates it with a minimum twelve-month window, so a server that drops it now breaks clients for no gain.
