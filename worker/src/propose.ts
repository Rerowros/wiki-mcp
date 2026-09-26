/**
 * propose_cycle / check_proposal / abandon_proposal.
 *
 * The protocol is asynchronous on purpose. A proposal waits on GitHub Actions,
 * a run can sit queued for minutes, and holding an MCP tool call open that long
 * is exactly the pattern the stateless spec pushes away from. So proposing
 * returns immediately with an id, and the agent polls.
 */
import * as gh from "./github";
import { readSnapshot } from "./read-snapshot";
import {
  acquireLock, claimLock, ensureTables, pagePath, releaseLock, renderPage, todayUtc,
  indexedHeadSha, normalizeCycle, validateCycle, type CycleInput, type WriteEnv,
} from "./write";

export interface ProposeResult {
  status: "rejected" | "queued";
  proposal_id?: string;
  branch?: string;
  commit?: string;
  problems?: { where: string; message: string }[];
  next_step?: string;
}

function cycleFiles(cycle: CycleInput, author: string, today: string) {
  const pages = [cycle.research, ...(cycle.outputs ?? [])];
  const files = pages.map((p) => ({
    path: pagePath(p.type, p.slug),
    content: renderPage(p, author, today),
  }));
  // The log entry is one file per cycle under wiki/log/, never an append to
  // wiki/log.md: two agents editing one shared file lose an entry silently and
  // leave valid-looking markdown behind.
  files.push({
    path: `wiki/log/${today}-${cycle.log.slug.replace(/^\d{4}-\d{2}-\d{2}-/, "")}.md`,
    content: renderPage(cycle.log, author, today),
  });
  return files;
}

export async function proposeCycle(
  env: WriteEnv, cycle: CycleInput, author: string,
): Promise<ProposeResult> {
  await ensureTables(env);

  const problems = await readSnapshot(env, () => validateCycle(env, cycle));
  if (problems.length) {
    return {
      status: "rejected",
      problems,
      next_step: "Fix these and call propose_cycle again. Nothing was written.",
    };
  }

  cycle = normalizeCycle(cycle);

  if (!env.GITHUB_TOKEN) {
    return {
      status: "rejected",
      problems: [{ where: "server", message:
        "GITHUB_TOKEN is not configured on the server, so the validated cycle cannot be written." }],
    };
  }

  const initialMain = await gh.headSha(env, "main");
  const initialIndex = await indexedHeadSha(env);
  if (!initialIndex || initialIndex !== initialMain) {
    return {
      status: "rejected",
      problems: [{ where: "server", message:
        `The search index is not a ready, exact snapshot of main (index=${initialIndex ?? "missing or not ready"}, main=${initialMain}). ` +
        "Writing is paused until reindexing finishes; corpus validation against stale D1 would be unsafe." }],
      next_step: "Retry after the index reports ready at the current main SHA.",
    };
  }

  const id = crypto.randomUUID();
  const today = todayUtc();
  const branch = `agent/${today}-${cycle.log.slug.replace(/^\d{4}-\d{2}-\d{2}-/, "")}`.slice(0, 120);

  if (!(await acquireLock(env, id))) {
    return {
      status: "rejected",
      problems: [{ where: "server", message: "another proposal is being merged; retry in a moment" }],
      next_step: "Wait a few seconds and call propose_cycle again.",
    };
  }

  try {
    // Cut from main *inside* the lock. That is what makes a green branch imply
    // a green main: nothing else can land in between.
    const parent = await gh.headSha(env, "main");
    const indexed = await indexedHeadSha(env);
    if (!indexed || indexed !== parent) {
      await releaseLock(env, id);
      return {
        status: "rejected",
        problems: [{ where: "server", message:
          `main/index changed while the proposal was waiting for its lock (index=${indexed ?? "missing"}, main=${parent}).` }],
        next_step: "Retry after reindexing finishes.",
      };
    }
    const lockedProblems = await readSnapshot(env, () => validateCycle(env, cycle));
    if (lockedProblems.length) {
      await releaseLock(env, id);
      return {
        status: "rejected", problems: lockedProblems,
        next_step: "The corpus changed while validation was running. Fix these and call propose_cycle again.",
      };
    }
    const files = cycleFiles(cycle, author, today);
    const message =
      `${cycle.log.title || cycle.question}\n\n` +
      `Question: ${cycle.question}\n` +
      `Written by ${author} through the MCP write path.\n`;
    const sha = await gh.commitFiles(env, { branch, parent, message, files });

    await env.DB.prepare(
      `INSERT INTO proposals (id, branch, sha, status, created_at, updated_at, summary)
       VALUES (?, ?, ?, 'queued', ?, ?, ?)`
    ).bind(id, branch, sha, new Date().toISOString(), new Date().toISOString(),
           cycle.question.slice(0, 200)).run();

    return {
      status: "queued",
      proposal_id: id,
      branch,
      commit: sha,
      next_step:
        "Validation runs in CI. Call check_proposal with this proposal_id — usually ready " +
        "within a minute. It merges to main on green and returns the lint errors on red.",
    };
  } catch (e) {
    await releaseLock(env, id);
    const detail = e instanceof gh.GitHubError ? `${e.message}: ${e.body}` : String(e);
    return { status: "rejected", problems: [{ where: "github", message: detail }] };
  }
}

