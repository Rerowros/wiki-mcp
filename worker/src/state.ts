/**
 * The structured path: claims, backlinks, listings, open threads.
 *
 * `getState` is the tool that should answer before `search` is reached. On this
 * corpus that is not a nicety — measured against tests/golden.yaml, "what is
 * the current model SKU" puts the correct page sixth under BM25, behind
 * a hub whose own summary asserts the opposite, while the claims lookup answers
 * it correctly in one row with its citation.
 */
import { pageUrl, type Env } from "./search";
import { resolveMeasurements, type Measurement } from "./measurements";

export interface Claim {
  subject: string;
  metric: string;
  qualifier: string;
  value: string;
  as_of: string;
  source: string;
  source_url: string;
  earlier?: { value: string; as_of: string; source: string }[];
}

/**
 * How much of the vault the claims table actually covers.
 *
 * Shipped with every state lookup, hit or miss. An empty result reads to a
 * model as "the vault does not know this", which is the exact inversion of the
 * truth: the vault knows a great deal and has recorded a fraction of it in
 * machine-readable form. These numbers make the sparsity a visible fact rather
 * than something to infer from silence, and next_step makes the fallback to
 * search mandatory rather than optional.
 */
async function coverage(env: Env, subject: string, metric: string) {
  const row = await env.DB.prepare(
    `SELECT (SELECT count(*) FROM claims) AS claims_total,
            (SELECT count(DISTINCT subject) FROM claims) AS subjects_total,
            (SELECT count(DISTINCT page) FROM claims) AS pages_with_claims,
            (SELECT count(*) FROM pages WHERE type = 'finding') AS findings_total`
  ).first<{ claims_total: number; subjects_total: number; pages_with_claims: number; findings_total: number }>();

  const { results: subjects } = await env.DB.prepare(
    `SELECT DISTINCT subject FROM claims ORDER BY subject LIMIT 60`
  ).all<{ subject: string }>();

  const findingsWithClaims = await env.DB.prepare(
    `SELECT count(*) AS n FROM pages p WHERE p.type='finding' AND EXISTS (SELECT 1 FROM claims c WHERE c.page=p.slug)`
  ).first<{n:number}>();
  const uncovered = (row?.findings_total ?? 0) - (findingsWithClaims?.n ?? 0);
  return {
    claims_total: row?.claims_total ?? 0,
    subjects_total: row?.subjects_total ?? 0,
    pages_with_claims: row?.pages_with_claims ?? 0,
    findings_total: row?.findings_total ?? 0,
    known_subjects: subjects.map((s) => s.subject),
    note:
      `The claims table is a partial index over findings, not over the corpus. ` +
      `${uncovered} of ${row?.findings_total ?? 0} findings have no structured claims; some are qualitative. ` +
      `A miss here means NOT RECORDED AS A CLAIM, not NOT KNOWN.`,
    next_step:
      `If nothing matched, call search(${JSON.stringify(`${subject} ${metric}`.trim() || "your question")}) ` +
      `before concluding the vault has no answer.`,
  };
}

export async function measurementRecords(env: Env, subject = "", metric = "") {
  const clauses: string[] = [];
  const binds: string[] = [];
  if (subject) { binds.push(`%${subject.toLowerCase()}%`); clauses.push(`(lower(c.subject) LIKE ?${binds.length} OR c.metric = 'active-version')`); }
  if (metric) { binds.push(`%${metric.toLowerCase()}%`); clauses.push(`(lower(c.metric) LIKE ?${binds.length} OR c.metric = 'active-version')`); }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";

  const { results } = await env.DB.prepare(
    `SELECT DISTINCT p.slug AS page, p.rel_path, p.status, p.frontmatter
       FROM claims c JOIN pages p ON p.slug = c.page ${where}
      ORDER BY p.slug
      LIMIT 10001`
  ).bind(...binds).all<{ subject: string; metric: string; qualifier: string; value: string; as_of: string; page: string;
    rel_path: string; status: string; frontmatter: string }>();
  if (results.length > 10000) throw new Error("Too many measurements; narrow subject/metric. No partial state was returned.");
  const records: Measurement[] = [];
  for (const r of results) {
    const fm = JSON.parse(r.frontmatter) as {review_after?: string; claims?: Measurement[]};
    // Frontmatter retains every observation, including revisions within one day.
    // Joining a SQL row back with find() loses all but the first such observation.
    for (const original of fm.claims ?? []) {
      if (original.metric !== 'active-version' &&
          ((subject && !original.subject.toLowerCase().includes(subject.toLowerCase())) ||
           (metric && !original.metric.toLowerCase().includes(metric.toLowerCase())))) continue;
      records.push({...original, qualifier:original.qualifier ?? "", as_of:String(original.as_of),
        source:r.page, source_url:pageUrl(r.rel_path, env.GITHUB_REPO), path:r.rel_path,
        evidence_url:original.source_url, page_status:r.status, review_after:fm.review_after ?? ""});
      if (records.length > 10000) throw new Error("Too many measurements; narrow subject/metric. No partial state was returned.");
    }
  }
  return records;
}

