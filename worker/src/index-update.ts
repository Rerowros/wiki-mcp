import type { Env } from "./search";

type ReindexEnv = Pick<Env, "DB"> & { ADMIN_TOKEN?: string };

const LEASE_MS = 15 * 60 * 1000;
const BATCH_SIZE = 32; // Leaves room for protocol queries within the Free invocation limit.
const META_KEYS = [
  "index_run_id", "index_target_sha", "index_mode", "index_base_sha",
  "index_lease_until", "index_next_batch", "index_statements_done",
  "index_expected_batches", "index_expected_statements",
];

function tokenMatches(given: string, expected: string): boolean {
  if (given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

/**
 * Add an assertion to a D1 batch.  A scalar subquery returns NULL when its
 * predicate does not match; the NOT NULL guard then aborts and rolls back the
 * whole batch.  This makes ownership checks atomic with the SQL mutations.
 */
function guard(env: ReindexEnv, predicate: string, binds: unknown[]) {
  return env.DB.prepare(
    `INSERT INTO index_rebuild_guard(singleton,run_id)
     VALUES (1,(SELECT run_id FROM index_rebuild_lock WHERE singleton=1 AND ${predicate}))`,
  ).bind(...binds);
}

function clearGuard(env: ReindexEnv) {
  return env.DB.prepare("DELETE FROM index_rebuild_guard WHERE singleton=1");
}

function putMeta(env: ReindexEnv, key: string, value: string) {
  return env.DB.prepare(
    "INSERT INTO meta(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
  ).bind(key, value);
}

function deleteMeta(env: ReindexEnv) {
  return META_KEYS.map((key) => env.DB.prepare("DELETE FROM meta WHERE k=?").bind(key));
}

async function markFailed(env: ReindexEnv, runId: string, batchIndex: number): Promise<void> {
  try {
    await env.DB.batch([
      clearGuard(env),
      guard(env, "run_id=? AND next_batch=?", [runId, batchIndex]),
      putMeta(env, "index_state", "failed"),
      clearGuard(env),
    ]);
  } catch {
    // A newer request owns or advanced the run.  Never poison its state.
  }
}

export async function handleReindex(request: Request, env: ReindexEnv): Promise<Response> {
  const auth = request.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!env.ADMIN_TOKEN || !token || !tokenMatches(token, env.ADMIN_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(await request.text());
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("expected an operation object");
    }
  } catch (error) {
    return json({ ok: false, error: String(error).slice(0, 200) }, 400);
  }

  await env.DB.batch([
    env.DB.prepare("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS index_rebuild_lock (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT NOT NULL,
      target_sha TEXT NOT NULL, next_batch INTEGER NOT NULL, statements_done INTEGER NOT NULL,
      expected_batches INTEGER NOT NULL, expected_statements INTEGER NOT NULL, lease_until INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS index_rebuild_guard (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT NOT NULL)`),
  ]);

  const operation = String(payload.operation ?? "");
  const runId = String(payload.run_id ?? "");
  if (!runId) return json({ ok: false, error: "run_id is required" }, 400);
  const now = Date.now();

  try {
    if (operation === "start") {
      const target = String(payload.target_sha ?? "");
      const base = String(payload.base_sha ?? "");
      const mode = String(payload.mode ?? "");
      const batches = Number(payload.expected_batches);
      const statements = Number(payload.expected_statements);
      if (!/^[0-9a-f]{40}$/.test(target) || !["full", "incremental"].includes(mode) ||
          !Number.isSafeInteger(batches) || batches < 0 ||
          !Number.isSafeInteger(statements) || statements < 0 ||
          batches !== (statements === 0 ? 0 : Math.ceil(statements / BATCH_SIZE))) {
        return json({ ok: false, error: "invalid start metadata" }, 400);
      }

      const [current, state, existing] = await Promise.all([
        env.DB.prepare("SELECT v FROM meta WHERE k='head_sha'").first<{ v: string }>(),
        env.DB.prepare("SELECT v FROM meta WHERE k='index_state'").first<{ v: string }>(),
        env.DB.prepare("SELECT run_id,lease_until FROM index_rebuild_lock WHERE singleton=1")
          .first<{ run_id: string; lease_until: number }>(),
      ]);
      if (existing && existing.lease_until >= now) {
        return json({ ok: false, error: "another rebuild holds the lease" }, 409);
      }
      // An interrupted generation may have committed arbitrary early batches.
      // Only a full reset can make that database canonical again.
      if (mode === "incremental" && (existing || (state?.v ?? "ready") !== "ready")) {
        return json({ ok: false, error: "an interrupted rebuild requires a full rebuild" }, 409);
      }
      if (mode === "incremental" && (!current || current.v !== base)) {
        return json({ ok: false, error: "base_sha does not match the published index" }, 409);
      }

      const lease = now + LEASE_MS;
      await env.DB.batch([
        env.DB.prepare("DELETE FROM index_rebuild_lock WHERE singleton=1 AND lease_until < ?").bind(now),
        env.DB.prepare(`INSERT INTO index_rebuild_lock
          (singleton,run_id,target_sha,next_batch,statements_done,expected_batches,expected_statements,lease_until)
          VALUES (1,?,?,0,0,?,?,?)`).bind(runId, target, batches, statements, lease),
        clearGuard(env),
        guard(env, mode === 'incremental'
          ? "run_id=? AND EXISTS(SELECT 1 FROM meta WHERE k='head_sha' AND v=?) AND COALESCE((SELECT v FROM meta WHERE k='index_state'),'ready')='ready'"
          : "run_id=?", mode === 'incremental' ? [runId,base] : [runId]),
        putMeta(env, "index_state", "building"), putMeta(env, "index_run_id", runId),
        putMeta(env, "index_target_sha", target), putMeta(env, "index_mode", mode),
        putMeta(env, "index_base_sha", base), putMeta(env, "index_lease_until", String(lease)),
        putMeta(env, "index_next_batch", "0"), putMeta(env, "index_statements_done", "0"),
        putMeta(env, "index_expected_batches", String(batches)),
        putMeta(env, "index_expected_statements", String(statements)),
        clearGuard(env),
      ]);
      return json({ ok: true, run_id: runId, lease_until: lease });
    }

    if (operation === "heartbeat") {
      const lease = now + LEASE_MS;
      await env.DB.batch([
        clearGuard(env), guard(env, "run_id=? AND lease_until>=?", [runId, now]),
        env.DB.prepare("UPDATE index_rebuild_lock SET lease_until=? WHERE singleton=1").bind(lease),
        putMeta(env, "index_lease_until", String(lease)), clearGuard(env),
      ]);
      return json({ ok: true, run_id: runId, lease_until: lease });
    }

    if (operation === "batch") {
      const batchIndex = Number(payload.batch_index);
      const statements = payload.statements;
      if (!Number.isSafeInteger(batchIndex) || batchIndex < 0 || !Array.isArray(statements) ||
          statements.length === 0 || statements.length > BATCH_SIZE ||
          !statements.every((statement) => typeof statement === "string")) {
        return json({ ok: false, error: "invalid batch metadata or SQL statements" }, 400);
      }
      const lease = now + LEASE_MS;
      try {
        await env.DB.batch([
          clearGuard(env),
          guard(env,
            "run_id=? AND next_batch=? AND lease_until>=? AND next_batch<expected_batches " +
            "AND statements_done+?<=expected_statements AND EXISTS(SELECT 1 FROM meta WHERE k='index_state' AND v='building')",
            [runId, batchIndex, now, statements.length]),
          ...(statements as string[]).map((sql) => env.DB.prepare(sql)),
          env.DB.prepare(`UPDATE index_rebuild_lock SET next_batch=next_batch+1,
            statements_done=statements_done+?, lease_until=? WHERE singleton=1`)
            .bind(statements.length, lease),
          putMeta(env, "index_lease_until", String(lease)),
          putMeta(env, "index_next_batch", String(batchIndex + 1)),
          env.DB.prepare("UPDATE meta SET v=CAST(CAST(v AS INTEGER)+? AS TEXT) WHERE k='index_statements_done'")
            .bind(statements.length),
          clearGuard(env),
        ]);
      } catch (error) {
        await markFailed(env, runId, batchIndex);
        return json({ ok: false, error: String(error).slice(0, 500) }, 409);
      }
      return json({ ok: true, count: statements.length, next_batch: batchIndex + 1 });
    }

    if (operation === "finish") {
      const target = String(payload.target_sha ?? "");
      const batches = Number(payload.expected_batches);
      const statements = Number(payload.expected_statements);
      await env.DB.batch([
        clearGuard(env),
        guard(env,
          "run_id=? AND target_sha=? AND lease_until>=? AND next_batch=expected_batches " +
          "AND statements_done=expected_statements AND expected_batches=? AND expected_statements=? " +
          "AND EXISTS(SELECT 1 FROM meta WHERE k='index_state' AND v='building')",
          [runId, target, now, batches, statements]),
        env.DB.prepare("DELETE FROM index_rebuild_lock WHERE singleton=1"),
        putMeta(env, "head_sha", target), putMeta(env, "built_at", new Date().toISOString()),
        putMeta(env, "index_state", "ready"), putMeta(env, "index_epoch", runId),
        ...deleteMeta(env), clearGuard(env),
      ]);
      return json({ ok: true, head_sha: target, index_epoch: runId });
    }

    if (operation === "abort") {
      await env.DB.batch([
        clearGuard(env),
        guard(env,
          "run_id=? AND (lease_until<? OR EXISTS(SELECT 1 FROM meta WHERE k='index_state' AND v='failed'))",
          [runId, now]),
        env.DB.prepare("DELETE FROM index_rebuild_lock WHERE singleton=1"),
        putMeta(env, "index_state", "failed"), ...deleteMeta(env), clearGuard(env),
      ]);
      return json({ ok: true, state: "failed" });
    }

    return json({ ok: false, error: "unknown operation" }, 400);
  } catch (error) {
    const message = String(error).slice(0, 500);
    const conflict = /UNIQUE constraint|NOT NULL constraint|index_rebuild/i.test(message);
    return json({ ok: false, error: message }, conflict ? 409 : 400);
  }
}
