/**
 * The write path.
 *
 * The primitive is a whole cycle, not a page. AGENTS.md says a cycle produces a
 * research trail, zero or more outputs, and exactly one log entry, and that a
 * cycle whose `next` is empty is unfinished. A per-page write tool lets an
 * agent drop a finding with no trail and no log — which is precisely the
 * "manufacture confident nonsense at scale" that docs/retrieval.md warns
 * about, arriving through the front door. So a cycle is validated as a unit and
 * written as one commit.
 */
import schema from "./schema.generated.json";
import * as gh from "./github";
import type { Env } from "./search";

export interface WriteEnv extends Env, gh.GitHubEnv {
  DB: D1Database;
  GITHUB_REPO: string;
}

export interface PageInput {
  type: string;
  slug: string;
  title: string;
  summary: string;
  body: string;
  tags?: string[];
  related?: string[];
  entities?: string[];
  supersedes?: string[];
  question?: string;
  cycle_class?: string;
  source?: string;
  confidence?: string;
  replicated?: boolean | null;
  claims?: Record<string, unknown>[];
  authors?: string[];
  year?: number;
  review_after?: string;
  url?: string;
  venue?: string;
  stance?: string;
  next?: string;
  search?: string[];
  output?: string[];
}

export interface CycleInput {
  question: string;
  cycle_class?: string;
  research: PageInput;
  outputs?: PageInput[];
  log: PageInput;
}

// Source and concept pages are legitimate cycle outputs: source preserves the
// evidence read during the cycle, and Class C may establish a concept. Hubs,
// research pages and extra log entries remain forbidden here.
const OUTPUT_TYPES = new Set(["source", "concept", "finding", "comparison", "thesis", "synthesis", "query"]);

/** The cycle-level question and class are the single source of truth. */
export function normalizeCycle(cycle: CycleInput): CycleInput {
  const cycleClass = cycle.cycle_class ?? cycle.research?.cycle_class ?? cycle.log?.cycle_class;
  return {
    ...cycle,
    cycle_class: cycleClass,
    research: cycle.research ? {
      ...cycle.research,
      question: cycle.question,
      cycle_class: cycleClass,
    } : cycle.research,
    log: cycle.log ? {
      ...cycle.log,
      slug: `${todayUtc()}-${cycle.log.slug.replace(/^\d{4}-\d{2}-\d{2}-/, '')}`,
      question: cycle.question,
      cycle_class: cycleClass,
    } : cycle.log,
  };
}

const SLUG_RE = new RegExp(schema.slugPattern);
const LOCK_TTL_MS = 10 * 60 * 1000;

/** UTC, always. `W.today()` uses the runner's local date, and a 02:00 MSK cycle would file itself as yesterday. */
export function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Path is computed here. The client never supplies one, so it cannot escape wiki/. */
export function pagePath(type: string, slug: string): string {
  const dir = (schema.typeDir as Record<string, string>)[type];
  return dir ? `wiki/${dir}/${slug}.md` : `wiki/${slug}.md`;
}

function yamlScalar(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  // JSON strings and flow collections are valid YAML 1.2 scalars. Using them
  // also escapes newlines and prevents objects in claims becoming
  // "[object Object]" through String coercion.
  return JSON.stringify(v);
}

function yamlList(key: string, items: unknown[]): string {
  return `${key}: ${JSON.stringify(items)}\n`;
}

/**
 * Render frontmatter in FIELD_ORDER.
 *
 * Not byte-exact with dump_frontmatter, and it does not need to be: publish.yml
 * runs `lint.py --fix` on main and normalises whatever lands. Chasing
 * byte-equality here would be a second serialiser to keep in step.
 */
