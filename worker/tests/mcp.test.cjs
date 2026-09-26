const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { DatabaseSync } = require('node:sqlite');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { InMemoryTransport } = require('@modelcontextprotocol/sdk/inMemory.js');
const cache = new Map();
function load(name) {
  if(cache.has(name)) return cache.get(name);
  const file=path.join(__dirname,'../src',name+'.ts');
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  const out={};cache.set(name,out);
  new Function('exports','require',code)(out,(dep)=>{
    if(dep.startsWith('./') && dep.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(__dirname,'../src',dep)));
    if(dep.startsWith('./')) return load(dep.slice(2));
    return require(dep);
  });return out;
}
function fixtureDb() {
  const db=new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);
    CREATE TABLE pages(slug TEXT PRIMARY KEY,type TEXT,title TEXT,summary TEXT,status TEXT,author TEXT,created TEXT,updated TEXT,page_date TEXT,rel_path TEXT,frontmatter TEXT);
    CREATE TABLE claims(subject TEXT,metric TEXT,qualifier TEXT,value TEXT,as_of TEXT,page TEXT);
    CREATE TABLE chunks(id INTEGER PRIMARY KEY,slug TEXT,ord INTEGER,heading TEXT,text TEXT);
    CREATE TABLE links(src TEXT,dst TEXT,kind TEXT); CREATE TABLE threads(page TEXT,date TEXT,question TEXT,next TEXT);`);
  const records=JSON.parse(fs.readFileSync(path.join(__dirname,'../../tests/fixtures/measurement-cases.json'))).records;
  const groups=new Map();for(const r of records){const rows=groups.get(r.source)??[];rows.push(r);groups.set(r.source,rows);}
  for(const [slug,rows] of groups){
    const fm={claims:rows,review_after:rows[0].review_after};
    db.prepare('INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?)').run(slug,'finding',slug,slug,rows[0].page_status??'current','test','2026-09-01','2026-09-01','2026-09-01',`wiki/findings/${slug}.md`,JSON.stringify(fm));
    for(const r of rows)db.prepare('INSERT INTO claims VALUES(?,?,?,?,?,?)').run(r.subject,r.metric,r.qualifier??'',String(r.value),r.as_of,slug);
  }
  return {db,env:{DB:{prepare(sql){const make=(args=[])=>({bind:(...a)=>make(a),
    all:async()=>({results:db.prepare(sql).all(...args)}),
    first:async()=>db.prepare(sql).get(...args)??null,
    run:async()=>({meta:db.prepare(sql).run(...args)})});return make();}}}};
}

test('remote client gets contract, version-specific state, history and a reindex refusal',async()=>{
  const {createServer}=load('mcp');const {env,db}=fixtureDb();
  const server=createServer(env,{canWrite:false});
  const client=new Client({name:'offline-integration-test',version:'1'});
  const [ct,st]=InMemoryTransport.createLinkedPair();
  await server.connect(st);await client.connect(ct);
  const call=async(name,args={})=>client.callTool({name,arguments:args});
  try {
    const tools=await client.listTools();assert(tools.tools.some(t=>t.name==='get_working_context'));
    assert(!tools.tools.some(t=>t.name==='propose_cycle'));
    const context=await call('get_working_context',{document:'AGENTS'});
    assert.match(JSON.parse(context.content[0].text).text,/source of truth/);
    const current=JSON.parse((await call('get_state',{subject:'model-a',metric:'bench-index'})).content[0].text);
    assert.deepEqual(current.matched.map(x=>x.value),[49]);
    assert.match(current.matched[0].source_url,/wiki\/findings\/new-score.md$/);
    const history=JSON.parse((await call('get_state',{subject:'model-a',metric:'bench-index',version:'4.1.1'})).content[0].text);
    assert.deepEqual(history.matched.map(x=>x.value),[61]);
    const missing=JSON.parse((await call('get_refresh_queue',{subject:'model-b',metric:'bench-index'})).content[0].text);
    assert.equal(missing.missing_current[0].expected_version,'4.3');
    db.exec("INSERT INTO meta VALUES('index_state','building')");
    const blocked=await call('get_state',{subject:'model-a'});assert.equal(blocked.isError,true);
    assert.match(blocked.content[0].text,/rebuilding/);
  } finally {await client.close();await server.close();db.close();}
});

test('two observations on one page retain the newer value and dated evidence',async()=>{
  const {env,db}=fixtureDb();
  try {
    const a={subject:'same-page',metric:'bench-index-v4.3',value:40,as_of:'2026-09-07',observed_at:'2026-09-07T08:00:00Z',source_url:'https://example.com/am'};
    const b={...a,value:42,observed_at:'2026-09-07T12:00:00Z',source_url:'https://example.com/pm'};
    db.prepare('INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?)').run('revisions','finding','Revisions','','current','test','2026-09-07','2026-09-07','2026-09-07','wiki/findings/revisions.md',JSON.stringify({claims:[a,b]}));
    for(const r of [a,b])db.prepare('INSERT INTO claims VALUES(?,?,?,?,?,?)').run(r.subject,r.metric,'',String(r.value),r.as_of,'revisions');
    const result=await load('state').getState(env,'same-page','bench-index');
    assert.equal(result.matched[0].value,42);
    assert.equal(result.matched[0].earlier[0].value,40);
    assert.equal(result.matched[0].earlier[0].observed_at,a.observed_at);
    assert.equal(result.matched[0].earlier[0].evidence_url,a.source_url);
  } finally { db.close(); }
});
