<!-- generated: build.py — do not edit by hand.
     Regenerate with: python tools/build.py -->

# Research Log

One bullet per cycle, newest first. Source files: `wiki/log/`.

## 2026-09-27

- **Cycle:** Which MCP specification revision is current, and does it still expect remote servers to support Dynamic Client Registration? Class A. Search: [[mcp-spec-revisions-research-2026-09-27]]. Analysis: The evidence forbids calling DCR removed: 2026-07-28 deprecates it with a minimum twelve-month window, so a server that drops it now breaks clients for no gain. Output: [[mcp-latest-revision-2026-07-28]], [[mcp-latest-revision-2025-11-25]], [[mcp-latest-revision-2025-06-18]]. Next: Which MCP clients register only through Client ID Metadata Documents, so DCR could be switched off?