export interface CheckResult {
  status: "queued" | "validating" | "failed" | "merged" | "abandoned" | "unknown";
  branch?: string;
  errors?: string[];
  merged_sha?: string;
  ci_url?: string;
  next_step?: string;
}

export async function checkProposal(env: WriteEnv, id: string): Promise<CheckResult> {
  await ensureTables(env);
  const row = await env.DB.prepare("SELECT * FROM proposals WHERE id = ?").bind(id)
    .first<{ id: string; branch: string; sha: string; status: string; errors: string | null; merged_sha: string | null }>();
  if (!row) return { status: "unknown", next_step: "No proposal with that id." };
  if (row.status === "abandoned") return { status: "abandoned", branch: row.branch };

  if (row.status === "merged") {
    return { status: "merged", branch: row.branch, merged_sha: row.merged_sha ?? undefined };
  }
  if (row.status === "failed") {
    return {
      status: "failed", branch: row.branch,
      errors: row.errors ? JSON.parse(row.errors) : [],
      next_step: "Fix the pages and call propose_cycle again with new slugs, or abandon_proposal to drop the branch.",
    };
  }

  const ci = await gh.checkState(env, row.sha, row.branch);
  if (ci.status === "none" || ci.status === "queued") {
    return { status: "queued", branch: row.branch, ci_url: ci.url ?? undefined,
             next_step: "CI has not started yet. Check again shortly." };
  }
  if (ci.status === "in_progress") {
    return { status: "validating", branch: row.branch, ci_url: ci.url ?? undefined,
             next_step: "CI is running. Check again shortly." };
  }

  if (ci.conclusion !== "success") {
    const errors = await gh.failureDetail(env, row.sha);
    await env.DB.prepare("UPDATE proposals SET status='failed', errors=?, updated_at=? WHERE id=?")
      .bind(JSON.stringify(errors), new Date().toISOString(), id).run();
    await releaseLock(env, id);
    return {
      status: "failed", branch: row.branch, ci_url: ci.url ?? undefined,
      errors: errors.length ? errors : [`CI concluded ${ci.conclusion}; see ${ci.url}`],
      next_step: "The branch is kept so you can read it. Fix and propose again.",
    };
  }

  const mergeHolder = `${id}:${crypto.randomUUID()}`;
  if (!(await claimLock(env, id, mergeHolder))) {
    return { status: "validating", branch: row.branch, ci_url: ci.url ?? undefined,
             next_step: "CI passed, but another proposal owns the merge lock. Check again shortly." };
  }

  let merged: string;
  try {
    const fresh = await env.DB.prepare("SELECT status,merged_sha FROM proposals WHERE id=?").bind(id)
      .first<{status:string;merged_sha:string|null}>();
    if (fresh?.status === 'abandoned') return {status:'abandoned',branch:row.branch};
    if (fresh?.status === 'merged') return {status:'merged',branch:row.branch,merged_sha:fresh.merged_sha ?? row.sha};
    const parent = await gh.commitParentSha(env, row.sha);
    const indexed = await indexedHeadSha(env);
    const currentMain = await gh.headSha(env, "main");
    // GitHub may have accepted the atomic ref update while the following D1
    // status write failed. Reconcile that partial response before treating the
    // proposal as stale; this also makes check_proposal safe to retry.
    if (currentMain === row.sha || (currentMain !== parent && await gh.containsCommit(env,row.sha,currentMain))) {
      merged = row.sha;
      await env.DB.prepare("UPDATE proposals SET status='merged', merged_sha=?, updated_at=? WHERE id=?")
        .bind(merged, new Date().toISOString(), id).run();
      try { await gh.deleteBranch(env, row.branch); } catch { /* scheduled sweep can clean it */ }
      return { status: "merged", branch: row.branch, merged_sha: merged };
    }
    if (!indexed || indexed !== parent || currentMain !== parent) {
      const errors = [
        `main changed after validation (proposal base=${parent}, main=${currentMain}, index=${indexed ?? "missing"}); ` +
        "the proposal was not merged and must be submitted again against the current corpus.",
      ];
      await env.DB.prepare("UPDATE proposals SET status='failed', errors=?, updated_at=? WHERE id=?")
        .bind(JSON.stringify(errors), new Date().toISOString(), id).run();
      return { status: "failed", branch: row.branch, errors,
               next_step: "Submit a new proposal so corpus checks run against the current main and index." };
    }
    merged = await gh.fastForwardBranch(env, row.branch, parent, row.sha);
    // Once main moved, that is the durable outcome. Record it before branch
    // cleanup so a cleanup permission/network error cannot turn a merge into a
    // false failure on the next poll.
    await env.DB.prepare("UPDATE proposals SET status='merged', merged_sha=?, updated_at=? WHERE id=?")
      .bind(merged, new Date().toISOString(), id).run();
    try { await gh.deleteBranch(env, row.branch); } catch { /* best effort; scheduled sweep retries */ }
  } catch (e) {
    // A timeout can hide a successful GitHub ref update. Confirm main before
    // recording failure; otherwise a retry would report a real merge as lost.
    try {
      const confirmedMain=await gh.headSha(env, "main");
      if (confirmedMain === row.sha || await gh.containsCommit(env,row.sha,confirmedMain)) {
        try {
          await env.DB.prepare("UPDATE proposals SET status='merged', merged_sha=?, updated_at=? WHERE id=?")
            .bind(row.sha, new Date().toISOString(), id).run();
          try { await gh.deleteBranch(env, row.branch); } catch { /* best effort */ }
          return { status: "merged", branch: row.branch, merged_sha: row.sha };
        } catch {
          return { status: "validating", branch: row.branch,
                   next_step: "main contains the proposal, but its local status could not be recorded; check again shortly." };
        }
      }
    } catch {
      return {status:'validating',branch:row.branch,
        next_step:'GitHub outcome could not be confirmed. Retry check_proposal; do not create a replacement yet.'};
    }
    if (!(e instanceof gh.GitHubError) || ![409,422].includes(e.status)) {
      return {status:'validating',branch:row.branch,
        next_step:'Merge outcome is uncertain or temporarily unavailable. Retry check_proposal before submitting another cycle.'};
    }
    const detail = e instanceof gh.GitHubError ? `${e.message}: ${e.body}` : String(e);
    const errors = [`Merge refused: ${detail}`];
    await env.DB.prepare("UPDATE proposals SET status='failed', errors=?, updated_at=? WHERE id=?")
      .bind(JSON.stringify(errors), new Date().toISOString(), id).run();
    return { status: "failed", branch: row.branch, errors,
             next_step: "Submit a new proposal against the current main." };
  } finally {
    await releaseLock(env, mergeHolder);
  }

  return {
    status: "merged", branch: row.branch, merged_sha: merged,
    next_step:
      "Merged to main. The search index rebuilds from that push, so the new pages become " +
      "searchable within about a minute.",
  };
}

