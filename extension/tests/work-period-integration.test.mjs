import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createPeriodTransport} from '../work-period/transport.js';
import {createConnection} from '../connection.js';
import {requestApi} from '../api.js';
import {ARROW_TOP_GUTTER, ARROW_BOTTOM_GUTTER, arrowZoomBounds, axisDayLabel, dailyLimitMinutes, placeArrowLabelBoxes, projectArrowModel} from '../work-period/app.js';
import {setLocale} from '../i18n.js';
import {ui, uiMarkup} from '../work-period/ui-text.js';

const source = path => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('production period transport uses the authenticated connection for every endpoint', async () => {
  const calls=[];
  const chromeApi={storage:{local:{get:async()=>({connection:{token:'synthetic-credential',port:32123}})}}};
  const connection=createConnection(chromeApi,{request:(path,options)=>requestApi(path,{...options,fetcher:async(url,init)=>{
    calls.push({url,init});return {ok:true,status:200,json:async()=>({revision:7})};
  }})});
  const transport=createPeriodTransport(connection);
  for(const route of ['model','context','command','snapshot']){
    const response=await transport(`/api/${route}`,route==='model' || route==='snapshot' ? {} : {method:'POST',body:JSON.stringify({request_id:'synthetic-request',expected_revision:6})});
    assert.equal(response.ok,true);assert.deepEqual(await response.json(),{revision:7});
  }
  assert.deepEqual(calls.map(call=>new URL(call.url).pathname),['/api/work/period/model','/api/work/period/context','/api/work/period/command','/api/work/period/snapshot']);
  for(const {url,init} of calls){assert.equal(new URL(url).port,'32123');assert.equal(new URL(url).search,'');assert.ok(!url.includes('synthetic-credential'));assert.equal(init.headers.Authorization,'Bearer synthetic-credential');assert.equal(init.credentials,'omit');}
});

test('transport preserves conflict status and refuses arbitrary routes without sending anything', async () => {
  let calls=0;
  const transport=createPeriodTransport({async api(){calls++;throw Object.assign(new Error('Conflict'),{status:409});}});
  const response=await transport('/api/context',{method:'POST',body:'{}'});
  assert.equal(response.ok,false);assert.equal(response.status,409);assert.deepEqual(await response.json(),{error:'Conflict'});
  await assert.rejects(transport('/api/other'),/Unknown Work endpoint/);assert.equal(calls,1);
});

test('common Work shell and bundled children keep their feature guard', async () => {
  const plan=await source('work.html'),day=await source('work-day.html'),boot=await source('work-period/bootstrap.js');
  assert.match(plan,/data-work-mode="day"/);assert.match(plan,/data-work-mode="week"/);assert.match(plan,/id="work-view"/);
  assert.doesNotMatch(day,/mode-projects|board-wrap|work-mode-tabs/);
  assert.match(await source('work-period.html'),/id="sync-status" hidden/);
  assert.match(day,/src="work-day-paused.js/);assert.match(await source('work-day-paused.js'),/await import\("\.\/work-app.js/);
  assert.match(await source('work-paused.js'),/if \(WORK_ENABLED\)/);
  assert.ok(boot.indexOf("await import('../desktop-platform.js')")<boot.indexOf('configurePeriodTransport(createConnection'));
  assert.match(boot,/await initI18n\(\)/);assert.match(boot,/await syncLocale\(\)/);assert.match(boot,/applyDocument\(\)/);
  for(const asset of [plan,await source('work-period/app.js'),await source('work-period/styles.css'),boot]){
    assert.doesNotMatch(asset,/4387[0-9]|localhost|Тренировоч|training-label|__periodPlanner|\/api\/restore|course[_-]source\.json|fetch\(/i);
  }
});

test('captions stay at their true date anchor and crowded details are suppressed', () => {
  const items=[{id:'start',y:100,height:40,anchorOffset:10,priority:2},{id:'crowded',y:106,height:40,anchorOffset:10,priority:1},{id:'deadline',y:180,height:40,anchorOffset:10,priority:2}];
  const placed=placeArrowLabelBoxes(items,260,230,{todayHeight:20,gap:8});
  assert.deepEqual(placed.map(item=>item.id),['start','deadline']);
  for(const item of placed)assert.equal(item.top+item.anchorOffset,item.y);
  assert.equal(axisDayLabel('2026-11-09'),'09');
  const input={today:'2026-10-26',projects:[{id:'synthetic',visible:true,tasks:[{id:'package',title:'Synthetic package',kind:'package',window_start:'2026-11-09',window_end:'2026-11-15'}],timeline_entries:[{id:'start',task_id:'package',event_kind:'start',date:'2026-11-09'},{id:'end',task_id:'package',event_kind:'deadline',date:'2026-11-15'}]}]};
  const axis=projectArrowModel(input,{pixelsPerDay:12});
  assert.deepEqual(axis.groups.map(group=>group.date),['2026-11-09','2026-11-15']);assert.equal(axis.groups[1].y-axis.groups[0].y,6*12);
});

test('last-day caption has internal room without changing true date coordinates or fit range', () => {
  const source={today:'2026-11-01',projects:[{id:'qa',visible:true,start:'2026-11-01',end:'2026-11-15',tasks:[{id:'end',date:'2026-11-15'}]}]};
  for(const pixelsPerDay of [4,12,48]){
    const axis=projectArrowModel(source,{pixelsPerDay});
    assert.equal(axis.groups[0].y,ARROW_TOP_GUTTER+14*pixelsPerDay);
    assert.equal(axis.endY,axis.groups[0].y);
    assert.equal(axis.height-axis.endY,ARROW_BOTTOM_GUTTER);
    const placed=placeArrowLabelBoxes([{id:'end',y:axis.endY,height:64,anchorOffset:11}],axis.height,axis.todayY);
    assert.equal(placed.length,1);assert.equal(placed[0].top+11,axis.endY);
  }
  const bounds=arrowZoomBounds(14,680),fit=projectArrowModel(source,{pixelsPerDay:bounds.min});
  assert.equal(fit.height,680);assert.equal(fit.groups[0].y,fit.endY);
});

test('day budget respects the selected date override including a deliberate zero', () => {
  const data={settings:{daily_limit_minutes:300,daily_limit_minutes_by_date:{'2026-10-26':450,'2026-10-27':0}}};
  assert.equal(dailyLimitMinutes(data,'2026-10-26'),450);assert.equal(dailyLimitMinutes(data,'2026-10-27'),0);assert.equal(dailyLimitMinutes(data,'2026-10-28'),300);
});

test('localization translates static UI fragments and leaves personal titles unchanged', () => {
  setLocale('en');
  try{
    const personalTitle='Проекты';
    assert.equal(ui`<h2>Проекты</h2><strong>${personalTitle}</strong>`,'<h2>Projects</h2><strong>Проекты</strong>');
    assert.equal(uiMarkup('<button title="Сохранить">Сохранить</button>'),'<button title="Save">Save</button>');
  }finally{setLocale('ru');}
});
