/**
 * The Git Data API, which is the only GitHub API that can write a cycle.
 *
 * AGENTS.md says one cycle is one commit, and a cycle is several files: the
 * research trail, its outputs, and exactly one log entry. The Contents API
 * makes one commit per file, so a cycle written through it would land as three
 * or four commits and could half-land. Blobs, then a tree, then one commit,
 * then move the ref — that is atomic and it is what this does.
 */

export interface GitHubEnv {
  GITHUB_TOKEN: string;
  GITHUB_REPO: string; // "owner/name"
}

export interface FileToWrite {
  path: string;
  content: string;
}

const API = "https://api.github.com";

export class GitHubError extends Error {
  constructor(message: string, readonly status: number, readonly body: string) {
    super(message);
  }
}

async function gh<T>(env: GitHubEnv, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "wiki-mcp",
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  const text = await res.text();
  if (!res.ok) {
    throw new GitHubError(`${init.method ?? "GET"} ${path} -> ${res.status}`, res.status, text.slice(0, 400));
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export async function headSha(env: GitHubEnv, branch = "main"): Promise<string> {
  const ref = await gh<{ object: { sha: string } }>(env, `/repos/${env.GITHUB_REPO}/git/ref/heads/${branch}`);
  return ref.object.sha;
}

export async function commitParentSha(env: GitHubEnv, sha: string): Promise<string> {
  const commit = await gh<{ parents: { sha: string }[] }>(env, `/repos/${env.GITHUB_REPO}/git/commits/${sha}`);
  if (commit.parents.length !== 1) {
    throw new GitHubError("proposal commit must have exactly one parent", 409, `found ${commit.parents.length}`);
  }
  return commit.parents[0].sha;
}

/** A publish normalization commit may already have advanced main after our merge. */
export async function containsCommit(env: GitHubEnv, ancestor: string, head: string): Promise<boolean> {
  if (ancestor === head) return true;
  const result=await gh<{status:string}>(env,`/repos/${env.GITHUB_REPO}/compare/${ancestor}...${head}`);
  return result.status === 'ahead' || result.status === 'identical';
}

/** Blobs, tree, commit, ref — one commit containing every file. */
export async function commitFiles(
  env: GitHubEnv,
  opts: { branch: string; parent: string; message: string; files: FileToWrite[] },
): Promise<string> {
  const repo = env.GITHUB_REPO;
  const parentCommit = await gh<{ tree: { sha: string } }>(env, `/repos/${repo}/git/commits/${opts.parent}`);

  const blobs = await Promise.all(opts.files.map(async (f) => {
    const blob = await gh<{ sha: string }>(env, `/repos/${repo}/git/blobs`, {
      method: "POST",
      body: JSON.stringify({ content: f.content, encoding: "utf-8" }),
    });
    return { path: f.path, mode: "100644", type: "blob", sha: blob.sha };
  }));

  const tree = await gh<{ sha: string }>(env, `/repos/${repo}/git/trees`, {
    method: "POST",
    body: JSON.stringify({ base_tree: parentCommit.tree.sha, tree: blobs }),
  });

  const commit = await gh<{ sha: string }>(env, `/repos/${repo}/git/commits`, {
    method: "POST",
    body: JSON.stringify({ message: opts.message, tree: tree.sha, parents: [opts.parent] }),
  });

  await gh(env, `/repos/${repo}/git/refs`, {
    method: "POST",
    body: JSON.stringify({ ref: `refs/heads/${opts.branch}`, sha: commit.sha }),
  });

  return commit.sha;
}

export interface CheckState {
  status: "queued" | "in_progress" | "completed" | "none";
  conclusion: string | null;
  url: string | null;
}

/** The state of CI for one commit. */
export async function checkState(env: GitHubEnv, sha: string, branch: string): Promise<CheckState> {
  const res = await gh<{
    workflow_runs: {
      head_sha: string; head_branch: string; status: string;
      conclusion: string | null; html_url: string; path: string;
    }[];
  }>(env, `/repos/${env.GITHUB_REPO}/actions/workflows/validate.yml/runs?` +
          `head_sha=${encodeURIComponent(sha)}&branch=${encodeURIComponent(branch)}&event=push&per_page=20`);

  // Only validate.yml for this exact commit and branch can authorize a merge.
  // An unrelated successful check on the same repository is not sufficient.
  const runs = (res.workflow_runs ?? []).filter((r) =>
    r.head_sha === sha && r.head_branch === branch &&
    r.path.replace(/^\//, "") === ".github/workflows/validate.yml");
  if (runs.length === 0) return { status: "none", conclusion: null, url: null };

  const failed = runs.find((r) => r.status === "completed" && r.conclusion !== "success");
  if (failed) return { status: "completed", conclusion: failed.conclusion, url: failed.html_url };
  if (runs.some((r) => r.status !== "completed")) {
    return { status: "in_progress", conclusion: null, url: runs[0].html_url };
  }
  const successful = runs.find((r) => r.status === "completed" && r.conclusion === "success");
  return successful
    ? { status: "completed", conclusion: "success", url: successful.html_url }
    : { status: "completed", conclusion: "failure", url: runs[0].html_url };
}

/** Annotations are where lint.py's actual messages surface. */
export async function failureDetail(env: GitHubEnv, sha: string): Promise<string[]> {
  try {
    const res = await gh<{ check_runs: {
      id: number; conclusion: string | null; name: string; head_sha: string;
      app: { slug: string } | null;
    }[] }>(
      env, `/repos/${env.GITHUB_REPO}/commits/${sha}/check-runs`);
    const bad = (res.check_runs ?? []).filter((r) =>
      r.head_sha === sha && r.name === "check" && r.app?.slug === "github-actions" &&
      r.conclusion === "failure");
    const out: string[] = [];
    for (const run of bad.slice(0, 2)) {
      const anns = await gh<{ message: string; path: string }[]>(
        env, `/repos/${env.GITHUB_REPO}/check-runs/${run.id}/annotations`);
      out.push(...anns.slice(0, 20).map((a) => `${a.path}: ${a.message}`));
    }
    return out;
  } catch {
    return [];
  }
}

/**
 * Merge a branch into main.
 *
 * Deliberately performed by the Worker rather than by CI. A push made with CI's
 * own GITHUB_TOKEN does not trigger further workflows, so a CI-side merge would
 * mean publish.yml never ran for agent writes and D1 would quietly stop
 * matching main. The Worker's token is a different identity, so its push does
 * trigger publish.yml — and publish.yml's own commit, made with GITHUB_TOKEN,
 * does not re-trigger it. The loop breaks itself.
 */
export async function fastForwardBranch(
  env: GitHubEnv, branch: string, expectedBase: string, expectedHead: string,
): Promise<string> {
  const [main, proposed] = await Promise.all([headSha(env, "main"), headSha(env, branch)]);
  if (main !== expectedBase) {
    throw new GitHubError("main changed after the proposal was validated", 409, `expected ${expectedBase}, got ${main}`);
  }
  if (proposed !== expectedHead) {
    throw new GitHubError("proposal branch changed after validation", 409, `expected ${expectedHead}, got ${proposed}`);
  }
  // A non-forced ref update is atomic on GitHub. If main advances between the
  // reads above and this request, the proposal is no longer a fast-forward and
  // GitHub rejects it rather than silently merging against unvalidated state.
  const res = await gh<{ object: { sha: string } }>(env, `/repos/${env.GITHUB_REPO}/git/refs/heads/main`, {
    method: "PATCH",
    body: JSON.stringify({ sha: expectedHead, force: false }),
  });
  if (res.object.sha !== expectedHead) {
    throw new GitHubError("main did not advance to the validated commit", 409, JSON.stringify(res).slice(0, 400));
  }
  return res.object.sha;
}

export async function deleteBranch(env: GitHubEnv, branch: string): Promise<void> {
  try {
    await gh(env, `/repos/${env.GITHUB_REPO}/git/refs/heads/${branch}`, { method: "DELETE" });
  } catch (e) {
    if (!(e instanceof GitHubError) || e.status !== 422) throw e;
  }
}

export async function listAgentBranches(env: GitHubEnv): Promise<{ name: string; sha: string }[]> {
  const refs = await gh<{ ref: string; object: { sha: string } }[]>(
    env, `/repos/${env.GITHUB_REPO}/git/matching-refs/heads/agent/`);
  return (refs ?? []).map((r) => ({ name: r.ref.replace("refs/heads/", ""), sha: r.object.sha }));
}

export async function commitDate(env: GitHubEnv, sha: string): Promise<string> {
  const c = await gh<{ committer: { date: string } }>(env, `/repos/${env.GITHUB_REPO}/git/commits/${sha}`);
  return c.committer?.date ?? "";
}
