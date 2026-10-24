import test from "node:test";
import assert from "node:assert/strict";
import {importanceScore, legacyForScore, validImportanceScore} from "../importance-score.js";
import {capturePayload, editNotePatch} from "../panel-state.js";
import {normalizeDraft} from "../capture-window.js";
import {dictationOverrides} from "../dictation.js";
import {createRightClickPair} from "../task-rename.js";

test("zero importance survives draft, capture, dictation and edit boundaries", () => {
  const draft = normalizeDraft({version:2, text:"Подготовить урок",kind:"task",importanceScore:0});
  assert.equal(draft.importanceScore, 0);
  assert.equal(dictationOverrides(draft).importance_score, 0);
  assert.equal(capturePayload({text:draft.text,kind:"task",importanceScore:0}).importance_score, 0);
  assert.equal(editNotePatch({title:draft.text,kind:"task",importanceScore:0}).importance_score, 0);
});
test("auto priority leaves dictation free, old labels remain compatible, strict scores reject coercion", () => {
  assert.equal("importance_score" in dictationOverrides(normalizeDraft(null)), false);
  assert.deepEqual(["low","normal","high","critical"].map(importance => importanceScore({importance})), [1,2,4,5]);
  assert.equal(importanceScore({importance_score:0,importance:"critical"}), 0);
  assert.equal(importanceScore({}),0);
  for (const value of [-1,6,0.5,"5",true,null]) assert.equal(validImportanceScore(value),false);
  assert.equal(legacyForScore(5,"task"),"critical");
  assert.equal(legacyForScore(5,"thought"),"high");
});
test("rename needs two nearby right clicks, not a long gap, modifier, keyboard or click elsewhere", () => {
  let time=0;
  const pair=createRightClickPair({now:()=>time});
  const event={button:2,clientX:20,clientY:30};
  assert.equal(pair(event),false);
  time=200; assert.equal(pair(event),true);
  time=300; assert.equal(pair(event),false);
  time=900; assert.equal(pair(event),false);
  time=1000; assert.equal(pair({...event,clientX:100}),false);
  time=1100; assert.equal(pair({...event,ctrlKey:true}),false);
  time=1200; assert.equal(pair(event),false);
  time=1300; assert.equal(pair({...event,button:0}),false);
  time=1400; assert.equal(pair(event),false);
  time=1500; assert.equal(pair(event),true);
});