export async function getState(env: Env, subject = "", metric = "", version?: string, includeHistory = false) {
  return {...resolveMeasurements(await measurementRecords(env, subject, metric), subject, metric, version, includeHistory),
    coverage:await coverage(env, subject, metric)};
}

export async function getRefreshQueue(env: Env, subject = "", metric = "") {
  const result = resolveMeasurements(await measurementRecords(env, subject, metric), subject, metric);
  const due = result.matched.filter(r => r.freshness === "review_due");
  return {missing_current:result.missing_current, review_due:due, active_versions:result.active_versions,
    instruction:"Recheck the methodology version first, then fetch exactly the missing configurations. Save source_url, observed_at and a snapshot hash. An absent measurement is unknown, never zero. This queue does not schedule or publish anything."};
}

export async function getBacklinks(env: Env, slug: string, offset = 0) {
  const { results } = await env.DB.prepare(
    `SELECT l.src, l.kind, p.type, p.title, p.page_date
       FROM links l JOIN pages p ON p.slug = l.src
      WHERE l.dst = ?1 ORDER BY l.kind, l.src LIMIT 201 OFFSET ?2`
  ).bind(slug, offset).all<{ src: string; kind: string; type: string; title: string; page_date: string }>();

  const inbound: Record<string, { id: string; title: string; type: string; date: string }[]> = {};
  for (const r of results.slice(0,200)) {
    (inbound[r.kind] ??= []).push({ id: r.src, title: r.title, type: r.type, date: r.page_date ?? "" });
  }
  return { slug, returned: Math.min(results.length,200), inbound, next_offset:results.length > 200 ? offset+200 : null };
}

export async function listByType(
  env: Env,
  opts: { type?: string; status?: string; since?: string; until?: string; limit?: number; offset?: number } = {}
) {
  const clauses: string[] = [];
  const binds: (string | number)[] = [];
  const add = (sql: string, v: string) => { binds.push(v); clauses.push(sql.replace("?", `?${binds.length}`)); };
  if (opts.type) add("type = ?", opts.type);
  add("status = ?", opts.status ?? "current");
  if (opts.since) add("page_date >= ?", opts.since);
  if (opts.until) add("page_date <= ?", opts.until);
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const limit = Math.min(Math.max(opts.limit ?? 50, 1), 200);
  const offset = Math.max(0, Math.floor(opts.offset ?? 0));

  const { results } = await env.DB.prepare(
    `SELECT slug, type, title, summary, status, page_date, rel_path
       FROM pages ${where} ORDER BY page_date DESC, slug LIMIT ${limit+1} OFFSET ${offset}`
  ).bind(...binds).all<{ slug: string; type: string; title: string; summary: string; status: string; page_date: string; rel_path: string }>();

  return {items:results.slice(0,limit).map((r) => ({
    id: r.slug, title: r.title, url: pageUrl(r.rel_path, env.GITHUB_REPO),
    type: r.type, summary: r.summary, status: r.status, date: r.page_date ?? "",
  })), next_offset:results.length > limit ? offset+limit : null};
}

export async function getOpenThreads(env: Env, limit = 20, offset = 0) {
  limit = Math.min(Math.max(limit,1),100);
  const { results } = await env.DB.prepare(
    `SELECT t.page, t.date, t.question, t.next, p.rel_path FROM threads t JOIN pages p ON p.slug=t.page
     WHERE p.status='current' ORDER BY date DESC, page DESC LIMIT ${limit+1} OFFSET ?1`
  ).bind(offset).all<{ page: string; date: string; question: string; next: string; rel_path: string }>();
  return {items:results.slice(0,limit).map((r) => ({
    id: r.page, url: pageUrl(r.rel_path, env.GITHUB_REPO), date: r.date,
    question: r.question ?? "", next: r.next,
  })), next_offset:results.length > limit ? offset+limit : null};
}

export async function getMeta(env: Env) {
  const { results } = await env.DB.prepare(`SELECT k, v FROM meta`).all<{ k: string; v: string }>();
  const meta = Object.fromEntries(results.map((r) => [r.k, r.v]));
  const counts = await env.DB.prepare(
    `SELECT (SELECT count(*) FROM pages) AS pages,
            (SELECT count(*) FROM chunks) AS chunks,
            (SELECT count(*) FROM claims) AS claims`
  ).first<{ pages: number; chunks: number; claims: number }>();
  return { ...meta, ...counts };
}
