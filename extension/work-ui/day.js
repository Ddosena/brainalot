import {t} from '../i18n.js';
import {addCubeButton,trackBlock,unpinnedPossible} from './pool.js';
// Approved Work prototype, 2026-10-28: shared day implementation.
export function installDay(ctx) {
const {clamp,ddmm,dur,esc,nowMinute,pad,wd}=ctx;
const {S,U,TODAY,DAY_FROM,DAY_SPAN}=ctx;
const DOWF=ctx.DOWF;
function offerColumn(label,list,tomorrow=false){
  const cubes=list.map(k=>ctx.keycap(k)).join('');
  return `<div class="offer-column${tomorrow?' tomorrow':''}"><h4>${label} <span>${list.length}</span></h4>`+
    `<div class="cubes"${tomorrow?' data-scroll-strip tabindex="0"':''}>${cubes}</div>${tomorrow&&list.length>3?'<small class="strip-more" aria-hidden="true">↔ · '+list.length+'</small>':''}</div>`;
}
function dayScreen(){
  const i=U.day,list=ctx.dayCubes(i),doneList=list.filter(k=>k.pct>=100),inDay=list.filter(k=>k.pct<100),total=ctx.dayLoad(i);
  const goalsHere=ctx.poolGoals?ctx.poolGoals(i):ctx.activeOn(i).filter(ctx.visible);
  const others=unpinnedPossible(ctx,i),ids=new Set(others.map(k=>k.id));
  const calendar=k=>['calendar','calendar_optional'].includes(k.kind);
  // Tomorrow is for work that can be brought forward, not scheduled Calendar appointments.
  // Owner 2026-10-28: cubes already placed on a day are taken, so they never appear here; the goal
  // cubes come from goals stretched over tomorrow (including those also running today).
  const tomorrowGoals=ctx.activeOn(i+1).filter(ctx.visible).flatMap(g=>ctx.shelf(g.id)).filter(k=>!k.pinned);
  const tomorrow=[...unpinnedPossible(ctx,i+1),...tomorrowGoals]
    .filter(k=>k.day==null)
    .filter(k=>!calendar(k)||k.all_day===true)
    .filter(k=>{if(ids.has(k.id))return false;ids.add(k.id);return true;});
  const offerRows=offerColumn('Brainalot',others.filter(k=>!calendar(k)))+
    offerColumn(t('Календарь'),others.filter(calendar));
  const today=i===TODAY?t(' · сегодня'):'';
  const todayButton=i!==TODAY?`<button class="link" data-act="today">${t('к сегодня')}</button>`:'';
  const doneTime=dur(doneList.reduce((a,k)=>a+k.min,0)),inDayTime=dur(inDay.reduce((a,k)=>a+k.min,0));
  const trophyItems=doneList.map(ctx.trophy).join('');
  const trophies=doneList.length?`<div class="trophies">${trophyItems}</div>`:
    `<span class="muted">${t('Кубики на 100% встают сюда трофеями.')}</span>`;
  const basket=inDay.map(k=>ctx.keycap(k,{time:true})).join('')+addCubeButton(ctx,'',true);
  const pinned=ctx.pinnedPanel?ctx.pinnedPanel():'',repeatables=ctx.repeatablesPanel?ctx.repeatablesPanel():'';
  const picker=ctx.goalChips?ctx.goalChips():'';
  // The chosen goals' shelf cubes in one row; no second line repeating the goal name.
  const chosenCubes=goalsHere.flatMap(g=>ctx.shelf(g.id)).map(k=>ctx.keycap(k)).join('');
  const goalRows=goalsHere.length?`<div class="cubes">${chosenCubes}${addCubeButton(ctx,goalsHere[0].id)}</div>`:
    S.goals.length?`<p class="empty-note">${t('Выбери цели выше — появятся их кубики.')}</p>`:`<p class="empty-note">${t('Создайте цель или добавьте кубик без цели.')}</p>`;
  const offers=offerRows||`<p class="empty-note">${t('Пусто')}</p>`;
  return `<div class="main">
   <div class="toolbar"><button class="tri" data-act="day-prev" aria-label="${t("Предыдущий день")}">◀</button>`+
`<h1 style="flex:none">${DOWF[wd(i)]} · ${ddmm(i)}${today}</h1>`+
`<button class="tri" data-act="day-next" aria-label="${t("Следующий день")}">▶</button>${todayButton}`+
`<span style="flex:1"></span><button class="btn primary" data-act="new-cube">${t("+ Кубик")}</button>`+
`<button class="btn add" data-act="new-goal">${t("+ Цель")}</button></div>
   ${ctx.hints()}
   ${dayGoals(i)}
   <section class="panel shelf" id="doneShelf"><h3>${t("Готово")} <span>${doneList.length} · ${doneTime}</span></h3>${trophies}</section>
   <section class="panel trackp"><div class="nav"><b>${t("Шкала дня")}</b><span>`+
`08:00–24:00 · ${dur(total)} ${t("в плане дня")}</span>`+
`<span class="navhint">${t("тащи кубик на шкалу или на часы · края — длительность · унеси со шкалы — вернётся на полку цели")}</span>`+
`</div>${hTrack(i)}</section>
   <div class="daygrid"><section class="panel clockcard"><div id="clockhost" style="width:100%">${ctx.clockSvg(i)}</div>`+
`</section>
    <div class="day-workspace"><section class="panel trayp day-basket" data-drop="tray" data-day="${i}">`+
`<h3>${t("В дне")} <span>${inDay.length} · ${inDayTime} ${t("· без времени — брось сюда")}</span>`+
`</h3><div class="cubes">${basket}</div></section>
     ${pinned}<div class="shelves"><section class="panel trayp" data-drop="shelf"><h3>${t("Кубики целей")} <span>${goalsHere.length}</span>`+
`</h3>${picker}${goalRows}</section>
      <div class="side-shelves"><div class="side-top">${repeatables}<section class="panel trayp tomorrow-shelf">${offerColumn(t('Завтра'),tomorrow,true)}</section></div>
      <section class="panel trayp offers" data-drop="shelf">`+
`<h3>${t("Brainalot и календарь")} <span>${others.length} ${t("· возможные")}</span>`+
`</h3><div class="offer-columns">${offers}</div></section></div></div></div>
  </div>`;
}
// Owner 2026-10-29: in Day, hovering a goal lights up all its cubes (timeline, clock, shelves);
// hovering a cube lights up only its goal. Classes only: no re-render, nothing is saved.
let hover=null;
function paintHover(){
  const doc=globalThis.document,root=doc?.querySelector?.('.app.dayapp');
  doc?.querySelectorAll?.('.hl-link').forEach(n=>n.classList.remove('hl-link'));
  if(!root)return;
  root.classList.toggle('hl-on',hover?.from==='goal');
  if(!hover)return;
  if(hover.from==='goal')root.querySelectorAll('[data-cube]').forEach(n=>{if(ctx.cube(n.dataset.cube)?.goal===hover.goal)n.classList.add('hl-link');});
  root.querySelectorAll('.dg[data-goal],.clab[data-goal],.gchip[data-goal]').forEach(n=>{if(n.dataset.goal===hover.goal)n.classList.add('hl-link');});
}
function hoverAt(target){
  const el=U.screen==='day'&&!ctx.D?.pdrag&&!ctx.D?.rdrag?target?.closest?.('.dg[data-goal],.clab[data-goal],.gchip[data-goal],[data-cube]'):null;
  const goal=el?(el.dataset.cube?ctx.cube(el.dataset.cube)?.goal||null:el.dataset.goal):null;
  const next=goal?{goal,from:el.dataset.cube?'cube':'goal'}:null;
  if(next?.goal!==hover?.goal||next?.from!==hover?.from){hover=next;paintHover();}
}
function dayGoals(i){
  const load=g=>S.cubes.filter(k=>k.goal===g.id&&k.day===i).reduce((a,k)=>a+k.min,0);
  const list=ctx.activeOn(i).filter(ctx.visible).sort((a,b)=>load(b)-load(a)||a.e-b.e);
  if(!list.length)return `<section class="dgoals"><h3>${t('Цели этого дня')}</h3><p class="empty-note">`+
    `${t('На этот день целей нет. Растяни цель на Неделе на этот день или нажми «+ Цель».')}</p></section>`;
  const goals=list.map(g=>dayGoal(g,i)).join('');
  return `<section class="dgoals"><h3>${t('Цели этого дня')} <span>${list.length}</span></h3><div class="dgrid">`+
    `${goals}</div></section>`;
}
function dayGoal(g,i){
    const own=ctx.readinessCubes(g),doneN=own.filter(k=>k.pct>=100).length;
    // The goal's days are only shown, not clickable: a bar from start to end with this day marked.
    const len=g.e-g.s+1,pos=clamp(i-g.s,0,len-1),dayNumber=pos+1;
    const due=g.due!=null?t(' · срок ')+ddmm(g.due):'',start=pos/len*100,width=100/len,project=ctx.proj(g.p)?.name||'';
    return `<div class="dg" style="--c:${ctx.goalColor(g)}" data-goal="${esc(g.id)}">`+
      `<button class="t" data-act="open-goal" data-arg="${esc(g.id)}" title="${t('Открыть цель')}">${esc(g.title)}</button>`+
      `<span class="pc">${g.pct}%</span>
     <span class="m">${esc(project)}${due} ${t('· день')} ${dayNumber} ${t('из')} ${len} `+
      `${t('· кубики')} ${doneN}/${own.length}</span>
     <div class="bot"><span class="gspan" title="${ddmm(g.s)}–${ddmm(g.e)} ${t('· этот день —')} ${dayNumber}${t('-й')}">`+
      `<span>${ddmm(g.s)}</span><span class="gbar" style="--a:${start}%;--w:${width}%"><i></i><b></b></span>`+
      `<span>${ddmm(g.e)}</span></span>${ctx.goalBar(g,true)}</div></div>`;
}
function hTrack(i){
  const x=m=>(m-DAY_FROM)/DAY_SPAN*100,now=nowMinute(),isToday=i===TODAY;
  const blocks=ctx.trackRows(i,'track').map(e=>{
    const live=!e.fixed&&isToday&&now>=e.at&&now<e.at+e.min;
    return trackBlock(ctx,e,{axis:'h',position:x(e.at),size:e.min/DAY_SPAN*100,live});
  }).join('');
  const hours=Array.from({length:16},(_,k)=>{
    const hour=pad(8+k);
    return `<span>${hour}</span>`;
  }).join('');
  const nowline=isToday&&now>=DAY_FROM?`<div class="nowline" style="left:${x(now)}%"></div>`:'';
  return `<div class="htrack" id="htrack" data-drop="track" data-day="${i}"><div class="hrs">${hours}</div>`+
    `<div class="grid"></div>${nowline}${blocks}</div>`;
}
Object.assign(ctx,{paintHover,hoverAt,dayScreen,dayGoals,hTrack});
}
