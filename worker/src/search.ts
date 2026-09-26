/**
 * Retrieval over the D1 index.
 *
 * This is a port of `tools/query.py`, deliberately: the two index the same
 * heading chunks the same way and score with the same field weights, so a
 * ranking measured locally against tests/golden.yaml still holds once served.
 * When they drift, the local numbers stop predicting anything and every tuning
 * decision made against them is worthless.
 */

export interface Env {
  DB: D1Database;
  MCP_TOKEN: string;
  ADMIN_TOKEN: string;
  /** "owner/name" of the repository holding wiki/; used for page URLs. */
  GITHUB_REPO?: string;
}

/** Placeholder used when GITHUB_REPO is not configured. */
export const DEFAULT_REPO = "your-org/wiki-mcp";

/**
 * title, summary, heading, symbols, text — the column order of chunks_fts.
 *
 * Swept against tests/golden.yaml rather than chosen by taste: against the
 * obvious-looking [8, 6, 3, 10, 1] this moves recall@5 from 0.67 to 0.80 and
 * MRR from 0.543 to 0.627. The heavy title and summary weights turned out to be
 * actively harmful — on a corpus where dozens of pages are about the same
 * subject, they reward a page for being *about* the topic rather than for
 * answering the question. Kept in step with WEIGHTS in tools/query.py.
 */
const WEIGHTS = [2.0, 2.0, 1.0, 4.0, 1.0] as const;

/** Matches tools/chunk.py TOKEN_RE: everything that is not a letter or digit splits. */
const TOKEN_RE = /[\p{L}\p{N}]+/gu;

/** Matches tools/chunk.py SYMBOL_RE: a dotted or hyphenated identifier. */
const SYMBOL_RE = /[\p{Ll}][\p{Ll}\p{N}]*(?:[-.][\p{Ll}\p{N}]+)+/gu;

export interface Hit {
  id: string;
  title: string;
  url: string;
  type: string;
  summary: string;
  date: string;
  status: string;
  score: number;
  heading: string;
}

/**
 * Build an FTS5 MATCH expression from free text.
 *
 * Every term is emitted as a quoted phrase. That is not cosmetic: FTS5 MATCH is
 * a query language with its own operators, so unquoted user input can change
 * the query's meaning or fail to parse. Quoting makes the input data.
 */
export function buildMatch(query: string): string {
  const terms = new Set<string>();
  for (const m of query.toLowerCase().matchAll(TOKEN_RE)) terms.add(m[0]);

  const clauses: string[] = [];
  // The collapsed identifier goes first and against the symbols column. It is
  // the only form that carries real IDF: `model-3.8-flash` tokenizes to
  // model/3/8/flash, whose distinguishing parts are two of the commonest
  // tokens in a vault full of version numbers.
  for (const m of query.toLowerCase().matchAll(SYMBOL_RE)) {
    const raw = m[0];
    if (!/\d/.test(raw)) continue;
    clauses.push(`symbols:"${raw.replace(/[-.]/g, "")}"`);
    clauses.push(`"${raw}"`);
    for (const part of raw.split(/[-.]/)) terms.delete(part);
  }
  for (const t of terms) if (t.length > 1) clauses.push(`"${t}"`);

  return clauses.join(" OR ");
}

/**
 * docs/retrieval.md: "an answer-shaped question prefers finding, comparison,
 * thesis; an evidence-shaped question prefers research and source". Nothing
 * implemented it, and trails kept outranking the findings they produced —
 * asked what a subscription plan includes, the vault returned the working notes
 * rather than the conclusion. Swept with tools/tune.py: MRR 0.627 -> 0.737.
 * Kept in step with TYPE_PRIOR in tools/query.py.
 */
const TYPE_PRIOR: Record<string, number> = {
  finding: 1.2, comparison: 1.2, thesis: 1.2, synthesis: 1.2,
  research: 0.7, source: 0.7, log: 0.7,
};

/**
 * One hop over hand-placed edges: a page linked by a strong hit inherits a
 * tenth of its score. Those 6544 edges were placed deliberately by whoever
 * wrote the page, which is a stronger signal than co-occurrence — but only
 * just: 0.2 measured worse than 0.1 and 0.3 worse than nothing, so this is a
 * nudge, not a mechanism. Body prose links are excluded; only curated
 * frontmatter edges count.
 */
const GRAPH_BOOST = 0.1;
const GRAPH_SEEDS = 5;
const CURATED_KINDS = ["related", "source", "supersedes", "superseded_by", "entities"];

export interface SearchOptions {
  limit?: number;
  type?: string;
  includeSuperseded?: boolean;
}

