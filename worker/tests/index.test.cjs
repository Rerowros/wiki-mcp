const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const {DatabaseSync}=require('node:sqlite');
const out={};
new Function('exports',ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/index-update.ts'),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(out);
function fixture(){
  const db=new DatabaseSync(':memory:');
  db.exec("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT); CREATE TABLE data(value TEXT); INSERT INTO meta VALUES('head_sha','"+'a'.repeat(40)+"')");
  let beforeBatch;
  const wrap=(sql,args=[])=>({sql,args,bind:(...a)=>wrap(sql,a),
    first:async()=>db.prepare(sql).get(...args)??null,
    all:async()=>({results:db.prepare(sql).all(...args)}),
    run:async()=>({meta:db.prepare(sql).run(...args)})});
  const env={ADMIN_TOKEN:'test',DB:{prepare:wrap,batch:async statements=>{
    if(beforeBatch){const hook=beforeBatch;beforeBatch=null;hook(statements);}
    db.exec('BEGIN');
    try{const result=statements.map(s=>{const st=db.prepare(s.sql);st.setReadBigInts(true);return {meta:st.run(...s.args)};});db.exec('COMMIT');return result;}
    catch(e){db.exec('ROLLBACK');throw e;}
  }}};
  const call=async payload=>{
    const res=await out.handleReindex(new Request('https://wiki/admin/reindex',{
      method:'POST',headers:{authorization:'Bearer test'},body:JSON.stringify(payload)}),env);
    return {status:res.status,...await res.json()};
  };
  const start=(run_id='one',mode='full',count=1)=>({operation:'start',run_id,mode,
    target_sha:'b'.repeat(40),base_sha:'a'.repeat(40),expected_batches:Math.ceil(count/32),expected_statements:count});
  const finish=(run_id='one',count=1)=>({operation:'finish',run_id,target_sha:'b'.repeat(40),expected_batches:Math.ceil(count/32),expected_statements:count});
  const meta=k=>db.prepare('SELECT v FROM meta WHERE k=?').get(k)?.v;
  return {db,call,start,finish,meta,env,hook(fn){beforeBatch=fn;}};
}
test('index is unreadable until all batches finish; wrong owner and duplicate batch do not mutate',async()=>{
  const f=fixture();try{
    assert((await f.call(f.start())).ok);
    assert.equal(f.meta('index_state'),'building');
    assert.equal((await f.call(f.finish())).status,409);
    const batch={operation:'batch',run_id:'other',batch_index:0,statements:["INSERT INTO data VALUES('right')"]};
    assert.equal((await f.call(batch)).status,409);
    assert.equal(f.db.prepare('SELECT count(*) n FROM data').get().n,0);
    batch.run_id='one';assert((await f.call(batch)).ok);
    assert.equal((await f.call(batch)).status,409);
    assert.equal(f.meta('index_state'),'building');
    assert((await f.call(f.finish())).ok);
    assert.equal(f.meta('head_sha'),'b'.repeat(40));assert.equal(f.meta('index_state'),'ready');
    assert.equal(f.meta('index_epoch'),'one');
    assert.equal(f.db.prepare('SELECT count(*) n FROM data').get().n,1);
  }finally{f.db.close();}
});
test('failed SQL rolls back its complete batch and requires full recovery',async()=>{
  const f=fixture();try{
    assert((await f.call(f.start('one','full',2))).ok);
    const failed=await f.call({operation:'batch',run_id:'one',batch_index:0,statements:["INSERT INTO data VALUES('partial')","INSERT INTO missing VALUES(1)"]});
    assert.equal(failed.ok,false);assert.equal(f.meta('index_state'),'failed');
    assert.equal(f.meta('head_sha'),'a'.repeat(40));assert.equal(f.db.prepare('SELECT count(*) n FROM data').get().n,0);
    assert((await f.call({operation:'abort',run_id:'one'})).ok);
    assert.equal((await f.call(f.start('two','incremental'))).status,409);
    assert((await f.call(f.start('two','full'))).ok);
    assert((await f.call({operation:'batch',run_id:'two',batch_index:0,statements:["INSERT INTO data VALUES('recovered')"]})).ok);
    assert((await f.call(f.finish('two'))).ok);
  }finally{f.db.close();}
});
test('live lease cannot be stolen or aborted; expired run cannot finish a replacement',async()=>{
  const f=fixture();try{
    assert((await f.call(f.start())).ok);
    assert.equal((await f.call(f.start('two'))).status,409);
    assert.equal((await f.call({operation:'abort',run_id:'one'})).status,409);
    assert((await f.call({operation:'heartbeat',run_id:'one'})).ok);
    f.db.exec('UPDATE index_rebuild_lock SET lease_until=0');
    assert((await f.call(f.start('two'))).ok);
    assert.equal((await f.call(f.finish('one'))).status,409);
    assert.equal(f.meta('index_run_id'),'two');assert.equal(f.meta('index_state'),'building');
  }finally{f.db.close();}
});
test('incremental base check is enforced inside the mutation transaction',async()=>{
  const f=fixture();try{
    // Inject a competing publisher after preliminary reads, just before start's transaction.
    const original=f.env.DB.batch;
    f.env.DB.batch=async statements=>{
      if(statements.some(s=>s.sql.includes('INSERT INTO index_rebuild_lock')))
        f.db.prepare("UPDATE meta SET v=? WHERE k='head_sha'").run('c'.repeat(40));
      return original(statements);
    };
    assert.equal((await f.call(f.start('one','incremental'))).status,409);
    assert.equal(f.meta('head_sha'),'c'.repeat(40));
    assert.equal(f.meta('index_state'),undefined);
    assert.equal(f.db.prepare('SELECT count(*) n FROM index_rebuild_lock').get().n,0);
  }finally{f.db.close();}
});
test('protocol rejects unauthenticated writes and oversized batches',async()=>{
  const f=fixture();try{
    const denied=await out.handleReindex(new Request('https://wiki/admin/reindex',{method:'POST',body:'{}'}),f.env);
    assert.equal(denied.status,401);
    assert((await f.call(f.start('one','full',33))).ok);
    assert.equal((await f.call({operation:'batch',run_id:'one',batch_index:0,statements:Array(33).fill('SELECT 1')})).status,400);
  }finally{f.db.close();}
});

test('complete real vault exporter loads through the Worker protocol without chunk collisions',async()=>{
  const {execFileSync}=require('node:child_process');
  const root=path.join(__dirname,'../..');
  const script="import sys,json; sys.path.insert(0,'tools'); import export_index as E; import wikilib as W; p=W.load_pages(); s=[x.strip()+';' for x in E.SCHEMA.split(';') if x.strip()]; s += [q for slug in sorted(p) for q in E.page_rows(p[slug])]; print(json.dumps({'statements':s,'pages':len(p),'claims':sum(len(x.fm.get('claims') or []) for x in p.values())}))";
  const exported=JSON.parse(execFileSync('python',['-c',script],{cwd:root,encoding:'utf8',maxBuffer:40*1024*1024}));
  const f=fixture();try{
    const statements=exported.statements;
    assert((await f.call(f.start('real-vault','full',statements.length))).ok);
    for(let i=0;i<statements.length;i+=32){
      const result=await f.call({operation:'batch',run_id:'real-vault',batch_index:i/32,statements:statements.slice(i,i+32)});
      assert.equal(result.ok,true,`batch ${i/32}: ${result.error}`);
    }
    assert((await f.call(f.finish('real-vault',statements.length))).ok);
    assert.equal(f.db.prepare('SELECT count(*) n FROM pages').get().n,exported.pages);
    assert.equal(f.db.prepare('SELECT count(*) n FROM claims').get().n,exported.claims);
    assert.equal(f.db.prepare('SELECT count(*) n FROM chunks').get().n,f.db.prepare('SELECT count(*) n FROM chunks_fts').get().n);
    assert.equal(f.db.prepare('SELECT count(*) n FROM chunks c JOIN chunks_fts f ON f.rowid=c.id WHERE c.text<>f.text').get().n,0);
  }finally{f.db.close();}
});
