// Cross-language integration: production Work UI commands -> FastAPI, retaining legacy bytes.
// The transport is stdio to TestClient, so no shared port or user profile is touched.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createWorkTransport} from '../extension/work-ui/transport.js';
import {createCommands} from '../extension/work-ui/commands.js';

const child = spawn(process.argv[2], ['-u', '-c', `
import sys,json,base64
from fastapi.testclient import TestClient
from megamozg.api import create_app
from megamozg.core import Store
from megamozg import features
# Exercise the real API on a throwaway root; this does not bind a network port.
features.WORK_ENABLED=True
root=sys.argv[1]
client=TestClient(create_app(root),base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer '+Store(root).token()})
for line in sys.stdin:
    request=json.loads(line)
    response=client.request(request['method'],request['path'],json=request.get('body'))
    print(json.dumps({'id':request['id'],'status':response.status_code,'data':base64.b64encode(response.content).decode()}),flush=True)
`, process.argv[3]], { stdio: ['pipe', 'pipe', 'inherit'] });
const pending = new Map();
let sequence = 0;
createInterface({ input: child.stdout }).on('line', line => {
  const answer = JSON.parse(line);
  pending.get(answer.id)?.(answer); pending.delete(answer.id);
});
async function request(path, method = 'GET', body) {
  const id = ++sequence;
  const result = new Promise(resolve => pending.set(id, resolve));
  child.stdin.write(JSON.stringify({ id, path, method, body }) + '\n');
  const response = await result;
  const bytes = Buffer.from(response.data, 'base64');
  if (response.status >= 400) throw Object.assign(new Error(bytes.toString()), { status: response.status });
  return bytes;
}
const api = async (...args) => JSON.parse((await request(...args)).toString());
async function upload(bytes) {
  const digest=createHash('sha256').update(bytes).digest('hex'),size=131072,total=Math.max(1,Math.ceil(bytes.length/size));
  for(let index=0;index<total;index++)await api(`/api/work/blobs/${digest}/chunks`,'POST',{index,total,content_base64:bytes.subarray(index*size,(index+1)*size).toString('base64')});
  return digest;
}
const readImage = async id => {
  const data = await request(`/api/work/images/${encodeURIComponent(id)}`);
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
};
try {
  const state = { version: 1, projects: [{ id: 'p', name: 'Project', unknown: ['preserve'] }],
    items: [{ id: 't', title: 'Task', project: 'p', notes: 'full '.repeat(65000), checklist: [{ text: 'step', done: false }] }],
    boards: [{ id: 'general', items: [{ id: 'img', type: 'image', imageId: 'picture' }], view: { x: 40, y: 28, z: 1 } }],
    view: { days: 7 }, future: { retain: true } };
  const images = [['picture', new Blob([new Uint8Array(300000).fill(42)], { type: 'image/png' })],
    ['empty-orphan', new Blob([])], ['untyped-orphan', new Blob(['original bytes'])]];
  const prepared = {state_blob:await upload(Buffer.from(JSON.stringify(state))),images:[]};
  for(const [id,blob]of images)prepared.images.push({id,blob:await upload(Buffer.from(await blob.arrayBuffer())),mime:blob.type||'application/octet-stream'});
  const command = { request_id: 'migration-from-browser', expected_revision: 0, ...prepared };
  const first = await api('/api/work/commit', 'POST', command);
  const retry = await api('/api/work/commit', 'POST', command);
  assert.deepEqual(retry, first);
  assert.deepEqual((await api('/api/work/state')).state,state);
  const transport=createWorkTransport({api});await transport.refresh();const commands=createCommands(transport);
  await commands.execute({type:'plan.start'});
  await commands.execute({type:'project.create',payload:{name:'Owner project',color:'#f09300'}});
  const project=transport.model.projects.find(p=>p.title==='Owner project');assert.ok(project);
  await commands.execute({type:'goal.create',payload:{project_id:project.id,title:'Owner goal',planned_minutes:60,start_date:transport.model.today,end_date:transport.model.today}});
  const goal=transport.model.tasks.find(t=>t.title==='Owner goal');assert.ok(goal);
  await commands.execute({type:'cube.create',payload:{goal_id:goal.id,title:'Owner cube',duration_minutes:30,selected_date:transport.model.today,start_minute:600}});
  assert.equal(transport.model.cubes.find(c=>c.title==='Owner cube').start,'10:00');await commands.undo();assert.ok(!transport.model.cubes.some(c=>c.title==='Owner cube'));
  const verified=await api('/api/work/state');
  for(const key of['items','boards','view','future'])assert.deepEqual(verified.state[key],state[key]);
  assert.deepEqual(verified.state.projects.find(p=>p.id==='p'),state.projects[0]);
  for(const [id,blob]of images)assert.deepEqual(Buffer.from(await readImage(id)),Buffer.from(await blob.arrayBuffer()));
  await assert.rejects(api('/api/work/commit', 'POST', { ...command, request_id: 'stale-client' }), error => error.status === 409);
  console.log(JSON.stringify({ verified: true, requests: sequence, images: verified.images.length, stateBytes: JSON.stringify(state).length, revision: verified.revision,typedCommands:true }));
} finally {
  child.stdin.end();
}
