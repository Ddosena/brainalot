import test from 'node:test';
import assert from 'node:assert/strict';
import {compactTimelineDate} from '../work-constructor.js';

test('timeline date shows only weekday, day and month',()=>{
  assert.equal(compactTimelineDate('2026-10-26'),'ПН · 26.10');
  assert.equal(compactTimelineDate('2026-10-27'),'ВТ · 27.10');
  assert.equal(compactTimelineDate('2026-12-31'),'ЧТ · 31.12');
});

test('invalid dates do not produce a misleading label',()=>{
  assert.equal(compactTimelineDate('2026-02-30'),'');
  assert.equal(compactTimelineDate('today'),'');
});
