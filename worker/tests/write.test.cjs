const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { DatabaseSync } = require('node:sqlite');

function loader(overrides = {}) {
  const cache = new Map();
  function load(name) {
    if (cache.has(name)) return cache.get(name);
    const file = path.join(__dirname, '../src', name + '.ts');
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    }).outputText;
    const out = {}; cache.set(name, out);
    new Function('exports', 'require', code)(out, (dep) => {
      if (dep in overrides) return overrides[dep];
      if (dep.startsWith('./') && dep.endsWith('.json')) {
        return JSON.parse(fs.readFileSync(path.join(__dirname, '../src', dep), 'utf8'));
      }
      if (dep.startsWith('./')) return load(dep.slice(2));
      return require(dep);
    });
    return out;
  }
  return load;
}

function database() {
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);
    CREATE TABLE pages(slug TEXT PRIMARY KEY,frontmatter TEXT);
    CREATE TABLE claims(subject TEXT,metric TEXT,qualifier TEXT,value TEXT,as_of TEXT,page TEXT);`);
  const wrap = (sql, args = []) => ({
    bind: (...next) => wrap(sql, next),
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    first: async () => db.prepare(sql).get(...args) ?? null,
    run: async () => ({ meta: db.prepare(sql).run(...args) }),
  });
  const env = {
    DB: {
      prepare: (sql) => wrap(sql),
      batch: async (statements) => {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        return results;
      },
    },
    GITHUB_TOKEN: 'test', GITHUB_REPO: 'owner/repo',
  };
  return { db, env };
}

function cycle(claims = [], outputs = []) {
  return {
    question: 'What changed?', cycle_class: 'B',
    research: { type: 'research', slug: 'research-new', title: 'Research', summary: 'Research summary', body: 'Trail.', claims },
    outputs,
    log: { type: 'log', slug: 'cycle-new', title: 'Cycle', summary: 'Cycle summary', body: 'Done.', next: 'CLOSED' },
  };
}

test('renderPage preserves structured claims and extended frontmatter', () => {
  const { renderPage } = loader()('write');
  const claim = { subject: 'model', metric: 'score', value: 42, as_of: '2026-09-07' };
  const text = renderPage({
    type: 'thesis', slug: 't', title: 'T', summary: 'S', body: 'Body',
    claims: [claim], stance: 'supported', authors: ['A', 'B'], year: 2026,
    review_after: '2026-09-09',
  }, 'agent', '2026-09-08');
  assert.match(text, /claims: \[{"subject":"model","metric":"score","value":42,"as_of":"2026-09-07"}\]/);
  assert.doesNotMatch(text, /\[object Object\]/);
  assert.match(text, /stance: "supported"/);
  assert.match(text, /authors: \["A","B"\]/);
  assert.match(text, /year: 2026/);
  assert.match(text, /review_after: "2026-09-09"/);
});

test('source and concept are accepted outputs, conflicting explicit cycle metadata is rejected', async () => {
  const { env, db } = database();
  const { validateCycle } = loader()('write');
  const outputs = [
    { type: 'source', slug: 'source-new', title: 'Source', summary: 'Source summary', body: 'Evidence.' },
    { type: 'concept', slug: 'concept-new', title: 'Concept', summary: 'Concept summary', body: 'Definition.' },
  ];
  assert.deepEqual(await validateCycle(env, cycle([], outputs)), []);
  const bad = cycle(); bad.research.question = 'A different question';
  assert((await validateCycle(env, bad)).some((p) => /disagrees/.test(p.message)));
  db.close();
});

test('same-day contradictory values need distinct explicit observed_at on both sides', async () => {
  const { env, db } = database();
  const { validateCycle } = loader()('write');
  const old = { subject: 'm', metric: 'score', qualifier: '', value: 1, as_of: '2026-09-07' };
  db.prepare('INSERT INTO pages VALUES (?,?)').run('old', JSON.stringify({ claims: [old] }));
  db.prepare('INSERT INTO claims VALUES (?,?,?,?,?,?)').run('m', 'score', '', '1', '2026-09-07', 'old');
  const incoming = { subject: 'm', metric: 'score', qualifier: '', value: 2, as_of: '2026-09-07', observed_at: '2026-09-08T00:01:00Z' };
  assert((await validateCycle(env, cycle([incoming]))).some((p) => /contradiction/.test(p.message)));
  old.observed_at = '2026-09-07T23:59:00Z';
  db.prepare('UPDATE pages SET frontmatter=? WHERE slug=?').run(JSON.stringify({ claims: [old] }), 'old');
  assert.deepEqual(await validateCycle(env, cycle([incoming])), []);
  const intra = cycle([incoming], [{ type: 'finding', slug: 'finding-new', title: 'Finding', summary: 'Finding summary', body: 'Finding.', claims: [{ ...incoming, value: 3, observed_at: '' }] }]);
  assert((await validateCycle(env, intra)).some((p) => /inside cycle/.test(p.message)));
  db.close();
});

test('only a ready index at its recorded head can authorize writes', async () => {
  const { env, db } = database();
  const { indexedHeadSha } = loader()('write');
  db.prepare('INSERT INTO meta VALUES (?,?)').run('head_sha', 'a'.repeat(40));
  assert.equal(await indexedHeadSha(env), 'a'.repeat(40));
  db.prepare('INSERT INTO meta VALUES (?,?)').run('index_state', 'building');
  assert.equal(await indexedHeadSha(env), null);
  db.prepare("UPDATE meta SET v='ready' WHERE k='index_state'").run();
  assert.equal(await indexedHeadSha(env), 'a'.repeat(40));
  db.close();
});

test('collision checks use the final server-dated log slug', async()=>{
  const {env,db}=database();const {validateCycle,todayUtc}=loader()('write');
  try {
    db.prepare('INSERT INTO pages VALUES (?,?)').run(`${todayUtc()}-cycle-new`,'{}');
    const c=cycle();c.log.slug='2000-01-01-cycle-new';
    assert((await validateCycle(env,c)).some(p=>p.where===`${todayUtc()}-cycle-new` && /already exists/.test(p.message)));
  } finally {db.close();}
});

test('an abandoned proposal cannot be merged by later polling',async()=>{
  const {env,db}=database();
  try {
    const load=loader({'./github':{checkState:async()=>{throw new Error('must not reach GitHub');}}});
    await load('write').ensureTables(env);
    db.prepare('INSERT INTO proposals(id,branch,sha,status,created_at,updated_at) VALUES(?,?,?,?,?,?)').run('gone','agent/gone','a','abandoned','now','now');
    assert.equal((await load('propose').checkProposal(env,'gone')).status,'abandoned');
  }finally{db.close();}
});

test('one check call atomically claims a proposal lock', async () => {
  const { env, db } = database();
  const { ensureTables, acquireLock, claimLock } = loader()('write');
  await ensureTables(env);
  assert.equal(await acquireLock(env, 'proposal'), true);
  assert.equal(await claimLock(env, 'proposal', 'check-a'), true);
  assert.equal(await claimLock(env, 'proposal', 'check-b'), false);
  db.close();
});

test('checkState accepts only exact validate.yml run and treats skipped or neutral as failure', async () => {
  const originalFetch = global.fetch;
  try {
    let runs = [];
    global.fetch = async () => new Response(JSON.stringify({ workflow_runs: runs }), { status: 200 });
    const { checkState } = loader()('github');
    const base = { head_sha: 'abc', head_branch: 'agent/x', status: 'completed', html_url: 'https://ci' };
    runs = [{ ...base, path: '.github/workflows/validate.yml.old', conclusion: 'success' }];
    assert.equal((await checkState({ GITHUB_TOKEN: 'x', GITHUB_REPO: 'o/r' }, 'abc', 'agent/x')).status, 'none');
    for (const conclusion of ['skipped', 'neutral']) {
      runs = [{ ...base, path: '.github/workflows/validate.yml', conclusion }];
      const state = await checkState({ GITHUB_TOKEN: 'x', GITHUB_REPO: 'o/r' }, 'abc', 'agent/x');
      assert.equal(state.status, 'completed'); assert.equal(state.conclusion, conclusion);
    }
  } finally { global.fetch = originalFetch; }
});

test('OAuth write authority requires explicit requested scope and checkbox', async () => {
  const { handleAuthorize } = loader()('auth');
  for (const allow of [false, true]) {
    let completed;
    const authReq = { clientId: 'client', redirectUri: 'https://client/cb', scope: ['wiki.read', 'wiki.write'] };
    const env = {
      WIKI_PASSPHRASE: 'secret', OAUTH_KV: { get: async () => null, put: async () => {} },
      OAUTH_PROVIDER: {
        lookupClient: async () => ({ clientName: 'Client' }),
        completeAuthorization: async (input) => { completed = input; return { redirectTo: 'https://client/done' }; },
      },
    };
    const form = new URLSearchParams({ req: btoa(JSON.stringify(authReq)), passphrase: 'secret' });
    if (allow) form.set('allow_write', 'yes');
    const response = await handleAuthorize(new Request('https://wiki/authorize', { method: 'POST', body: form }), env);
    assert.equal(response.status, 302);
    assert.equal(completed.props.authVersion, 2);
    assert.equal(completed.props.canWrite, allow);
    assert.equal(completed.props.grantedScopes.includes('wiki.write'), allow);
  }
});

test('merge is recorded before best-effort branch cleanup and already-moved main is reconciled', async () => {
  for (const alreadyMoved of [false, true, 'descendant']) {
    const { env, db } = database();
    const base = 'a'.repeat(40), head = 'b'.repeat(40), id = `proposal-${alreadyMoved}`;
    db.prepare('INSERT INTO meta VALUES (?,?)').run('head_sha', base);
    db.prepare('INSERT INTO meta VALUES (?,?)').run('index_state', 'ready');
    let main = alreadyMoved === 'descendant' ? 'c'.repeat(40) : alreadyMoved ? head : base;
    const gh = {
      GitHubError: class extends Error {},
      checkState: async () => ({ status: 'completed', conclusion: 'success', url: 'https://ci' }),
      commitParentSha: async () => base,
      containsCommit: async () => alreadyMoved === 'descendant',
      headSha: async (_env, branch) => branch === 'main' ? main : head,
      fastForwardBranch: async () => { main = head; return head; },
      deleteBranch: async () => { throw new Error('cleanup denied'); },
    };
    const { checkProposal } = loader({ './github': gh })('propose');
    await env.DB.batch([
      env.DB.prepare(`CREATE TABLE locks(name TEXT PRIMARY KEY,holder TEXT NOT NULL,expires_at INTEGER NOT NULL)`),
      env.DB.prepare(`CREATE TABLE proposals(id TEXT PRIMARY KEY,branch TEXT,sha TEXT,status TEXT,errors TEXT,merged_sha TEXT,created_at TEXT,updated_at TEXT,summary TEXT)`),
    ]);
    db.prepare('INSERT INTO locks VALUES (?,?,?)').run('merge', id, Date.now() + 60000);
    db.prepare('INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)').run(id, 'agent/x', head, 'queued', null, null, 'now', 'now', 'test');
    const result = await checkProposal(env, id);
    assert.equal(result.status, 'merged'); assert.equal(result.merged_sha, head);
    const stored = db.prepare('SELECT status,merged_sha FROM proposals WHERE id=?').get(id);
    assert.deepEqual({...stored}, { status: 'merged', merged_sha: head });
    db.close();
  }
});
