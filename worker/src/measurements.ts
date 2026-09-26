/** Pure version/freshness resolver. Shared fixtures exercise parity with tools/measurements.py. */
export interface Measurement {
  subject: string; metric: string; qualifier?: string | null; value: unknown;
  as_of: string; source: string; source_url?: string; path?: string;
  page_status?: string; review_after?: string; observed_at?: string;
  measurement_status?: string; [key: string]: unknown;
}
const VERSIONED = /^(.+)-v(\d+(?:\.\d+)*)$/;
const cmp = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
export function resolveMeasurements(records: Measurement[], subject = "", metric = "", version?: string,
  includeHistory = false, today = new Date().toISOString().slice(0, 10)) {
  records = records.map(r => r.observed_at ? {...r,observed_at:String(r.observed_at).replace(" ","T").replace("+00:00","Z")} : r);
  const active: Record<string, {version: string; as_of: string; source: string; source_url?: string; review_due: boolean}> = {};
  const ordered = [...records].sort((a,b) => cmp(b.as_of,a.as_of) || cmp(b.observed_at ?? "",a.observed_at ?? "") || cmp(b.source,a.source));
  for (const r of ordered) {
    if (r.metric === "active-version" && (r.page_status ?? "current") === "current" && r.as_of <= today && (r.observed_at ?? "").slice(0,10) <= today) {
      active[r.subject] ??= {version: String(r.value), as_of: r.as_of, source: r.source,
        source_url: r.source_url, review_due: Boolean(r.review_after && r.review_after <= today)};
    }
  }
  const wanted = version || metric.match(VERSIONED)?.[2];
  const groups = new Map<string, Measurement[]>();
  for (const r of ordered) {
    if (subject && !r.subject.toLowerCase().includes(subject.toLowerCase())) continue;
    if (metric && !r.metric.toLowerCase().includes(metric.toLowerCase())) continue;
    const key = JSON.stringify([r.subject, r.metric, r.qualifier ?? ""]);
    const group = groups.get(key) ?? []; group.push(r); groups.set(key, group);
  }
  const matched: Record<string, unknown>[] = [], historical: Record<string, unknown>[] = [];
  const missing = new Map<string, Record<string, unknown>>(), cells = new Set<string>();
  for (const [key, entries] of [...groups.entries()].sort(([a],[b]) => cmp(a,b))) {
    const [sub, met, qual] = JSON.parse(key) as string[];
    const eligible = entries.filter(r => (r.page_status ?? "current") === "current" && r.as_of <= today && (r.observed_at ?? "").slice(0,10) <= today);
    const original = (eligible.length ? eligible : entries)[0];
    const parsed = met.match(VERSIONED), family = parsed?.[1] ?? (active[met] ? met : undefined);
    const measuredVersion = parsed?.[2] ?? null;
    const expected = wanted || (family ? active[family]?.version : undefined);
    const row: Record<string, unknown> = {...original, qualifier: qual,
      observed_at: original.observed_at ?? null, measurement_status: original.measurement_status ?? "unspecified",
      freshness: original.review_after && original.review_after <= today ? "review_due" : "dated_snapshot",
      version: measuredVersion, earlier: entries.filter(r => r !== original).map(r => ({value:r.value, as_of:r.as_of,
        observed_at:r.observed_at ?? null,source:r.source,source_url:r.source_url ?? null,
        evidence_url:r.evidence_url ?? null,page_status:r.page_status ?? 'current'}))};
    const cell = JSON.stringify([sub,family,qual]);
    if (!eligible.length) row.exclusion_reason = "page_not_current_or_future";
    else if (family && (!expected || measuredVersion !== expected)) row.exclusion_reason = expected ? "version_not_active" : "active_version_not_recorded";
    else if (wanted && !family) row.exclusion_reason = "not_a_versioned_metric";
    else { matched.push(row); if(family) cells.add(cell); continue; }
    historical.push(row);
    if (family && expected) missing.set(cell, {subject:sub,family,qualifier:qual,expected_version:expected,
      expected_metric:`${family}-v${expected}`, reason:"No current measurement for this version/configuration; do not reuse another version."});
  }
  return {matched, active_versions:active, missing_current:[...missing.entries()].filter(([k]) => !cells.has(k)).map(([,v]) => v),
    historical:includeHistory ? historical : [], historical_count:historical.length,
    note:"Latest recorded observations, not a live provider check. Compare only the same version and configuration. Use an explicit version or include_history for older measurements; review_due requires rechecking."};
}
