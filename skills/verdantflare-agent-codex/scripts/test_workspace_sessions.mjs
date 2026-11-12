import test from "node:test";
import assert from "node:assert/strict";
import {normalizePath, belongs, visible, readAllThreads, mergeRows, allowsTask, createStore}
  from "../resources/workspace-sessions/filter.mjs";

test("Windows path boundaries and canonical aliases", () => {
  const roots = ["C:\\项目\\Design", "D:/Games"];
  for (const cwd of ["c:/项目/design", "C:/项目/DESIGN/sub/../sub", "\\\\?\\C:\\项目\\Design\\sub", "d:/games/a b"])
    assert.ok(belongs(cwd, roots), cwd);
  for (const cwd of [null, "design", "C:/项目/design-extra", "C:/项目/design/../other", "D:/games2", "/home/design", "C:design", "C:/../../design"])
    assert.equal(belongs(cwd, roots), false, String(cwd));
  assert.ok(belongs("\\\\?\\UNC\\server\\share\\sub", ["//SERVER/share"]));
  assert.ok(belongs("C:/Real/sub", ["D:/Alias"], {"D:/Alias": "C:/Real"}));
  assert.ok(belongs("D:/Alias/sub", ["C:/Real"], {"D:/Alias/sub": "C:/Real/sub"}));
  assert.equal(normalizePath("C:/x/\0"), null);
  assert.equal(belongs("C:/Real", []), false);
  assert.equal(belongs("C:/repo-worktree", ["C:/repo"]), false);
});

test("host identity, unknown metadata and pending tasks", () => {
  const row = {hostId:"local", cwd:"C:/design", conversationId:"1"};
  assert.ok(visible(row, ["C:/design"]));
  for (const change of [{hostId:"remote"},{cwd:null},{ephemeral:true},{threadSource:"chatgpt_hidden"}])
    assert.equal(visible({...row,...change}, ["C:/design"]), false);
  assert.ok(allowsTask({kind:"local",conversation:row},["C:/design"]));
  assert.equal(allowsTask({kind:"remote",task:{cwd:"C:/design"}},["C:/design"]),false);
  assert.equal(allowsTask({kind:"local",pendingWorktree:{title:"design"}},["C:/design"]),false);
});

test("scan reaches project rows beyond all-foreign first pages", async () => {
  const calls=[];
  const rows=await readAllThreads(async (method,params)=>{
    calls.push(params.cursor);
    return params.cursor===null?{data:[{id:"foreign",cwd:"D:/games"}],nextCursor:"second"}:
      {data:[{id:"mine",cwd:"C:/design"}],nextCursor:null};
  });
  assert.deepEqual(calls,[null,"second"]);
  assert.equal(rows.length,2);
  assert.equal(rows.filter(r=>visible({...r,hostId:"local"},["C:/design"])).length,1);
});

test("cursor loops, malformed replies, failure and cancellation do not mean empty success", async () => {
  await assert.rejects(readAllThreads(async()=>({data:[],nextCursor:"loop"})),/Repeated/);
  await assert.rejects(readAllThreads(async()=>({data:null})),/Invalid/);
  await assert.rejects(readAllThreads(async()=>{throw Error("offline")}),/offline/);
  let called=false;
  assert.equal(await readAllThreads(async()=>{called=true},()=>true),null);
  assert.equal(called,false);
});

test("live state overrides scanned metadata and archive suppression wins", () => {
  const row={hostId:"local",conversationId:"1",cwd:"C:/design",updatedAt:1,title:"old"};
  const rows=mergeRows([row],[{...row,title:"new",updatedAt:2}], ["C:/design"],{});
  assert.equal(rows.length,1);assert.equal(rows[0].title,"new");
  assert.deepEqual(mergeRows([row],[],["C:/design"],{},new Set(["1"])),[]);
});

function fixture(send, canonicalize=async()=>({})) {
  const metadata=new Map(), suppressed=new Set();
  const manager={sendRequest:send,
    observeCatalogThreads:async threads=>threads.forEach(t=>metadata.set(t.id,{...t,hostId:"local",conversationId:t.id})),
    getThreadListSummary:async id=>metadata.get(id),
    isConversationSuppressedAfterArchive:async id=>suppressed.has(id)};
  return {store:createStore({manager,roots:["C:/design"],canonicalize}),suppressed};
}

test("store single flight, failure retains confirmed rows, retry and archive refresh", async () => {
  let failure=false,calls=0;
  const {store,suppressed}=fixture(async()=>{calls++;if(failure)throw Error("offline");return{data:[{id:"1",cwd:"C:/design"}],nextCursor:null}});
  await Promise.all([store.refresh(),store.refresh()]);assert.equal(calls,1);
  assert.equal(store.snapshot().rows.length,1);
  failure=true;await store.refresh();assert.equal(store.snapshot().error,true);assert.equal(store.snapshot().rows.length,1);
  failure=false;await store.refresh();assert.equal(store.snapshot().error,false);
  suppressed.add("1");await store.updateSuppression();assert.ok(store.snapshot().suppressed.has("1"));
  store.dispose();
});

test("disposed workspace ignores a late response", async () => {
  let finish;
  const {store}=fixture(()=>new Promise(resolve=>{finish=resolve}));
  const task=store.refresh();store.dispose();
  finish({data:[{id:"1",cwd:"C:/design"}],nextCursor:null});await task;
  assert.equal(store.snapshot().rows.length,0);
});

test("canonicalization failure is visible and does not publish global rows", async () => {
  const {store}=fixture(async()=>({data:[{id:"1",cwd:"D:/alias"}],nextCursor:null}),async()=>{throw Error("unavailable")});
  await store.refresh();assert.equal(store.snapshot().error,true);assert.deepEqual(store.snapshot().rows,[]);
  store.dispose();
});
