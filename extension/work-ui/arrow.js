import {t} from '../i18n.js';
// Approved Work prototype, 2026-10-28: shared arrow implementation.
export function installArrow(ctx) {
const {clk,dayDate,dd,ddmm,dow,esc,monthName,wd,weekOf}=ctx;
const {S,U,TODAY,PPD_MIN,PPD_MAX}=ctx;
function pointAttributes(title, action, arg){
  return `role="button" tabindex="0" aria-label="${esc(title)}"`+
    ` class="${action.className}"${action.drag} data-act="${action.name}" data-arg="${esc(arg)}"`;
}
function arrowItem(m,pv,shift){
  if(m.kind==='web')return calendarItem(m);
  const g=m.g,done=ctx.isDone(g),drag=done?'':' data-gl';
  const cls=(done?' done':'')+(g.id===U.sel?' sel':'')+(g.id===U.flash?' fresh':'')+(pv?.id===g.id?' drag':'');
  const shared={name:'pick-goal',drag:drag+` data-goal="${esc(g.id)}"`};
  const dot=pointAttributes(g.title,{...shared,className:'dot'+cls},g.id);
  const label=pointAttributes(g.title,{...shared,className:'mk'+cls},g.id);
  const color=ctx.goalColor(g),mark=done?'✓':'';
  const status=done?t('выполнено'):g.due!=null?t('срок'):t('последний день');
  const length=g.e-g.s+1;
  const duration=done?'':' · '+(g.s+shift(g)===g.e+shift(g)?t('1 день'):`${length} ${t('дн.')}`);
  return `<div ${dot} style="top:${m.dotY}px;--c:${color}">${mark}</div>`+
    `<div ${label} style="top:${m.labelY}px;--c:${color}"><span>${esc(g.title)}<small>`+
    `${ddmm(m.day)} · ${status}${duration}</small></span></div>`;
}
function calendarItem(m){
  const w=m.w,shared={name:'pick-day',drag:''};
  const dot=pointAttributes(w.title,{...shared,className:'dot web'},w.day);
  const label=pointAttributes(w.title,{...shared,className:'mk web'},w.day);
  const time=w.at==null?t('весь день'):clk(w.at);
  return `<div ${dot} style="top:${m.dotY}px;--important:${w.color}" title="${esc(w.title)}"></div>`+
    `<div ${label} style="top:${m.labelY}px;--important:${w.color}"><span>${esc(w.title)}<small>`+
    `${dow(w.day)} ${ddmm(w.day)} · ${time} ${t('· календарь')}</small></span></div>`;
}
function arrow(){
  const gs=S.goals.filter(ctx.visible);
  // Two months back and half a year ahead (the manual planning horizon), wider if goals reach further.
  const from=Math.min(TODAY-60,...gs.map(g=>g.s-7)),to=Math.max(TODAY+182,...gs.map(g=>(g.due??g.e)+14)),ppd=U.ppd,y=i=>(i-from)*ppd+28,h=y(to)+20;
  ctx.D.ARROW_FROM=from;
  let months='',m0=from;
  for(let i=from;i<=to+1;i++){
    if(i===to+1||(dd(i)==='01'&&i!==from)){
      const alt=dayDate(m0).getUTCMonth()%2?' alt':'';
      const top=y(m0)-14,height=y(i)-y(m0);
      months+=`<div class="mb${alt}" style="top:${top}px;height:${height}px"><span>${monthName(m0)}</span></div>`;
      m0=i;
    }
  }
  // Every day is a row (weekends tinted, Mondays ruled); numbers are small but always on their row.
  let days='';const every=ppd>=11?1:ppd>=6?7:14;
  for(let i=from;i<=to;i++){
    const w=wd(i),we=w>=5;
    const weekend=we?' we':'',monday=w===0?' mon':'';
    if(ppd>=6)days+=`<div class="dr${weekend}${monday}" style="top:${y(i)}px;height:${ppd}px"></div>`;
    else if(w===0)days+=`<div class="dr mon" style="top:${y(i)}px;height:0"></div>`;
    if(every===1||dd(i)==='01'||i===TODAY||(w===0&&Math.round((i-weekOf(from))/7)%(every/7)===0)){
      const today=i===TODAY?' today':'',top=y(i)+ppd/2,dayName=ppd>=16?`<em>${dow(i)}</em>`:'';
      days+=`<div class="dn${monday}${weekend}${today}" style="top:${top}px">${dayName}<b>${dd(i)}</b></div>`;
    }
  }
  // A goal is a dot on its date (deadline, last day, or the day it was done); webinars are yellow diamonds.
  // Labels that would overlap move apart around their dots and keep a thin leader to the exact date.
  const pv=U.arrowDrag,shift=g=>pv&&pv.id===g.id?pv.delta:0;
  const items=[...gs.map(g=>({kind:'goal',g,day:(ctx.isDone(g)?(g.doneOn??g.e):(g.due??g.e))+shift(g)})),...ctx.calendarPoints().filter(w=>
w.day>=from&&w.day<=to).map(w=>({kind:'web',w,day:w.day}))].sort((a,b)=>a.day-b.day);
  const LH=24,groups=[];
  items.forEach(m=>{m.dotY=y(m.day)+ppd/2;groups.push({items:[m],start:m.dotY});
    while(groups.length>1){const p=groups[groups.length-2],q=groups[groups.length-1];if(q.start>=p.start+p.items.length*LH)break;
const all=[...p.items,...q.items],mean=all.reduce((a,x)=>a+x.dotY,0)/all.length;groups.splice(-2,2,{items:all,start:Math.max(8,mean-(all.length-1)*LH/2)});}});
  groups.forEach(gr=>gr.items.forEach((m,k)=>{m.labelY=gr.start+k*LH;}));
  const leaders=items.filter(m=>Math.abs(m.labelY-m.dotY)>2).map(m=>
`<path d="M 57 ${m.dotY.toFixed(1)} C 61 ${m.dotY.toFixed(1)} 59 ${m.labelY.toFixed(1)} 64 ${m.labelY.toFixed(1)}"/>`).join('');
  const sel=gs.find(g=>g.id===(pv?.id||U.sel)&&!ctx.isDone(g));
  const ws=U.weekStart;
  const itemMarkup=items.map(m=>arrowItem(m,pv,shift)).join('');
  const dragging=pv?' dragging':'',todayTop=y(TODAY),weekTop=y(ws),weekHeight=7*ppd;
  const pastTop=y(from),pastHeight=todayTop-pastTop,futureHeight=y(to)-todayTop;
  const selectedSpan=sel?arrowSpan(sel,y,shift,ppd):'';
  const quickTop=U.quick?y(U.quick.s):0,quickHeight=U.quick?Math.max(10,(U.quick.e-U.quick.s+1)*ppd):0;
  const quick=U.quick?`<div class="qpend" style="top:${quickTop}px;height:${quickHeight}px"></div>`:'';
  const title=t('Клик — неделя этой даты · правая кнопка — новая цель · тяни точку — перенос');
  return `<div class="arrow${dragging}" data-act="arrow-jump" style="height:${h}px" title="${title}">`+
    `${months}${days}<div class="past" style="height:${todayTop}px"></div>
   <div class="wkband" style="top:${weekTop}px;height:${weekHeight}px"></div>
   <div class="spine past" style="top:${pastTop}px;height:${pastHeight}px"></div>`+
    `<div class="spine future" style="top:${todayTop}px;height:${futureHeight}px"></div>
   ${selectedSpan}
   <div class="now" style="top:${todayTop}px"></div>
   ${quick}
   <svg class="leaders" style="height:${h}px" aria-hidden="true">${leaders}</svg>
   ${itemMarkup}</div>`;
}
function arrowSpan(g,y,shift,ppd){
  const top=y(g.s+shift(g)),height=(g.e-g.s+1)*ppd,color=ctx.goalColor(g);
  return `<div class="span" data-gl data-goal="${esc(g.id)}" style="top:${top}px;height:${height}px;--c:${color}"`+
    ` title="${esc(g.title)} · ${ddmm(g.s)}–${ddmm(g.e)}"></div>`;
}
// The slider is as tall as the arrow; its height is read after each render.
const ppdToY=()=>30+(1-Math.log(U.ppd/PPD_MIN)/Math.log(PPD_MAX/PPD_MIN))*(ctx.D.SCALER_H-60);
function placeThumb(){const sc=ctx.$('[data-scaler]');if(!sc||!sc.clientHeight)return;ctx.D.SCALER_H=sc.clientHeight;
sc.querySelector('.thumb').style.top=ppdToY()-15+'px';const l=sc.querySelector('.lbl');if(l)l.style.top=ppdToY()+'px';}
const scaleWord=()=>U.ppd>=26?t("дни"):U.ppd>=12?t("неделя"):U.ppd>=7?t("месяц"):t("квартал");
function scaler(){
  const top=ppdToY(),thumb=top-15,scale=Math.round(U.ppd);
  const label=ctx.D.scaling?`<span class="lbl" style="top:${top}px">${scaleWord()}</span>`:'';
  return `<div class="scaler" role="slider" tabindex="0" aria-valuemin="5" aria-valuemax="40" aria-valuenow="${scale}" `+
    `aria-label="${t('Масштаб стрелы')}" data-scaler title="${t('Тяни ползунок: вверх — крупнее, вниз — мельче')}">`+
    `<span class="end t">+</span><span class="rail"></span><span class="thumb" style="top:${thumb}px">⇕</span>`+
    `<span class="end b">−</span>${label}</div>`;
}
function projectRow(p){
  const all=S.goals.filter(g=>g.p===p.id),ok=all.filter(ctx.isDone).length;
  const colorName=ctx.paletteName(S.calendarPalette.find(c=>c.color?.toLowerCase()===p.c?.toLowerCase())||{name:p.c});
  const checked=p.hidden?'':' checked',width=all.length?ok/all.length*100:0;
  return `<div class="prow" style="--c:${p.c}"><input type="checkbox" data-field="project.show" data-id="${esc(p.id)}"${checked} `+
    `aria-label="${t('Показывать')} ${esc(p.name)}"><span class="dot" title="${esc(colorName)}" aria-label="${esc(colorName)}">`+
    `</span><button class="pn" data-act="project-edit" data-arg="${esc(p.id)}" title="${t('Изменить проект')}">${esc(p.name)}</button>`+
    `<em>${t('пройдено')} ${ok} ${t('из')} ${all.length}</em></div><div class="prog" style="--c:${p.c};--w:${width}%"><i></i></div>`;
}
function projectsPanel(){
  const rows=S.projects.map(projectRow).join('')||`<p class="empty-note">${t('Проектов пока нет.')}</p>`;
  return `<aside class="panel projects"><div class="ph"><h2>${t("Проекты")}</h2>`+
`<button class="link" data-act="new-project">${t("+ Проект")}</button>`+
`</div>${rows}<div class="zoom">`+
`<button data-act="zoom-month">${t("весь месяц")}</button>`+
`<span>${t("колесо — листать")}<br>${t("ползунок — масштаб")}</span></div><div class="arrowbox">`+
`<div class="av" id="av">${arrow()}</div>${scaler()}</div></aside>`;
}
Object.assign(ctx,{arrow,placeThumb,scaler,projectsPanel,ppdToY,scaleWord});
}
