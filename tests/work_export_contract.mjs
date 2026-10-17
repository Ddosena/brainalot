// Cross-language integration: actual B exporter -> actual A FastAPI -> verified bytes.
// The transport is stdio to TestClient, so no shared port or user profile is touched.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import assert from 'node:assert/strict';
import { prepareCommit, verifyCommit } from '../extension/work-sync.js';

const child = spawn(process.argv[2], ['-u', '-c', `
import sys,json,base64
from fastapi.testclient import TestClient
from megamozg.api import create_app
from megamozg.core import Store
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
  const prepared = await prepareCommit(api, state, images);
  const command = { request_id: 'migration-from-browser', expected_revision: 0, ...prepared };
  const first = await api('/api/work/commit', 'POST', command);
  const retry = await api('/api/work/commit', 'POST', command);
  assert.deepEqual(retry, first);
  const verified = await verifyCommit(api, readImage, first, state, images, new Map(prepared.images.map(x => [x.id, x.blob])));
  assert.deepEqual(verified.state, state);
  await assert.rejects(api('/api/work/commit', 'POST', { ...command, request_id: 'stale-client' }), error => error.status === 409);
  console.log(JSON.stringify({ verified: true, requests: sequence, images: verified.images.length, stateBytes: JSON.stringify(state).length, revision: verified.revision }));
} finally {
  child.stdin.end();
}
