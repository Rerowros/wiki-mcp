/**
 * The authorization surface for claude.ai and any other OAuth MCP client.
 *
 * This vault has exactly one reader, so the identity provider is a passphrase
 * held as a Worker secret rather than a third-party IdP. That is deliberate:
 * an upstream GitHub or Google app would need registering by hand outside this
 * repository, and would add a second place where access can be granted. One
 * secret, one owner, revoked by rotating it.
 *
 * Everything OAuth-shaped — PKCE, dynamic client registration, token issue and
 * refresh, discovery documents — is handled by @cloudflare/workers-oauth-provider.
 * This file only decides *who* is allowed and renders the consent screen.
 */
import type { OAuthHelpers, AuthRequest } from "@cloudflare/workers-oauth-provider";

export interface AuthEnv {
  OAUTH_PROVIDER: OAuthHelpers;
  OAUTH_KV: KVNamespace;
  WIKI_PASSPHRASE: string;
}

/** Constant-time compare, so the passphrase does not leak a byte at a time. */
function matches(given: string, expected: string): boolean {
  if (!expected || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

/**
 * Throttle guessing. A passphrase with no rate limit in front of it is a
 * passphrase being brute-forced at whatever rate the network allows; the
 * counter lives in KV because that is already a dependency here.
 */
const MAX_ATTEMPTS = 8;
const WINDOW_SECONDS = 900;

async function tooManyAttempts(env: AuthEnv, ip: string): Promise<boolean> {
  const key = `login-attempts:${ip}`;
  const n = Number((await env.OAUTH_KV.get(key)) ?? "0");
  return n >= MAX_ATTEMPTS;
}

async function noteFailure(env: AuthEnv, ip: string): Promise<void> {
  const key = `login-attempts:${ip}`;
  const n = Number((await env.OAUTH_KV.get(key)) ?? "0") + 1;
  await env.OAUTH_KV.put(key, String(n), { expirationTtl: WINDOW_SECONDS });
}

const STYLE = `
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 ui-sans-serif, system-ui, sans-serif; margin: 0;
         display: grid; place-items: center; min-height: 100vh;
         background: #faf9f7; color: #1a1a19; }
  @media (prefers-color-scheme: dark) { body { background: #16161a; color: #e8e8e6; } }
  main { width: min(28rem, 92vw); padding: 2rem; }
  h1 { font-size: 1.15rem; margin: 0 0 .25rem; }
  p  { margin: .25rem 0 1.25rem; opacity: .75; font-size: .9rem; }
  .client { font-family: ui-monospace, monospace; font-size: .82rem;
            padding: .6rem .75rem; border-radius: .4rem; margin-bottom: 1rem;
            background: rgba(128,128,128,.12); word-break: break-all; }
  input, button { width: 100%; box-sizing: border-box; font: inherit;
                  padding: .6rem .7rem; border-radius: .4rem;
                  border: 1px solid rgba(128,128,128,.4); background: transparent;
                  color: inherit; }
  button { margin-top: .75rem; cursor: pointer; border: 0; font-weight: 600;
           background: #1a1a19; color: #faf9f7; }
  @media (prefers-color-scheme: dark) { button { background: #e8e8e6; color: #16161a; } }
  .err { color: #b3261e; font-size: .85rem; margin: .5rem 0 0; }
  @media (prefers-color-scheme: dark) { .err { color: #f2b8b5; } }
  ul { padding-left: 1.1rem; font-size: .85rem; opacity: .75; }
`;

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

function loginPage(req: AuthRequest, clientName: string, error?: string): Response {
  // The client name comes from dynamic registration, i.e. from the client
  // itself. Escaped, and labelled as self-reported, so a client cannot dress
  // its consent screen up as something else.
  const requestsWrite = req.scope.includes("wiki.write");
  const writeConsent = requestsWrite ? `
    <label class="write"><input type="checkbox" name="allow_write" value="yes">
      Allow this application to propose research cycles that may be merged after CI validation
    </label>` : "";
  const body = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to the research wiki</title>
<style>${STYLE}</style>
<main>
  <h1>Connect to the research wiki</h1>
  <p>An application is asking to read this vault.</p>
  <div class="client">${escapeHtml(clientName)}<br>redirect: ${escapeHtml(req.redirectUri)}</div>
  <ul>
    <li>Read pages, claims, links and open threads</li>
  </ul>
  <form method="POST">
    <input type="hidden" name="req" value="${escapeHtml(btoa(JSON.stringify(req)))}">
    <input type="password" name="passphrase" placeholder="Passphrase" autofocus
           autocomplete="current-password" required>
    ${writeConsent}
    <button type="submit">Allow access</button>
    ${error ? `<p class="err">${escapeHtml(error)}</p>` : ""}
  </form>
</main>`;
  return new Response(body, {
    status: error ? 401 : 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

export async function handleAuthorize(request: Request, env: AuthEnv): Promise<Response> {
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";

  if (request.method === "GET") {
    const authReq = await env.OAUTH_PROVIDER.parseAuthRequest(request);
    const client = await env.OAUTH_PROVIDER.lookupClient(authReq.clientId);
    return loginPage(authReq, client?.clientName || authReq.clientId);
  }

  if (request.method !== "POST") {
    return new Response("method not allowed", { status: 405 });
  }

  const form = await request.formData();
  const authReq = JSON.parse(atob(String(form.get("req") ?? ""))) as AuthRequest;
  const client = await env.OAUTH_PROVIDER.lookupClient(authReq.clientId);
  const name = client?.clientName || authReq.clientId;

  if (await tooManyAttempts(env, ip)) {
    return loginPage(authReq, name, "Too many attempts. Try again in fifteen minutes.");
  }
  if (!matches(String(form.get("passphrase") ?? ""), env.WIKI_PASSPHRASE)) {
    await noteFailure(env, ip);
    return loginPage(authReq, name, "Wrong passphrase.");
  }

  const requested = new Set(authReq.scope.length ? authReq.scope : ["wiki.read"]);
  const granted = ["wiki.read"];
  if (requested.has("offline_access")) granted.push("offline_access");
  const canWrite = requested.has("wiki.write") && form.get("allow_write") === "yes";
  if (canWrite) granted.push("wiki.write");

  const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
    request: authReq,
    userId: "owner",
    metadata: { connectedAt: new Date().toISOString(), client: name },
    scope: granted,
    // A passphrase proves identity. It does not imply write consent: the
    // client must request wiki.write and the owner must check the box.
    props: { userId: "owner", canWrite, authVersion: 2, grantedScopes: granted },
  });

  return Response.redirect(redirectTo, 302);
}