export async function abandonProposal(env: WriteEnv, id: string): Promise<{ status: string }> {
  await ensureTables(env);
  const row = await env.DB.prepare("SELECT branch, status FROM proposals WHERE id = ?").bind(id)
    .first<{ branch: string; status: string }>();
  if (!row) return { status: "unknown" };
  if (row.status === 'merged' || row.status === 'abandoned') return {status:row.status};
  const holder = `${id}:abandon:${crypto.randomUUID()}`;
  if (!(await claimLock(env,id,holder))) return {status:'busy'};
  try {
    const fresh=await env.DB.prepare("SELECT status FROM proposals WHERE id=?").bind(id).first<{status:string}>();
    if (fresh?.status === 'merged') return {status:'merged'};
    await env.DB.prepare("UPDATE proposals SET status='abandoned', updated_at=? WHERE id=?")
      .bind(new Date().toISOString(), id).run();
    try { await gh.deleteBranch(env,row.branch); } catch { /* sweep retries cleanup */ }
    return {status:'abandoned'};
  } finally { await releaseLock(env,holder); }
}

/**
 * Delete agent branches nobody merged. A failed proposal keeps its branch so it
 * can be read; without this they accumulate forever.
 */
export async function sweepBranches(env: WriteEnv, maxAgeDays = 7): Promise<number> {
  if (!env.GITHUB_TOKEN) return 0;
  const cutoff = Date.now() - maxAgeDays * 86400_000;
  let removed = 0;
  for (const branch of await gh.listAgentBranches(env)) {
    const date = await gh.commitDate(env, branch.sha);
    if (date && Date.parse(date) < cutoff) {
      await gh.deleteBranch(env, branch.name);
      removed++;
    }
  }
  return removed;
}
