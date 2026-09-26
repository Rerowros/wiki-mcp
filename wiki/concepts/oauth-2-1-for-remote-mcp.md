---
type: concept
title: OAuth 2.1 for remote MCP servers
summary: A remote MCP server is an OAuth 2.1 resource server — clients discover the authorization server via RFC 9728 metadata, register (CIMD or DCR), and use PKCE with RFC 8707 resource indicators.
tags:
- oauth
- mcp
- security
related:
- '[[model-context-protocol]]'
- '[[mcp-latest-revision-2026-07-28]]'
created: 2026-09-27
updated: 2026-09-27
author: claude
status: current
---

# OAuth 2.1 for remote MCP servers

## What it is

OAuth 2.1 is a consolidation of OAuth 2.0 and its security best practices: the
implicit grant and the password grant are gone, **PKCE is required** for the
authorization-code flow, and redirect URIs are matched exactly.

MCP's authorization spec builds on it. Since revision 2025-06-18 an MCP server
is classified as an **OAuth resource server**. The pieces, in the order a client
meets them:

1. **401 + discovery.** An unauthenticated request to the MCP endpoint gets
   `401` with a `WWW-Authenticate` header pointing at the server's
   **Protected Resource Metadata** (RFC 9728, `/.well-known/oauth-protected-resource`).
   That document names the `resource` identifier and its authorization servers.
2. **Authorization server metadata** (RFC 8414) lists the authorize, token and
   registration endpoints and the supported PKCE methods (`S256`).
3. **Client registration.** Either **Client ID Metadata Documents** (CIMD — the
   client ID *is* an HTTPS URL of a JSON document describing the client) or
   **Dynamic Client Registration** (RFC 7591). The 2026-07-28 revision
   deprecates DCR in favour of CIMD, keeping it for backwards compatibility.
4. **Authorization request** with `code_challenge` (PKCE S256), `state`, the
   requested `scope`, and a `resource` parameter (RFC 8707 resource indicator)
   naming the MCP server, so the token is audience-bound and a malicious server
   cannot replay it elsewhere.
5. **Token exchange** with the `code_verifier`; refresh tokens when
   `offline_access` is granted.

## What it is not

- Not authentication of a person by itself. Who may approve a grant is the
  server's decision; this template uses a single owner passphrase on the consent
  page, rate-limited in KV.
- Not the only lane. CLI clients that can send a static header use a bearer
  token on a separate route (`/cli/mcp`), which avoids a browser round trip.

## Why it matters here

The Worker uses `@cloudflare/workers-oauth-provider` for every protocol step
(PKCE, CIMD and DCR, token issue and refresh, discovery documents). The
project-specific code only renders consent and decides scopes:

- `wiki.read` is granted on a correct passphrase.
- `wiki.write` is granted only if the client **requested** it **and** the owner
  ticked the write checkbox. A passphrase proves identity; it does not imply
  write consent.

The `resource` in the protected resource metadata must equal the URL typed into
the client character for character — a trailing slash is a different resource.
`PUBLIC_MCP_URL` in `wrangler.jsonc` pins it; `tools/test_oauth.py` walks the
full flow, including the refusals.