export function renderPage(page: PageInput, author: string, today: string): string {
  const fm: Record<string, unknown> = {
    type: page.type,
    title: page.title,
    summary: page.summary,
    tags: page.tags ?? [],
    entities: page.entities,
    related: page.related ?? [],
    created: today,
    updated: today,
    author,
    status: "current",
    supersedes: page.supersedes,
    authors: page.authors,
    year: page.year,
    review_after: page.review_after,
    question: page.question,
    cycle_class: page.cycle_class,
    url: page.url,
    venue: page.venue,
    source: page.source,
    confidence: page.confidence,
    stance: page.stance,
    replicated: page.type === "finding" ? (page.replicated ?? null) : undefined,
    claims: page.claims,
    date: page.type === "log" ? today : undefined,
    search: page.search,
    output: page.output,
    next: page.next,
  };

  let out = "---\n";
  for (const key of new Set([...schema.fieldOrder, "review_after", "date", "search", "output", "next"])) {
    const v = fm[key];
    if (v === undefined) continue;
    if (Array.isArray(v)) out += yamlList(key, v);
    else out += `${key}: ${yamlScalar(v)}\n`;
  }
  out += "---\n\n";
  return out + page.body.trim() + "\n";
}

export interface Problem { where: string; message: string }

/**
 * Everything checkable without a CI round trip.
 *
 * Deliberately shallow. Type-specific rules — url-or-venue, stance, the fact
 * that `replicated: null` counts as present — stay in lint.py, which CI runs
 * against the branch. Reimplementing them here would produce a second
 * declaration of the schema that drifts, and this repository has already been
 * bitten once by exactly that.
 */
