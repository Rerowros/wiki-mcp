const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function load(name) {
  const source = fs.readFileSync(path.join(__dirname,'../src',name+'.ts'),'utf8');
  const code = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const out = {}; new Function('exports','require',code)(out,require); return out;
}
const {resolveMeasurements} = load('measurements');
const {readSnapshot} = load('read-snapshot');
const {pageUrl,fetchPage} = load('search');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname,'../../tests/fixtures/measurement-cases.json')));
for (const item of fixture.cases) test(`measurement ${item.subject} ${item.metric} ${item.version ?? 'active'}`,()=>{
  const r=resolveMeasurements(fixture.records,item.subject,item.metric,item.version,true,fixture.today);
  assert.deepEqual(r.matched.map(x=>x.value),item.values);
  assert.equal(r.missing_current.length,item.missing);
  assert.equal(r.historical_count,item.historical);
});
test('within-day observations preserve earlier evidence',()=>{
  const r=resolveMeasurements(fixture.records,'model-e','bench-index',undefined,true,fixture.today);
  assert.equal(r.matched[0].value,41);
  assert.equal(r.matched[0].earlier[0].value,40);
});
test('evidence URLs use real nested paths and escape path components',()=>{
  assert.equal(pageUrl('wiki/findings/a.md','owner/repo'),'https://github.com/owner/repo/blob/main/wiki/findings/a.md');
  assert.equal(pageUrl('wiki/findings/a.md'),'https://github.com/your-org/wiki-mcp/blob/main/wiki/findings/a.md');
  assert.match(pageUrl('wiki/assets/a b.png'),/a%20b.png$/);
});
function envWithGenerations(generations) {
  let n=0;return {DB:{prepare:()=>({all:async()=>({results:Object.entries(generations[Math.min(n++,generations.length-1)])
    .map(([k,v])=>({k,v}))})})}};
}
test('read refuses partial index before touching content',async()=>{
  let read=false;
  await assert.rejects(readSnapshot(envWithGenerations([{index_state:'building'}]),async()=>{read=true;}),/rebuilding/);
  assert.equal(read,false);
});
test('read discards results if generation changed, including same-head rebuild',async()=>{
  await assert.rejects(readSnapshot(envWithGenerations([{index_state:'ready',index_epoch:'a'},{index_state:'ready',index_epoch:'b'}]),async()=>['mixed']),/changed/);
});
test('legacy ready index and stable generation are readable',async()=>{
  assert.equal(await readSnapshot(envWithGenerations([{}]),async()=>42),42);
});
test('long first section can be read in bounded continuations without omission',async()=>{
  const raw='x'.repeat(25001);
  const env={DB:{prepare:()=>({bind:()=>({first:async()=>({slug:'long',rel_path:'wiki/findings/long.md',frontmatter:'{}'}),
    all:async()=>({results:[{ord:0,heading:'',text:raw}]})})})}};
  const a=await fetchPage(env,'long'), b=await fetchPage(env,'long',false,a.next_offset),c=await fetchPage(env,'long',false,b.next_offset);
  assert.equal(a.text.length,12000);assert.equal(c.next_offset,null);
  assert.equal(a.text+b.text+c.text,raw);
});
