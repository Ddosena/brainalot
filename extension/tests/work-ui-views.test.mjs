import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {installCubes} from '../work-ui/cubes.js';
import {installWeek} from '../work-ui/week.js';
import {installArrow} from '../work-ui/arrow.js';
import {installDay} from '../work-ui/day.js';
import {installClock} from '../work-ui/clock.js';
import {installDialogs} from '../work-ui/dialogs.js';
import {installPool} from '../work-ui/pool.js';
import {installShell} from '../work-ui/shell.js';
import {setLocale} from '../i18n.js';

setLocale('ru');
const BASE=Date.UTC(2026,9,26);
function context() {
  const ctx={S:{started:true,projects:[{id:'p',name:'Project',c:'#f09300'}],goals:[{id:'g',p:'p',title:'Goal',s:0,e:3,h:2,pct:40,doneOn:null,due:null}],cubes:[{id:'k',goal:'g',title:'Cube',min:60,pct:40,day:2,at:780,links:[]}],events:[]},U:{screen:'week',weekStart:0,day:2,sel:null,ppd:22},D:{ARROW_FROM:0,SCALER_H:560},TODAY:2,DAY_FROM:480,DAY_SPAN:960,VPX:30,HALF:720,RING:112,RW:58,FW:76,NUMR:168,DURS:[15,30,60,240],PPD_MIN:5,PPD_MAX:40};
  const {S}=ctx;
  Object.assign(ctx,{dayDate:i=>new Date(BASE+i*86400000),pad:n=>String(n).padStart(2,'0'),dd:i=>String(26+i).padStart(2,'0'),ddmm:i=>`${26+i}.10`,wd:i=>((i%7)+7)%7,dow:i=>['пн','вт','ср','чт','пт','сб','вс'][((i%7)+7)%7],DOWF:['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'],monthName:()=> 'октябрь',weekOf:i=>Math.floor(i/7)*7,iso:i=>new Date(BASE+i*86400000).toISOString().slice(0,10),clk:m=>`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`,hm:m=>m+' мин',dur:m=>m+' мин',nowMinute:()=>800,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),clamp:(v,a,b)=>Math.max(a,Math.min(b,v)),proj:id=>S.projects.find(p=>p.id===id),goal:id=>S.goals.find(g=>g.id===id),cube:id=>S.cubes.find(k=>k.id===id),isDone:g=>g.pct>=100,visible:g=>!S.projects.find(p=>p.id===g.p)?.hidden,goalColor:()=> '#f09300',colorOf:k=>k.goal?'#f09300':'var(--possible)',eventsFor:i=>S.events.filter(e=>e.day===i),dayCubes:i=>S.cubes.filter(k=>k.day===i),dayLoad:i=>S.cubes.filter(k=>k.day===i).reduce((s,k)=>s+k.min,0),shelf:id=>S.cubes.filter(k=>k.goal===id&&k.day==null&&k.pct<100),activeOn:i=>S.goals.filter(g=>g.pct<100&&g.s<=i&&g.e>=i),calendarPoints:()=>S.projects.filter(p=>!p.hidden).flatMap(p=>p.calendarPoints||[]),paletteColors:()=>['#f09300'],swatchChoices:()=>[{color:'#f09300',name:'Манго',count:4}],paletteName:c=>c.name,currentTheme:()=>({theme:'midnight',mode:'dark'}),$:()=>null,render:()=>{}});
  globalThis.document={createElement:()=>({getContext:()=>({font:'',measureText:s=>({width:s.length*6})})})};
  globalThis.innerWidth=1440;globalThis.innerHeight=900;
  ctx.activityOptions=()=>'<option value=personal>Дело</option>';installShell(ctx);installCubes(ctx);installPool(ctx);installWeek(ctx);installArrow(ctx);installDay(ctx);installClock(ctx);installDialogs(ctx);
  return ctx;
}
// Keep descendant selectors and spaces inside CSS strings meaningful while allowing
// source formatting. Only the explicitly approved integration rules are excluded.
const cssCanonical = source => source.replace(/\/\*[^]*?\*\//g,'').replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\s+|[{}:;,]/g, token => {
 if (/^["']/.test(token)) return token;
 return /^\s+$/.test(token)?' ':token;
}).replace(/\s*([{}:;,])\s*/g,'$1').trim();
test('approved CSS preserves prototype layout apart from documented integration additions',()=>{
 const source=readFileSync(new URL('./fixtures/work-prototype-2026-10-28.css',import.meta.url),'utf8').replace(/\/\* ---------- Prototype bar[^]*?(?=\/\* ----------)/,'');
 const additions=new Set([
  '.sw label','.sw small','.sw small em',
  '.cbK:focus-visible,.trophy:focus-visible,.gband:focus-visible,.day:focus-visible,.arrow .dot:focus-visible,.arrow .mk:focus-visible,.scaler:focus-visible',
  '.pin-btn','.pin-btn[aria-pressed="true"]','.pin-btn:focus-visible',
  '.cube-play','.cube-play:focus-visible','.cbK::after,.hb::after,.vb::after','.cbK.time-running,.vb.time-running',
  '.active-timer','.active-timer[hidden]','.active-timer>span','.active-timer output',
  '.work-timer','.timer-reading,.timer-actions','.timer-reading','.work-timer p','.timer-settings','.timer-settings summary','.timer-settings .fields','.timer-stats',
  '.day-basket','.goal-picker','.goal-picker>span','.goal-picker select','.goal-picker option',
  // Owner 2026-10-28: timeline cubes name their goal at the bottom.
  '.htrack .hb .gt','.vb .gt','.gband.done'
 ].map(cssCanonical));
 const actual=readFileSync(new URL('../work-ui/styles.css',import.meta.url),'utf8').split('/* Day pools:')[0].replace(/([^{}]+)\{([^{}]*)\}/g,(rule,selector,body)=>{
  const key=cssCanonical(selector);
  // The original .sw label rule remains; only the added flex layout is excluded.
  return additions.has(key)&&(key!=='.sw label'||/display\s*:\s*flex/.test(body))?'':rule;
 });
 // Theme palettes now live in theme-tokens.css; keep the entire layout contract.
 const withoutTheme = css => css.replace(/\.(?:graphite|midnight|contrast)\.(?:dark|light)\{[^}]*\}/g,'')
  .replace(/body\{--important:[^}]*\}/g,'').replace(/\.light\{--hand:[^}]*\}/g,'');
 assert.equal(cssCanonical(withoutTheme(actual)),cssCanonical(withoutTheme(source)));
});
test('one cube markup escapes owner text, keeps progress and source count',()=>{const c=context();Object.assign(c.S.cubes[0],{title:'<script>"',links:[{url:'https://example.com'}]});const html=c.keycap(c.S.cubes[0]);assert.match(html,/&lt;script>/);assert.doesNotMatch(html,/<script>/);assert.match(html,/--p:40%/);assert.match(html,/class="srcn"/);assert.match(html,/data-drag="k"/);});
test('goal readiness is read only and shows equal cube shares with server percentage',()=>{const c=context();c.S.cubes.push({...c.S.cubes[0],id:'k2',pct:100});const html=c.goalBar(c.S.goals[0]);assert.equal((html.match(/<span class="gp/g)||[]).length,2);assert.match(html,/>40% · 1\/2</);assert.doesNotMatch(html,/<button/);});
test('goal readiness shares use server membership and exclude forecast cubes',()=>{const c=context(),g=c.S.goals[0];c.S.cubes.push({...c.S.cubes[0],id:'forecast',candidate:true,pct:0});g.readiness_cube_ids=['k'];g.pct=40;const html=c.goalBar(g);assert.equal((html.match(/<span class="gp/g)||[]).length,1);assert.match(c.dayGoals(2),/кубики 0\/1/);g.readiness_cube_ids=[];assert.equal(c.readinessCubes(g).length,0);});
test('source identifiers are escaped in a separate attribute and never concatenated with cube identity',()=>{const c=context();const sourceId='source:colon"><img src=x onerror=alert(1)>';c.S.cubes[0].links=[{id:sourceId,text:'Note'}];c.U.modal={type:'cube',id:'k'};const html=c.modalHtml();assert.doesNotMatch(html,/<img/);assert.match(html,/data-arg="k" data-source-id="source:colon&amp;quot;|data-arg="k" data-source-id="source:colon&quot;/);assert.doesNotMatch(html,/data-arg="k:source/);});
test('Gantt assigns overlapping goals to different rows and keeps clipped edges',()=>{const c=context();c.S.goals.push({...c.S.goals[0],id:'g2',s:-4,e:1,title:'Clipped'});const html=c.bands();assert.match(html,/cut-l/);assert.match(html,/grid-row:1/);assert.match(html,/grid-row:2/);});
test('goal card has no manual done action when cubes exist',()=>{const c=context();assert.doesNotMatch(c.goalCard(c.S.goals[0]),/data-act="goal-done"/);c.S.cubes=[];assert.match(c.goalCard(c.S.goals[0]),/data-act="goal-done"/);});
test('explicit add cube to a completed goal keeps that exact goal instead of creating a standalone note',()=>{const c=context();c.S.goals[0].pct=100;c.U.modal={type:'cube-new',goal:'g',where:'shelf'};const html=c.modalHtml();assert.match(html,/<option value="g" selected>/);assert.doesNotMatch(html,/<option value="" selected>/);});
test('forecast-only goal keeps manual completion actions until a real cube participates',()=>{const c=context(),g=c.S.goals[0];g.readiness_cube_ids=[];c.S.cubes[0].candidate=true;c.U.menu={kind:'goal',id:g.id,x:0,y:0};assert.match(c.goalCard(g),/data-act="goal-done"/);assert.match(c.menuHtml(),/data-act="goal-done"/);g.readiness_cube_ids=['k'];assert.doesNotMatch(c.goalCard(g),/data-act="goal-done"/);assert.doesNotMatch(c.menuHtml(),/data-act="goal-done"/);});
test('day rows contain all active goals and passive date bars',()=>{const c=context();c.S.goals.push({...c.S.goals[0],id:'g2',title:'Second'});const html=c.dayGoals(2);assert.match(html,/Second/);assert.equal((html.match(/class="gspan"/g)||[]).length,2);assert.doesNotMatch(html,/data-act="pick-day"/);});
test('day has both source and goal shelves, trophy and time labels',()=>{const c=context();c.S.cubes.push({id:'o',goal:null,title:'Offer',src:'Brainalot',min:30,pct:0,day:null,at:null});const html=c.dayScreen();assert.match(html,/Кубики целей/);assert.match(html,/Brainalot и календарь/);assert.match(html,/data-act="pool-goal"/);assert.match(html,/13:00–14:00/);assert.match(c.trophy({...c.S.cubes[0],pct:100}),/class="medal">✓/);});
test('tomorrow omits timed Calendar lessons but keeps them on their own day track',()=>{
  const c=context();
  c.S.events.push({id:'lesson',kind:'calendar',title:'Timed lesson',day:3,at:600,min:60,all_day:false});
  assert.doesNotMatch(c.dayScreen(),/Timed lesson/);
  assert.match(c.hTrack(3),/Timed lesson/);
  assert.match(c.hTrack(3),/10:00–11:00/);
});
test('tomorrow offers all-day Calendar tasks and Brainalot cubes, excluding timed optional offers',()=>{
  const c=context(),base={goal:null,min:30,pct:0,day:null,at:null,source_date:'2026-10-29'};
  c.S.cubes.push(
    {...base,id:'homework',kind:'calendar_optional',title:'All-day homework',all_day:true},
    {...base,id:'timed',kind:'calendar_optional',title:'Timed optional lesson',all_day:false,source_start:'10:00'},
    {...base,id:'unknown',kind:'calendar_optional',title:'Unknown duration lesson',all_day:false,duration_unknown:true},
    {...base,id:'brainalot',kind:'brainalot',title:'Tomorrow library task',day:3}
  );
  const html=c.dayScreen();
  assert.match(html,/All-day homework/);
  // Owner 2026-10-28: a cube already placed on tomorrow is taken and stays out of the strip.
  assert.doesNotMatch(html,/Tomorrow library task/);
  assert.doesNotMatch(html,/Timed optional lesson|Unknown duration lesson/);
});
test('tomorrow skips cubes already placed on a day and offers unplaced cubes of goals running tomorrow',()=>{
  const c=context(),base={goal:null,min:30,pct:0,day:3,at:600,kind:'calendar_optional'};
  c.S.cubes.push(
    {...base,id:'all-day',title:'Placed all-day task',all_day:true},
    {...base,id:'timed',title:'Placed timed lesson',all_day:false},
    {id:'tomorrow-goal-cube',goal:'g2',title:'Next goal shelf cube',min:60,pct:0,day:null,at:null,links:[]}
  );
  c.S.goals.push({...c.S.goals[0],id:'g2',title:'Tomorrow goal',s:3,e:4});
  const strip=c.dayScreen().split('tomorrow-shelf')[1];
  assert.doesNotMatch(strip,/Placed all-day task|Placed timed lesson/);
  assert.match(strip,/Next goal shelf cube/);
});
test('arrow clusters crowded labels with leaders and keeps exact date dots',()=>{const c=context();c.S.goals.push({...c.S.goals[0],id:'g2',title:'Second'});const html=c.arrow();assert.equal((html.match(/class="dot"/g)||[]).length,2);assert.match(html,/<path d="M 57 /);assert.match(html,/class="dr we/);assert.match(html,/class="dn today/);});
test('calendar diamonds use project colour and disappear with hidden project',()=>{const c=context();c.S.projects[0].calendarPoints=[{day:4,at:600,title:'Calendar',color:'#f09300'}];let html=c.arrow();assert.match(html,/class="dot web"/);assert.match(html,/--important:#f09300/);c.S.projects[0].hidden=true;assert.doesNotMatch(c.arrow(),/class="dot web"/);});
test('calendar palette has names counts owners and warns about legacy custom colour',()=>{const c=context();c.S.projects.push({id:'p2',name:'Other',c:'#f09300'});const html=c.swatches('c','#123456','project.c','p');assert.match(html,/Манго · 4/);assert.match(html,/Other/);assert.match(html,/Свой цвет/);assert.match(html,/required/);});
test('menus expose day actions and hide manual goal closure when cube derived',()=>{const c=context();c.U.menu={kind:'day',id:'2',x:2000,y:2000};assert.match(c.menuHtml(),/Перейти в день/);assert.match(c.menuHtml(),/left:1225px/);c.U.menu={kind:'goal',id:'g',x:0,y:0};assert.doesNotMatch(c.menuHtml(),/data-act="goal-done"/);});
test('cube editor only renders http links as active anchors',()=>{const c=context();c.S.cubes[0].links=[{id:'s',url:'javascript:alert(1)',text:'unsafe'}];c.U.modal={type:'cube',id:'k'};const html=c.modalHtml();assert.doesNotMatch(html,/href="javascript:/);assert.match(html,/unsafe/);});
test('clock preserves classic radii, labels, current hand and radial overflow wrapping',()=>{const c=context();const html=c.clockSvg(2);assert.match(html,/r="112" style="stroke-width:58"/);assert.match(html,/ck-hand/);assert.match(html,/<textPath/);assert.match(html,/>12<\/text>/);assert.match(c.sectorLabel({...c.S.cubes[0],title:'Смоделировать очень длинное основание'},780,795,58,'test',false),/<tspan/);assert.doesNotMatch(c.clockSvg(3),/class="ck-hand"/);});
test('onboarding completes only after a goal cube is placed, not an offer',()=>{const c=context();c.S.cubes[0].day=null;assert.match(c.hints(),/Перетащи кубик/);c.S.cubes.push({goal:null,day:2});assert.notEqual(c.hints(),'');c.S.cubes[0].day=2;assert.equal(c.hints(),'');});
test('day plan counts cubes plus timed calendar events, overlapping events once',async()=>{
 // Owner 2026-10-28: «в плане 2,9 ч» ignored lessons and the webinar.
 const {dayPlanMinutes}=await import('../work-ui/pool.js');
 const cubes=[{min:80},{min:80},{min:25}],events=[{at:630,min:60},{at:840,min:60},{at:1140,min:150},{at:1150,min:30}];
 assert.equal(dayPlanMinutes(cubes,events),185+270);
 assert.equal(dayPlanMinutes([],[]),0);
});
