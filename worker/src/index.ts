/**
 * Worker entrypoint. Two lanes onto the same MCP server.
 *
 * `/mcp` is OAuth-protected. That is the lane claude.ai needs — a static header
 * there is beta and org-limited — and the only lane ChatGPT accepts at all.
 *
 * `/cli/mcp` takes a static bearer token. That is the lane Claude Code (via a
 * project .mcp.json) and Codex CLI (via ~/.codex/config.toml) use, because both
 * accept a header and neither should need a browser round trip.
 *
 * It must not merely differ from `apiRoute` — it must not START WITH it.
 * OAuthProvider matches api routes by PREFIX, so `/mcp-token` was swallowed by
 * `/mcp` and answered 401 for a token that was perfectly valid.
 *
 * Stateless by construction — MCP 2026-07-28 removed protocol sessions, so
 * `createMcpHandler` builds a fresh server per request and no Durable Object
 * sits in the read path.
 */
import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { createMcpHandler } from "agents/mcp/server";

import { handleAuthorize, type AuthEnv } from "./auth";
import { createServer } from "./mcp";
import { handleReindex } from "./index-update";
import { sweepBranches } from "./propose";
import type { Env } from "./search";
import type { WriteEnv } from "./write";

type FullEnv = Env & AuthEnv & WriteEnv & {
  /** Exact public URL of `/mcp`. Empty means: derive it from the request origin. */
  PUBLIC_MCP_URL?: string;
  RESOURCE_NAME?: string;
};

/**
 * The URL a client is told to connect to. The PRM `resource` must match it
 * exactly, character for character, or Claude refuses the connection. Pin it
 * with PUBLIC_MCP_URL when the Worker is reachable under more than one host.
 */
function resourceUrl(request: Request, env: FullEnv): string {
  return env.PUBLIC_MCP_URL || new URL("/mcp", request.url).toString();
}

/**
 * Constant-time compare. A plain === leaks the shared secret one byte at a time
 * to anyone who can measure response latency.
 */
function tokenMatches(given: string, expected: string): boolean {
  if (given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

/** Everything that is not the OAuth-protected MCP route. */
const defaultHandler = {
  async fetch(request: Request, env: FullEnv, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/authorize") {
      return handleAuthorize(request, env);
    }

    if (url.pathname === "/health") {
      const [pageRow, metaRows] = await Promise.all([
        env.DB.prepare("SELECT count(*) AS pages FROM pages").first<{ pages: number }>(),
        env.DB.prepare(`SELECT k,v FROM meta WHERE k IN
          ('head_sha','index_state','index_epoch','index_run_id','index_target_sha','index_mode',
           'index_base_sha','index_lease_until','index_next_batch','index_statements_done',
           'index_expected_batches','index_expected_statements')`).all<{ k: string; v: string }>(),
      ]);
      const meta = Object.fromEntries((metaRows.results ?? []).map((r) => [r.k, r.v]));
      return Response.json({ ok: true, pages: pageRow?.pages ?? 0, ...meta,
        head_sha: meta.head_sha ?? null, index_state: meta.index_state ?? "ready" });
    }

    /*
     * Reindexing.
     *
     * CI cannot reach D1 with wrangler, because minting a scoped Cloudflare API
     * token needs permissions this deployment's OAuth grant does not have. But
     * the Worker already holds a D1 binding, so CI posts the generated SQL here
     * instead and only needs one shared secret — which is a smaller blast
     * radius than a Cloudflare token sitting in GitHub anyway.
     *
     * A batch operation carries an array of statements rather than SQL text. D1's `exec`
     * is line-oriented and would choke on a multi-line CREATE, and splitting
     * SQL text on semicolons is not an option here because page bodies contain
     * them inside string literals. An explicit list has neither problem.
     */
    if (url.pathname === "/admin/reindex" && request.method === "POST") {
      return handleReindex(request, env);
    }

    // The bearer lane. Same MCP server, different door.
    if (url.pathname === "/cli/mcp") {
      const auth = request.headers.get("authorization") ?? "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      if (!env.MCP_TOKEN || !token || !tokenMatches(token, env.MCP_TOKEN)) {
        return new Response(
          JSON.stringify({ error: "unauthorized", detail: "Send Authorization: Bearer <token>." }),
          { status: 401, headers: { "content-type": "application/json" } },
        );
      }
      // Built per request so the factory closes over this request's env. A
      // module-level handler would need env in a global, and an isolate serves
      // concurrent requests, so that global would race.
      const handler = createMcpHandler(
        () => createServer(env, { canWrite: true, author: "agent" }),
        { route: "/cli/mcp" },
      );
      return handler(request, env, ctx);
    }

    return new Response(
      "wiki-mcp server.\n" +
      "  /mcp      OAuth (claude.ai, ChatGPT)\n" +
      "  /cli/mcp  bearer token (Claude Code, Codex CLI)\n",
      { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
} satisfies ExportedHandler<FullEnv>;

function createOAuth(resource: string, resourceName: string) {
  return new OAuthProvider({
    apiRoute: "/mcp",
    apiHandler: {
      fetch(request: Request, env: FullEnv, ctx: ExecutionContext) {
        // props come from the grant completed in auth.ts.
        const props = (ctx as unknown as { props?: {
          canWrite?: boolean; authVersion?: number; grantedScopes?: string[];
        } }).props;
        const canWrite = props?.authVersion === 2 && props.canWrite === true &&
          props.grantedScopes?.includes("wiki.write") === true;
        return createMcpHandler(
          () => createServer(env, { canWrite, author: "agent" }),
          { route: "/mcp" },
        )(request, env, ctx);
      },
    } satisfies ExportedHandler<FullEnv>,
    defaultHandler,
    authorizeEndpoint: "/authorize",
    tokenEndpoint: "/token",
    clientRegistrationEndpoint: "/register",
    // Claude prefers Client ID Metadata Documents and falls back to dynamic
    // registration; RFC 7591 DCR is deprecated in newer spec revisions but still
    // what several clients use, so both stay enabled.
    clientIdMetadataDocumentEnabled: true,
    scopesSupported: ["wiki.read", "wiki.write", "offline_access"],
    resourceMetadata: {
      // Must equal the URL typed into the client, character for character, or
      // Claude refuses the connection.
      resource,
      resource_name: resourceName,
      scopes_supported: ["wiki.read", "wiki.write"],
    },
  });
}

/** One provider per resource URL; in practice one per deployment. */
const providers = new Map<string, OAuthProvider>();
function oauthFor(request: Request, env: FullEnv): OAuthProvider {
  const resource = resourceUrl(request, env);
  let provider = providers.get(resource);
  if (!provider) {
    provider = createOAuth(resource, env.RESOURCE_NAME || "Agent-maintained wiki");
    providers.set(resource, provider);
  }
  return provider;
}

export default {
  fetch: (request: Request, env: FullEnv, ctx: ExecutionContext) =>
    oauthFor(request, env).fetch(request, env, ctx),

  // A failed proposal keeps its branch so it can be read. Without this they
  // accumulate forever.
  async scheduled(_event: ScheduledController, env: FullEnv, _ctx: ExecutionContext) {
    const removed = await sweepBranches(env, 7);
    if (removed) console.log(`swept ${removed} abandoned agent branches`);
  },
} satisfies ExportedHandler<FullEnv>;
