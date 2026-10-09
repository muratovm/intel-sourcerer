const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/intel_sourcerer/ui/workspace/temporary.js', 'utf8');
const storage = new Map();
const payload = {id:'test-session',articles:[{url:'https://example.com/1',notes:'',notes_revision:0,is_read:false},{url:'https://example.com/2',notes:'',notes_revision:0,is_read:false}]};
let network = 0;
let clockTime = Date.now();
class TestDate extends Date {
  constructor(...args) { super(...(args.length ? args : [clockTime])); }
  static now() { return clockTime; }
}
function reader(guest=false) {
  const window = {fetch:async (resource,options) => { network++; if(resource === '/temporary-session.json') return new Response(JSON.stringify(payload)); assert(['/temporary/parse','/guest/prepare'].includes(resource)); const records=JSON.parse(options.body).articles; return new Response(JSON.stringify({articles:records.map(record=>({body_html:"<p>Some research words</p>",notes:"",notes_format:"plain",notes_revision:0,is_read:false,...record,source:record}))})); }};
  const context = {Date:TestDate,crypto:{randomUUID:()=> 'empty-guest-session'},window,URL,Response,location:{href:'http://localhost/',origin:'http://localhost',pathname:guest?'/guest/read':'/'},sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},document:{querySelector:()=>({content:"guest"}),addEventListener:()=>{},createElement:()=>({innerHTML:'',get textContent(){return this.innerHTML.replace(/<[^>]*>/g,'')},querySelectorAll:()=>[]})}};
  vm.runInNewContext(source,context);
  return async (path,data) => {const response=await window.fetch(path,data?{body:JSON.stringify(data)}:{});return {status:response.status,data:await response.json()};};
}
(async()=>{
  let api=reader();
  let state=(await api('/api/state')).data;
  assert.equal(state.queue.items.length,2);
  assert.equal((await api('/api/notes',{url:payload.articles[0].url,text:'<p>note</p>',revision:0})).data.revision,1);
  assert.equal((await api('/api/notes',{url:payload.articles[0].url,text:'stale',revision:0})).status,400);
  let queue=(await api('/api/navigate',{queue_id:state.queue.id,cursor:0,action:'mark_next'})).data;
  assert.equal(queue.cursor,1);assert.equal(queue.read_count,1);
  payload.id='server-restarted';
  api=reader(); // Refresh restores this tab's temporary session.
  state=(await api('/api/state')).data;
  assert.equal(state.queue.cursor,1);assert.equal(state.articles[0].notes,'<p>note</p>');
  assert.equal((await api('/api/discover',{days:30})).status,400);
  assert.equal((await api('/api/navigate',{queue_id:state.queue.id,cursor:1,action:'next'})).data.finished,true);
  assert.equal(network,4); // Only the immutable input payload used HTTP.
  assert.equal((await api('/api/link',{url:'https://example.com/new'})).status,200);
  api=reader();
  state=(await api('/api/state')).data;
  assert.equal(state.articles.length,3);
  assert.equal(state.articles[0].notes,'<p>note</p>');
  assert.equal(network,7);
  storage.set('intel-guest-input',JSON.stringify({...payload,id:'guest-session'}));
  api=reader(true);
  state=(await api('/api/state')).data;
  assert.equal(state.queue.items.length,2);
  assert.equal(state.queue.id,'guest-session');
  assert.equal(network,8); // Guest inputs used the tab, never an HTTP backend.
  storage.delete('intel-guest-input');
  api=reader(true);
  state=(await api('/api/state')).data;
  assert.equal(state.articles.length,0);
  assert.equal(state.queue.items.length,0);
  assert(storage.has('intel-guest-input'));
  assert.equal((await api('/api/link',{url:'https://example.com/guest-new'})).status,200);
  api=reader(true);
  assert.equal((await api('/api/state')).data.articles.length,1);
  assert.equal((await api('/api/timer')).data.session,null);
  assert.equal((await api('/api/timer',{action:'start',url:'https://example.com/guest-new',target_seconds:5})).status,400);
  let timed=(await api('/api/timer',{action:'start',url:'https://example.com/guest-new',target_seconds:60})).data.session;
  assert.equal(timed.status,'running');
  assert.equal(timed.word_count,3);
  clockTime+=10000;
  timed=(await api('/api/timer',{action:'pause',id:timed.id})).data.session;
  assert.equal(timed.elapsed_seconds,10);
  clockTime+=30000;
  api=reader(true);
  timed=(await api('/api/timer')).data.session;
  assert.equal(timed.status,'paused');
  assert.equal(timed.elapsed_seconds,10);
  timed=(await api('/api/timer',{action:'resume',id:timed.id})).data.session;
  clockTime+=15000;
  api=reader(true);
  assert.equal((await api('/api/timer')).data.session.elapsed_seconds,25);
  timed=(await api('/api/timer',{action:'finish',id:timed.id})).data.session;
  assert.equal(timed.status,'completed');
  assert.equal(timed.elapsed_seconds,25);
  assert.equal((await api('/api/state')).data.articles[0].is_read,true);
  assert.equal((await api('/api/timer',{action:'finish',id:timed.id})).data.session.elapsed_seconds,25);
  assert.equal((await api('/api/timer')).data.session,null);
  assert.equal((await api('/api/delete-article',{url:'https://example.com/guest-new'})).status,200);
  api=reader(true);
  state=(await api('/api/state')).data;
  assert.equal(state.articles.length,0);
  assert.equal(state.queue.items.length,0);
  assert.equal((await api('/api/delete-article',{url:'https://example.com/missing'})).status,400);
  console.log('Temporary notes, queue navigation, refresh and no-backend checks passed');
})().catch(error=>{console.error(error);process.exit(1)});
