import test from "node:test";
import assert from "node:assert/strict";
import {requestApi} from "../api.js";

test("304 reuses an isolated copy and credentials never share cached notes", async () => {
  const seen=[];
  const fetcher=async (_url,options)=>{
    seen.push(options.headers);
    return options.headers["If-None-Match"]
      ? new Response(null,{status:304})
      : Response.json({notes:[{title:options.headers.Authorization}]},{headers:{ETag:'"v1"'}});
  };
  const first=await requestApi("/api/notes",{token:"cache-a",fetcher});
  first.notes[0].title="mutated outside cache";
  assert.equal((await requestApi("/api/notes",{token:"cache-a",fetcher})).notes[0].title,"Bearer cache-a");
  assert.equal(seen[1]["If-None-Match"],'"v1"');
  assert.equal((await requestApi("/api/notes",{token:"cache-b",fetcher})).notes[0].title,"Bearer cache-b");
  assert.equal(seen[2]["If-None-Match"],undefined);
});

test("a successful mutation invalidates library reads; an old server needs no ETag",async()=>{
  const seen=[];
  const fetcher=async (_url,options)=>{seen.push(options.headers);return Response.json({notes:[]},{headers:{ETag:'"before-write"'}});};
  await requestApi("/api/notes",{token:"cache-write",fetcher});
  await requestApi("/api/captures",{token:"cache-write",method:"POST",body:{text:"New"},fetcher});
  await requestApi("/api/notes",{token:"cache-write",fetcher});
  assert.equal(seen[2]["If-None-Match"],undefined);
  assert.deepEqual(await requestApi("/api/dashboard",{token:"old-server",fetcher:async()=>Response.json({today:[]})}),{today:[]});
});