export async function validateCycle(env: WriteEnv, cycle: CycleInput): Promise<Problem[]> {
  const problems: Problem[] = [];
  const add = (where: string, message: string) => problems.push({ where, message });

  if (!cycle.question?.trim()) add("cycle", "question is empty — a cycle starts with one");
  const cycleClass = cycle.cycle_class ?? cycle.research?.cycle_class ?? cycle.log?.cycle_class;
  if (!cycleClass) add("cycle", "cycle_class is empty — classify the cycle A, B, C, D or E before search");
  else if (!schema.cycleClasses.includes(cycleClass)) {
    add("cycle", `cycle_class must be one of ${schema.cycleClasses.join(", ")}`);
  }
  if (!cycle.research) add("cycle", "no research page; a cycle records what it fetched and read");
  if (!cycle.log) add("cycle", "no log entry; a cycle produces exactly one");
  if (cycle.log && !String(cycle.log.next ?? "").trim()) {
    add("log", "next is empty — an unfinished cycle stopped at search (write CLOSED if it is done)");
  }
  if (cycle.research && cycle.research.type !== "research") add("research", "must be type: research");
  if (cycle.log && cycle.log.type !== "log") add("log", "must be type: log");
  for (const [where, page] of [["research", cycle.research], ["log", cycle.log]] as const) {
    if (page?.question && page.question !== cycle.question) {
      add(where, "question disagrees with the cycle-level question; remove it or make it identical");
    }
    if (cycleClass && page?.cycle_class && page.cycle_class !== cycleClass) {
      add(where, "cycle_class disagrees with the cycle-level cycle_class; remove it or make it identical");
    }
  }
  for (const output of cycle.outputs ?? []) {
    if (!OUTPUT_TYPES.has(output.type)) {
      add(output.slug || "output", `output type ${JSON.stringify(output.type)} is not allowed; use source, concept, finding, comparison, thesis, synthesis or query`);
    }
  }

  // Validate the same server-dated log path that render/commit will use.
  cycle = normalizeCycle(cycle);
  const pages = [cycle.research, ...(cycle.outputs ?? []), cycle.log].filter(Boolean);
  const seen = new Set<string>();
  const incomingClaims = new Map<string, { value: string; observed: string; page: string }[]>();

  for (const page of pages) {
    const where = page.slug || "(no slug)";
    if (!page.slug || !SLUG_RE.test(page.slug)) {
      add(where, `slug must match ${schema.slugPattern}`);
      continue;
    }
    if (seen.has(page.slug)) add(where, "two pages in this cycle share a slug");
    seen.add(page.slug);

    if (!(page.type in schema.typeDir)) {
      add(where, `unknown type ${JSON.stringify(page.type)}`);
      continue;
    }
    if (!page.title?.trim()) add(where, "title is empty");
    const summary = String(page.summary ?? "");
    if (!summary.trim()) add(where, "summary is empty");
    else if (summary.length > schema.summaryMax) {
      add(where, `summary is ${summary.length} chars, max ${schema.summaryMax}`);
    }
    if (!page.body?.trim()) add(where, "body is empty");
    if (page.body?.includes("<!-- generated:")) {
      add(where, "body carries the generated marker, which hides a page from every tool");
    }
    if (page.cycle_class && !schema.cycleClasses.includes(page.cycle_class)) {
      add(where, `cycle_class must be one of ${schema.cycleClasses.join(", ")}`);
    }
    if (page.confidence && !schema.confidence.includes(page.confidence)) {
      add(where, `confidence must be one of ${schema.confidence.join(", ")}`);
    }
  }

  if (problems.length) return problems;

  // Corpus-level checks, straight out of D1. These are the expensive-to-discover
  // ones: a duplicate slug makes every [[link]] to it ambiguous, and an
  // unresolvable `supersedes` silently leaves the old page marked current.
  const slugs = [...seen];
  const marks = slugs.map(() => "?").join(", ");
  const { results: clashes } = await env.DB
    .prepare(`SELECT slug FROM pages WHERE slug IN (${marks})`)
    .bind(...slugs).all<{ slug: string }>();
  for (const { slug } of clashes) {
    add(slug, "a page with this slug already exists; [[links]] resolve by filename, not path");
  }

  for (const page of pages) {
    for (const raw of page.supersedes ?? []) {
      const target = String(raw).replace(/\[\[|\]\]/g, "").trim();
      if (!target) continue;
      const hit = await env.DB.prepare("SELECT 1 AS ok FROM pages WHERE slug = ?").bind(target).first();
      if (!hit && !seen.has(target)) {
        add(page.slug, `supersedes [[${target}]] which does not exist — a target that matches nothing leaves the old page current`);
      }
    }
    for (const claim of page.claims ?? []) {
      const subject = String(claim.subject ?? "");
      const metric = String(claim.metric ?? "");
      const qualifier = String(claim.qualifier ?? "");
      const asOf = String(claim.as_of ?? "");
      if (!subject || !metric || claim.value === undefined || !asOf) {
        add(page.slug, "each claim needs subject, metric, value and as_of");
        continue;
      }
      const claimKey = JSON.stringify([subject, metric, qualifier, asOf]);
      const incomingObserved = String(claim.observed_at ?? "");
      const priorIncoming = incomingClaims.get(claimKey) ?? [];
      for (const prior of priorIncoming) {
        if (prior.value === String(claim.value)) continue;
        if (prior.observed && incomingObserved && prior.observed !== incomingObserved) continue;
        add(page.slug, `contradiction inside cycle: ${subject}/${metric} as of ${asOf} has ` +
          `${prior.value} in ${prior.page} and ${claim.value} here. Distinct same-day snapshots ` +
          "require different observed_at values on both claims.");
      }
      priorIncoming.push({ value: String(claim.value), observed: incomingObserved, page: page.slug });
      incomingClaims.set(claimKey, priorIncoming);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf)) add(page.slug, `claim as_of must be YYYY-MM-DD, got ${asOf}`);
      if (asOf > todayUtc()) add(page.slug, `claim as_of ${asOf} is in the future`);
      const { results: clashes } = await env.DB.prepare(
        `SELECT c.value, c.page, p.frontmatter FROM claims c
         JOIN pages p ON p.slug=c.page
         WHERE c.subject=? AND c.metric=? AND c.qualifier=? AND c.as_of=?`
      ).bind(subject, metric, qualifier, asOf)
        .all<{ value: string; page: string; frontmatter: string }>();
      for (const clash of clashes) {
        if (String(clash.value) === String(claim.value)) continue;
        let existingObserved = "";
        try {
          const fm = JSON.parse(clash.frontmatter) as { claims?: Record<string, unknown>[] };
          const prior = (fm.claims ?? []).find((c) =>
            String(c.subject ?? "") === subject && String(c.metric ?? "") === metric &&
            String(c.qualifier ?? "") === qualifier && String(c.as_of ?? "") === asOf &&
            String(c.value) === String(clash.value));
          existingObserved = String(prior?.observed_at ?? "");
        } catch { /* malformed indexed frontmatter is handled by index validation */ }
        // A leaderboard can publish two real snapshots on one calendar day.
        // Both sides must explicitly identify distinct observations; adding a
        // timestamp to only the new side must not bypass an old contradiction.
        if (incomingObserved && existingObserved && incomingObserved !== existingObserved) continue;
        add(page.slug, `contradiction: ${subject}/${metric} as of ${asOf} is ${clash.value} in ${clash.page}, not ${claim.value}. Distinct same-day snapshots require different observed_at values on both claims.`);
      }
    }
  }

  return problems;
}

