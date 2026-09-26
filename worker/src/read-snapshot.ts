import type { Env } from "./search";

async function generation(env: Env) {
  const {results} = await env.DB.prepare("SELECT k,v FROM meta WHERE k IN ('index_state','index_epoch')")
    .all<{k:string;v:string}>();
  const m = Object.fromEntries(results.map(r => [r.k,r.v]));
  return {state:m.index_state ?? "ready", epoch:m.index_epoch ?? ""};
}

/** A multi-query tool must never return a mixture of two index generations. */
export async function readSnapshot<T>(env: Env, load: () => Promise<T>): Promise<T> {
  const before = await generation(env);
  if (before.state !== "ready") throw new Error("Index is rebuilding or failed; retry after get_index_status reports ready.");
  const value = await load();
  const after = await generation(env);
  if (after.state !== "ready" || after.epoch !== before.epoch)
    throw new Error("Index changed during this read; retry. No partial result was returned.");
  return value;
}
