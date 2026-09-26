<!-- generated: build.py — do not edit by hand.
     Regenerate with: python tools/build.py -->

# Wiki Index

One line per page, from its `summary` frontmatter. Superseded pages are
listed with a marker — they are kept for history, not as current answers.

## Concepts (4)

- [[model-context-protocol]] — MCP is an open JSON-RPC protocol that lets AI clients call tools and read resources on external servers; this wiki is exposed to agents as a remote MCP server.
- [[oauth-2-1-for-remote-mcp]] — A remote MCP server is an OAuth 2.1 resource server — clients discover the authorization server via RFC 9728 metadata, register (CIMD or DCR), and use PKCE with RFC 8707 resource indicators.
- [[proposal-branches-and-ci-gate]] — Agents never push to main — propose_cycle validates a whole cycle, commits it to an agent/** branch, and the server fast-forwards main only after validate.yml passes on that exact commit.
- [[sqlite-fts5-bm25-search]] — Search here is SQLite FTS5 over heading-sized chunks, ranked by field-weighted bm25(), with a symbols column so versioned identifiers like oauth-2.1 rank as one rare token.

## Findings (1)

- [[mcp-latest-revision-2025-06-18]] — As of 2025-06-18 the newest MCP revision classified servers as OAuth resource servers, required RFC 8707 resource indicators and removed JSON-RPC batching. ⏴superseded
- [[mcp-latest-revision-2025-11-25]] — As of 2025-11-25 the newest MCP revision recommended Client ID Metadata Documents for client registration and added experimental tasks and OIDC discovery. ⏴superseded
- [[mcp-latest-revision-2026-07-28]] — MCP 2026-07-28 is the current revision — stateless (no initialize handshake, no session header), server/discover added, Dynamic Client Registration deprecated in favour of CIMD.

## Methodology (2)

- [[research-cycle-idea-to-thesis]] — The loop every agent runs — one decision-changing question per cycle, explicit stop rules, promote only what survives analysis, always leave a next step.
- [[retrieval-evaluation-golden-set]] — How search quality is measured here — a hand-verified golden set of questions scored by recall@5, MRR and staleness rate, run in CI so ranking changes move numbers, not opinions.

## Sources (1)

- [[mcp-specification-changelogs]] — The official MCP versioning page names the current revision; each revision's Key Changes page lists what changed since the previous one.

## Research trails (1)

- [[mcp-spec-revisions-research-2026-09-27]] — Trail for which MCP revision is current and what its authorization changes mean for a remote server — official versioning page and three changelogs read.
