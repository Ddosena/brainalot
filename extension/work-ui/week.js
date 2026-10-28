import {t} from '../i18n.js';
import {addCubeButton,trackBlock,unpinnedPossible} from './pool.js';
// Approved Work prototype, 2026-10-28: shared week implementation.
export function installWeek(ctx) {
const {clk,dd,ddmm,dow,dur,esc,iso,nowMinute,pad,weekOf}=ctx;
const {S,U,TODAY,DAY_FROM,VPX}=ctx;
function weekScreen(){
  const ws=U.weekStart,days=Array.from({length:7},(_,k)=>ws+k);
  const end=ws+6,dayCells=days.map(dayCell).join('');
  const todayButton=ws!==weekOf(TODAY)?`<button class="link" data-act="week-today">${t('к текущей неделе')}</button>`:'';
  const selected=U.sel&&ctx.goal(U.sel);
  const card=selected?`<section class="panel card" style="--c:${ctx.goalColor(selected)}">${goalCard(selected)}</section>`:'';
  return `<div class="main">
   <div class="toolbar"><h1>${t("План работы")}</h1><button class="btn primary" data-act="new-cube">${t("+ Кубик")}</button>`+
`<button class="btn add" data-act="new-goal">${t("+ Цель")}</button><button class="btn add" data-act="new-project">${t("+ Проект")}</button></div>
   ${ctx.hints()}
   <div class="nav"><button class="tri" data-act="week-prev" aria-label="${t("Прошлая неделя")}">◀</button>`+
`<b>${ddmm(ws)} — ${ddmm(end)}</b><button class="tri" data-act="week-next" aria-label="${t("Следующая неделя")}">`+
`▶</button>${todayButton}
    <span class="navhint">${t("тяни цель — перенос · края — дни · держи у края — соседняя неделя · правая кнопка — меню")}</span></div>
   <section class="panel"><div class="wk" data-from="${ws}" style="background:${wkBackground(ws)}">${bands()}</div>`+
`<div class="days">${dayCells}</div></section>
   ${doneShelf()}
   ${card}
   ${dayPlan(U.day)}
  </div>`;
}
function wkBackground(ws){
  const lines='repeating-linear-gradient(90deg,var(--grid) 0 1px,transparent 1px calc(100%/7))',t=TODAY-ws;
  if(t<0||t>=7)return lines;
  const next=t+1;
  return `${lines},linear-gradient(90deg,transparent calc(100%/7*${t}),`+
    `color-mix(in srgb,var(--today) 70%,transparent) calc(100%/7*${t}) calc(100%/7*${next}),`+
    `transparent calc(100%/7*${next}))`;
}
function goalBand(g,ws,a,b,r){
  const n=S.cubes.filter(k=>k.goal===g.id).length;
  const cls=(g.id===U.sel?' sel':'')+(g.s<ws?' cut-l':'')+(g.e>ws+6?' cut-r':'');
  const left=g.s>=ws?`<span class="edge l" title="${t('Тяни — начало')}"></span>`:'';
  const right=g.e<=ws+6?`<span class="edge r" title="${t('Тяни — конец')}"></span>`:'';
  const range=g.s===g.e?ddmm(g.s):`${ddmm(g.s)}–${ddmm(g.e)}`;
  const volume=g.auto&&!g.h?t('объём по кубикам'):String(g.h).replace('.',',')+t(' ч');
  const count=n?` · ${n} ${t('куб.')}`:'',due=g.due!=null?t(' · срок ')+ddmm(g.due):'';
  const colStart=a+1,colEnd=b+2,row=r+1;
  return `<div class="gband${cls}" role="button" tabindex="0" aria-label="${esc(g.title)}" `+
    `data-act="open-goal" data-arg="${esc(g.id)}" data-band data-goal="${esc(g.id)}" `+
    `style="grid-column:${colStart}/${colEnd};grid-row:${row};--c:${ctx.goalColor(g)};--p:${g.pct}%" `+
    `title="${esc(g.title)} ${t('— нажми, чтобы открыть; тяни, чтобы перенести')}">${left}<i></i><b>${esc(g.title)}</b>`+
    `<small>${range} · ${volume} · ${g.pct}%${count}${due}</small>${right}</div>`;
}
function bands(){
  const ws=U.weekStart,rows=[];
  const list=S.goals.filter(g=>!ctx.isDone(g)&&ctx.visible(g)&&g.e>=ws&&g.s<=ws+6).sort((a,b)=>a.s-b.s||a.e-b.e);
  const html=list.map(g=>{
    const a=Math.max(g.s,ws)-ws,b=Math.min(g.e,ws+6)-ws;let r=rows.findIndex(end=>end<a);if(r<0){r=rows.length;rows.push(b);}else rows[r]=b;
    return goalBand(g,ws,a,b,r);
  }).join('');
  return html||`<div class="wk-empty">${t("На этой неделе целей нет — «+ Цель» или двойной клик по дню")}</div>`;
}
function dayCell(i){
  const list=ctx.dayCubes(i),done=list.filter(k=>k.pct>=100).length,load=ctx.dayLoad(i);
  const doneText=done?` · ✓${done}`:'';
  const foot=list.length?`${list.length} ${t('куб. ·')} ${dur(load)}${doneText}`:i<TODAY?t('Прошло'):t('Пусто — брось кубик');
  const cls=(i===TODAY?' today':'')+(i<TODAY?' past':'')+(i===U.day?' picked':'');
  const today=i===TODAY?t(' · сегодня'):'',width=Math.min(100,load/480*100);
  return `<div class="day${cls}" role="button" tabindex="0" aria-label="${dow(i)} ${ddmm(i)}" `+
    `data-drop="day" data-day="${i}" data-act="pick-day" data-arg="${i}" `+
    `title="${t('Выбрать день · двойной клик — открыть День')}"><span>${dow(i)}${today}</span><b>${dd(i)}</b>`+
    `<span class="load" style="--w:${width}%"><i></i></span><span class="foot">${foot}</span></div>`;
}
function doneShelf(){
  const ws=U.weekStart,list=S.goals.filter(g=>ctx.isDone(g)&&g.doneOn!=null&&g.doneOn>=ws&&g.doneOn<=ws+6&&ctx.visible(g));
  const items=list.map(g=>`<span class="done-tile" data-goal="${esc(g.id)}" data-act="open-goal" data-arg="${esc(g.id)}" `+
    `title="${t('Открыть · правая кнопка — меню')}"><em>✓</em>${esc(g.title)}<small>${ddmm(g.doneOn)}</small></span>`).join('');
  const content=items||`<span class="muted">${t('Сюда улетают цели, доведённые до 100%.')}</span>`;
  return `<section class="panel shelf" id="goalShelf"><h3>${t('Выполнено')} <span>${list.length}</span></h3>${content}</section>`;
}
function goalCard(g){
  const list=S.cubes.filter(k=>k.goal===g.id),hasReal=ctx.readinessCubes(g).length>0,total=list.reduce((a,k)=>a+k.min,0),
doneMin=Math.round(list.reduce((a,k)=>a+k.min*k.pct/100,0));
  let note=t('Кубиков пока нет — добавь их, и готовность будет считаться сама. Без кубиков цель закрывается вручную.');
  if(hasReal)note=ctx.isDone(g)?t('Все кубики готовы — цель закрыта. Чтобы вернуть её в работу, добавь кубик или сними готовность с кубика.'):
    t('Считается по кубикам: каждый кубик — равная доля, его готовность заполняет эту долю.');
  const options=S.projects.map(p=>{
    const selected=p.id===g.p?' selected':'';
    return `<option value="${esc(p.id)}"${selected}>${esc(p.name)}</option>`;
  }).join('');
  const project=ctx.proj(g.p)?.name||'';
  const auto=g.auto?t(' (по кубикам)'):'',due=g.due!=null?iso(g.due):'';
  const volume=g.auto?`${dur(total)} ${t('· объём по кубикам')}`:`${dur(total)} ${t('из')} ${String(g.h).replace('.',',')} ${t('ч объёма')}`;
  const cubes=list.map(k=>ctx.keycap(k,{where:true})).join(''),add=addCubeButton(ctx,g.id);
  const action=hasReal?'':ctx.isDone(g)?
    `<button class="btn" data-act="goal-reopen" data-arg="${esc(g.id)}">${t('Вернуть в работу')}</button>`:
    `<button class="btn ok" data-act="goal-done" data-arg="${esc(g.id)}">${t('Готово — на полку')}</button>`;
  return `<div class="gc" data-goal="${esc(g.id)}" style="--c:${ctx.goalColor(g)}">
   <div class="gc-head"><span class="type"><i></i>${t("Цель ·")} ${esc(project)}</span>`+
`<button class="x" data-act="close-goal" aria-label="${t("Закрыть карточку цели")}" title="${t("Закрыть карточку")}">✕</button></div>
   <input class="gc-title" data-field="goal.title" data-id="${esc(g.id)}" value="${esc(g.title)}" aria-label="${t("Название цели")}">
   <div class="ready"><label>${t("Готовность")}</label>${ctx.goalBar(g)}</div>
   <p class="gnote">${note}</p>
   <div class="fields">
    <label>${t("Проект")}<select data-field="goal.p" data-id="${esc(g.id)}">${options}</select></label>
    <label>${t("С")}<input type="date" data-field="goal.s" data-id="${esc(g.id)}" value="${iso(g.s)}"></label>
    <label>${t("По")}<input type="date" data-field="goal.e" data-id="${esc(g.id)}" value="${iso(g.e)}"></label>
    <label>${t("Объём, ч")}${auto}<input type="number" min="0" step="0.5" data-field="goal.h" data-id="${esc(g.id)}" value="${g.h}"></label>
    <label>${t("Срок")}<input type="date" data-field="goal.due" data-id="${esc(g.id)}" value="${due}"></label>
   </div>
   <div class="gc-cubes"><h4>${t("Кубики цели")} <span>· ${list.length} · ${volume} ${t("· сделано")} ${dur(doneMin)}</span></h4>
    <div class="cubes">${cubes}${add}</div></div>
   <div class="gc-actions">${action}<span class="sp"></span>`+
`<button class="btn danger" data-act="goal-delete" data-arg="${esc(g.id)}">${t("Удалить цель")}</button></div>
  </div>`;
}
function goalPoolRow(g){
  const cubes=ctx.shelf(g.id).map(k=>ctx.keycap(k)).join(''),add=addCubeButton(ctx,g.id);
  return `<div class="chainh" style="--c:${ctx.goalColor(g)}" data-goal="${esc(g.id)}" data-act="open-goal" `+
    `data-arg="${esc(g.id)}"><i></i>${esc(g.title)}<span class="gpct">${g.pct}%</span></div>`+
    `<div class="cubes">${cubes}${add}</div>`;
}
function dayPlan(i){
  const here=ctx.poolGoals?ctx.poolGoals(i):ctx.activeOn(i).filter(ctx.visible),others=unpinnedPossible(ctx,i);
  const pool=here.map(goalPoolRow).join('')||`<p class="empty-note">${t('В этот день нет целей. «+ Цель» — и её кубики появятся здесь.')}</p>`;
  const untimed=ctx.dayCubes(i).filter(k=>k.at==null&&k.pct<100),count=here.reduce((a,g)=>a+ctx.shelf(g.id).length,0);
  const today=i===TODAY?t(' · сегодня'):'',pinned=ctx.pinnedPanel?ctx.pinnedPanel():'';
  const repeatables=ctx.repeatablesPanel?ctx.repeatablesPanel():'';
  const offerKeys=others.map(k=>ctx.keycap(k)).join('')||`<p class="empty-note">${t('Пусто')}</p>`;
  const dayKeys=untimed.map(k=>ctx.keycap(k)).join('')||`<span class="empty-note">${t('брось сюда — в день без времени')}</span>`;
  return `<section class="sect"><div class="wh"><h2>${dow(i).toUpperCase()} · ${ddmm(i)}</h2><span>${t("План дня")}${today}`+
`</span><strong>${dur(ctx.dayLoad(i))} ${t("в плане дня")}</strong>`+
`<button class="btn small" data-act="open-day">${t("Открыть День →")}</button></div>
   ${pinned}${repeatables}<div class="cols">
    <div class="panel pool" data-drop="shelf"><h3>${t("По целям дня")} <span>${count}</span></h3>${pool}</div>
    <div class="panel pool" data-drop="shelf"><h3>${t("Другие")} <span>${others.length} ${t("· Brainalot и календарь")}</span></h3>`+
`<div class="cubes">${offerKeys}</div></div>
    <div class="panel vcol"><div class="vtray" data-drop="tray" data-day="${i}"><small>${t("В дне без времени")}</small>`+
`<div class="cubes">${dayKeys}</div></div><div class="vscroll" id="vscroll">${vTrack(i)}</div></div>
   </div></section>`;
}
function trackRows(i,zone){
  const rows=[...ctx.eventsFor(i)];
  S.cubes.forEach(k=>{if(k.day===i&&k.at!=null&&!(ctx.D.ghost&&ctx.D.ghost.id===k.id))rows.push(k);});
  if(ctx.D.ghost&&ctx.D.ghost.day===i){const k=ctx.cube(ctx.D.ghost.id);if(k)rows.push({...k,at:ctx.D.ghost.at,isGhost:true});}
  return rows;
}
function vTrack(i){
  const top=m=>(m-DAY_FROM)/60*VPX,now=nowMinute();
  const hours=Array.from({length:16},(_,k)=>{
    const position=k*VPX,hour=pad(8+k);
    return `<div class="h" style="top:${position}px">${hour}:00</div>`;
  }).join('');
  const blocks=trackRows(i,'vtrack').map(e=>{
    const height=e.min/60*VPX,size=e.fixed?height:Math.max(14,height);
    return trackBlock(ctx,e,{axis:'v',position:top(e.at),size});
  }).join('');
  const height=16*VPX,nowline=i===TODAY&&now>=DAY_FROM?`<div class="nowline" style="top:${top(now)}px"></div>`:'';
  return `<div class="vtrack" id="vtrack" data-drop="vtrack" data-day="${i}" style="height:${height}px">${hours}${nowline}${blocks}</div>`;
}

Object.assign(ctx,{weekScreen,wkBackground,bands,dayCell,doneShelf,goalCard,dayPlan,trackRows,vTrack});
}