/* --- the merge lock -------------------------------------------------------
 *
 * Two agents proposing on the same day can each pass validation against the
 * same view of main and still break main by merging: the corpus checks are
 * whole-corpus, so branch-green does not imply main-green. Cutting the branch
 * from main *inside* a lock restores that implication.
 *
 * It is a D1 row rather than a Durable Object. A DO is the textbook answer, but
 * SQLite Durable Objects are also available on Free. This service already
 * uses D1; conditional updates provide its bounded merge lock without another binding.
 */

export async function ensureTables(env: WriteEnv): Promise<void> {
  await env.DB.batch([
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS locks (
      name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS proposals (
      id TEXT PRIMARY KEY, branch TEXT NOT NULL, sha TEXT NOT NULL,
      status TEXT NOT NULL, errors TEXT, merged_sha TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, summary TEXT)`),
  ]);
}

/** D1 is derived from this exact Git commit; writes fail closed if it is stale or rebuilding. */
export async function indexedHeadSha(env: WriteEnv): Promise<string | null> {
  const { results } = await env.DB.prepare("SELECT k,v FROM meta WHERE k IN ('head_sha','index_state')")
    .all<{ k: string; v: string }>();
  const meta = Object.fromEntries((results ?? []).map((row) => [row.k, row.v]));
  // Legacy indexes predate index_state and are ready by definition. Once the
  // protocol writes a state, only the explicit ready state authorizes writes.
  return (meta.index_state ?? "ready") === "ready" ? (meta.head_sha ?? null) : null;
}

export async function acquireLock(env: WriteEnv, holder: string): Promise<boolean> {
  const now = Date.now();
  const res = await env.DB.prepare(
    `INSERT INTO locks (name, holder, expires_at) VALUES ('merge', ?1, ?2)
       ON CONFLICT(name) DO UPDATE SET holder = ?1, expires_at = ?2
       WHERE locks.expires_at < ?3 OR locks.holder = ?1`
  ).bind(holder, now + LOCK_TTL_MS, now).run();
  return (res.meta?.changes ?? 0) > 0;
}

/**
 * Atomically transfer a proposal's long-lived lock to one check_proposal call.
 * A unique new holder makes concurrent checks mutually exclusive even though
 * they refer to the same proposal id.
 */
export async function claimLock(env: WriteEnv, proposalId: string, holder: string): Promise<boolean> {
  const now = Date.now();
  const res = await env.DB.prepare(
    `INSERT INTO locks (name, holder, expires_at) VALUES ('merge', ?1, ?2)
       ON CONFLICT(name) DO UPDATE SET holder = ?1, expires_at = ?2
       WHERE locks.holder = ?3 OR locks.expires_at < ?4`
  ).bind(holder, now + LOCK_TTL_MS, proposalId, now).run();
  return (res.meta?.changes ?? 0) > 0;
}

export async function releaseLock(env: WriteEnv, holder: string): Promise<void> {
  await env.DB.prepare("DELETE FROM locks WHERE name='merge' AND holder=?").bind(holder).run();
}