export async function search(env: Env, query: string, opts: SearchOptions = {}): Promise<Hit[]> {
  const match = buildMatch(query);
  if (!match) return [];

  const limit = Math.min(Math.max(opts.limit ?? 10, 1), 50);

  const sql = `
    SELECT c.slug, c.heading,
           p.type, p.title, p.summary, p.page_date, p.status, p.rel_path,
           bm25(chunks_fts, ${WEIGHTS.join(", ")}) AS score
      FROM chunks_fts f
      JOIN chunks c ON c.id = f.rowid
      JOIN pages  p ON p.slug = c.slug
     WHERE chunks_fts MATCH ?1
       ${opts.type ? "AND p.type = ?2" : ""}
       ${opts.includeSuperseded ? "AND p.status != 'draft'" : "AND p.status = 'current'"}
     ORDER BY score ASC
     LIMIT 400`;

  const stmt = opts.type
    ? env.DB.prepare(sql).bind(match, opts.type)
    : env.DB.prepare(sql).bind(match);
  const { results } = await stmt.all<{
    slug: string; heading: string; type: string; title: string;
    summary: string; page_date: string; status: string; score: number; rel_path: string;
  }>();

  // bm25() is negative and lower is better, so flip it into a positive score
  // before aggregating. Getting this backwards ranks the worst match first and
  // still looks like a working search, which is why it is done in one place.
  const byPage = new Map<string, { best: number; row: typeof results[number] }>();
  for (const row of results) {
    const score = -row.score * (TYPE_PRIOR[row.type] ?? 1.0);
    const cur = byPage.get(row.slug);
    if (!cur || score > cur.best) byPage.set(row.slug, { best: score, row });
  }

  await expandOverLinks(env, byPage);

  // A page scores as its single best chunk. Nothing else.
  //
  // Two corroboration bonuses were tried and both made it worse. A tenth of
  // EVERY matching chunk let a 200-chunk page bank 20x its own best score, so
  // the 89 KB benchmark catalogue won queries where its own best chunk ranked
  // 1624th. A tenth of just the second-best still reordered pages whose best
  // chunks were 56 ranks apart, because bm25 values sit in a narrow band and a
  // tenth of one score is comparable to the gap between good and mediocre.
  // Measured, dropping it holds recall and moves MRR 0.594 -> 0.641 and
  // staleness 0.50 -> 0.00.
  //
  // This is passage retrieval: the best passage decides which page answers.
  return [...byPage.entries()]
    .map(([slug, e]) => ({
      id: slug,
      title: e.row.title,
      url: pageUrl(e.row.rel_path, env.GITHUB_REPO),
      type: e.row.type,
      summary: e.row.summary,
      // Currency travels with every result. 45% of slugs are dated and almost
      // nothing is marked superseded, so status alone would tell a reader
      // nothing about whether this is still true.
      date: e.row.page_date ?? "",
      status: e.row.status,
      heading: e.row.heading ?? "",
      score: Math.round(e.best * 1000) / 1000,
    }))
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
    .slice(0, limit);
}

/** One hop over curated links, seeded from the strongest hits only. */
async function expandOverLinks(
  env: Env,
  byPage: Map<string, { best: number; row: { slug: string } }>,
): Promise<void> {
  if (!GRAPH_BOOST || byPage.size === 0) return;
  const seeds = [...byPage.values()].sort((a, b) => b.best - a.best).slice(0, GRAPH_SEEDS);
  const marks = CURATED_KINDS.map(() => "?").join(", ");
  const rows = await Promise.all(seeds.map((seed) =>
    env.DB.prepare(`SELECT dst FROM links WHERE src = ? AND kind IN (${marks})`)
      .bind(seed.row.slug, ...CURATED_KINDS)
      .all<{ dst: string }>()
      .then((r) => ({ seed, dsts: r.results }))
  ));
  for (const { seed, dsts } of rows) {
    for (const { dst } of dsts) {
      const target = byPage.get(dst);
      if (target) target.best += seed.best * GRAPH_BOOST;
    }
  }
}

export function pageUrl(path: string, repo: string = DEFAULT_REPO): string {
  return `https://github.com/${repo || DEFAULT_REPO}/blob/main/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export async function fetchPage(env: Env, slug: string, full = false, offset = 0) {
  const page = await env.DB.prepare(
    `SELECT slug, type, title, summary, status, author, created, updated,
            page_date, rel_path, frontmatter
       FROM pages WHERE slug = ?1`
  ).bind(slug).first<Record<string, string>>();
  if (!page) return null;

  const { results: chunks } = await env.DB.prepare(
    `SELECT ord, heading, text FROM chunks WHERE slug = ?1 ORDER BY ord`
  ).bind(slug).all<{ ord: number; heading: string; text: string }>();

  // The largest page here is 89 KB. Returning that by default burns a context
  // window on one document, so the caller asks for it explicitly.
  const body = chunks.map(c => (c.heading ? `## ${c.heading}\n\n` : "") + c.text).join("\n\n");
  const end = full ? body.length : offset+12000;
  const truncated = end < body.length;

  return {
    id: slug,
    title: page.title,
    url: pageUrl(page.rel_path, env.GITHUB_REPO),
    type: page.type,
    summary: page.summary,
    status: page.status,
    date: page.page_date,
    author: page.author,
    created: page.created,
    updated: page.updated,
    path: page.rel_path,
    frontmatter: safeJson(page.frontmatter),
    text: body.slice(offset,end),
    offset, next_offset:truncated ? end : null,
    truncated,
    ...(truncated ? { note: "Continue with next_offset, or full=true. Do not infer missing sections from this excerpt." } : {}),
  };
}

function safeJson(raw: string): unknown {
  try { return JSON.parse(raw); } catch { return {}; }
}
