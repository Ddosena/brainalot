import {test} from 'node:test';
import assert from 'node:assert/strict';

function storage(){
  const stores=new Map([['kv',new Map()],['images',new Map()],['canonical-images',new Map()]]);
  const database={objectStoreNames:{contains:name=>stores.has(name)},transaction(){
    const transaction={pending:0,finished:false};
    const finish=()=>setImmediate(()=>{if(!transaction.pending&&!transaction.finished){transaction.finished=true;transaction.oncomplete?.();}});
    transaction.objectStore=name=>{
      const values=stores.get(name),request=operation=>{const result={};transaction.pending++;setImmediate(()=>{try{result.result=operation();result.onsuccess?.();}catch(error){transaction.error=error;transaction.onerror?.();}transaction.pending--;finish();});return result;};
      return {get:key=>request(()=>values.get(key)),getAllKeys:()=>request(()=>[...values.keys()]),getAll:()=>request(()=>[...values.values()]),put:(value,key)=>request(()=>values.set(key,value)),delete:key=>request(()=>values.delete(key)),clear:()=>request(()=>values.clear())};
    };finish();return transaction;
  }};
  globalThis.indexedDB={open(){const request={result:database};setImmediate(()=>request.onsuccess?.());return request;}};
  globalThis.localStorage={getItem(){return null;},setItem(){},removeItem(){}};
  globalThis.document={addEventListener(){}};globalThis.addEventListener=()=>{};globalThis.BroadcastChannel=class{postMessage(){}};
  return stores;
}

test('same-revision pending freezes writes, GET recovery replays only the durable original delete, conflict unlocks',async()=>{
  const stores=storage(),calls=[],events=[];
  let revision=7,pending=[],conflicts=[],prepared=null,receipt=null,boardPending=false,recovery='pending';
  const state={version:1,projects:[],items:[],boards:[],view:{},planning:{constructor:{cubes:{cube:{id:'cube',kind:'brainalot',source_ref:'note'}}},completed_cubes:{}}};
  const row=status=>({request_id:prepared?.request_id||'foreign',cube_id:'cube',note_id:'note',status,error:null});
  const snapshot=()=>({initialized:true,revision,state:structuredClone(state),images:[],deletion_state:{pending:structuredClone(pending),conflicts:structuredClone(conflicts)}});
  const server={readImage(){assert.fail('no images');},async api(path,method='GET',body){
    if(path==='/api/work/state'){
      if(pending.length&&recovery==='complete'){
        state.planning.cube_deletions={cube:{cube_id:'cube',request_id:prepared.request_id}};revision++;pending=[];
        receipt={...snapshot(),delete_result:{...row('complete'),operation_id:'journal-operation'}};
      }else if(pending.length&&recovery==='conflict'){pending=[];conflicts=[row('conflict')];}
      return snapshot();
    }
    if(path.startsWith('/api/work/blobs/'))return {blob:path.split('/')[4],complete:true,total:body.total,received:body.index+1,size:Buffer.from(body.content_base64,'base64').length};
    if(path==='/api/work/commit'&&boardPending){pending=[row('pending')];throw Object.assign(new Error('Foreign delete pending'),{status:503,delete_result:pending[0]});}
    assert.equal(path,'/api/work/planning');assert.equal(method,'POST');calls.push(structuredClone(body));
    if(receipt){assert.deepEqual(body,prepared);return structuredClone(receipt);}
    if(conflicts.length)throw Object.assign(new Error('Note conflict'),{status:409,delete_result:conflicts[0]});
    prepared=structuredClone(body);pending=[row('pending')];throw Object.assign(new Error('Pending delete'),{status:503,delete_result:pending[0]});
  }};
  const store=await import('../work-store.js?delete-store='+Date.now());store.setWorkTransport(server);store.subscribe(meta=>events.push(meta.from));await store.load();
  pending=[row('pending')];await store.refreshRemote();assert.equal(store.getRevision(),7);assert.equal(store.isWritable(),false);assert.ok(events.includes('deletion'));
  assert.throws(()=>store.update(value=>value.items.push({id:'forbidden'})),/приостановлены/);assert.deepEqual(store.get().items,[]);
  pending=[];await store.refreshRemote();assert.equal(store.isWritable(),true);
  await assert.rejects(store.planningAction('cube_delete',{cube_id:'cube',expected_note_version:'v1'}),{status:503});
  assert.equal(store.getRevision(),7);assert.equal(store.isWritable(),false);assert.equal((await store.getPendingPlanningDraft()).action,'cube_delete');
  await assert.rejects(store.discardPlanningDraft(),/Статус удаления/);assert.equal(calls.length,1);
  recovery='complete';await store.refreshRemote();assert.equal(store.getRevision(),8);assert.equal(store.isWritable(),true);assert.equal(await store.getPendingPlanningDraft(),null);assert.deepEqual(calls[0],calls[1]);assert.ok(store.get().planning.cube_deletions.cube);
  assert.equal([...stores.get('kv').keys()].filter(key=>key.startsWith('planning-draft:')).length,0);
  // A separate failed operation keeps the same Work revision and releases its local draft.
  receipt=null;prepared=null;pending=[];conflicts=[];recovery='pending';
  await assert.rejects(store.planningAction('cube_delete',{cube_id:'cube',expected_note_version:'v2'}),{status:503});
  recovery='conflict';await store.refreshRemote();assert.equal(store.getRevision(),8);assert.equal(store.isWritable(),true);assert.equal(await store.getPendingPlanningDraft(),null);assert.equal(store.getDeletionState().conflicts[0].status,'conflict');
  assert.equal(calls.at(-1).data.expected_note_version,'v2','recovery never substitutes a newer note version');
  // A board draft blocked by a foreign delete must survive metadata recovery.
  boardPending=true;conflicts=[];recovery='pending';store.update(value=>value.items.push({id:'unsent-board-item'}));
  await assert.rejects(store.flush(),{status:503});assert.equal(store.getDeletionState().pending.length,1);
  recovery='complete';await store.refreshRemote();assert.equal(store.getDeletionState().pending.length,0);
  assert.equal(store.get().items.at(-1).id,'unsent-board-item');assert.equal(store.getRevision(),8,'metadata recovery must not adopt over an unresolved board draft');
});
