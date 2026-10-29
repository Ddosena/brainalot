import {t} from '../i18n.js';

function calendarDates(cube){
  return new Set([cube.source_date||cube.planned_date,...(cube.source_occurrences||[]).map(item=>item.date)].filter(Boolean));
}
function occurrenceKeys(cube,date){
  const keys=(cube.source_occurrences||[]).filter(item=>item.date===date&&item.event_id).map(item=>`event:${item.event_id}:${date}`);
  if(!keys.length&&cube.source_event_id)keys.push(`event:${cube.source_event_id}:${date}`);
  return keys;
}
export function possibleOn(cubes,date){
  const seen=new Set(),result=[];
  for(const cube of cubes){
    if(cube.goal||cube.task_id||cube.day!=null||cube.selected_date||cube.pct>=100||cube.progress_percent>=100||cube.repeatable_instance||
cube.available===false||cube.deleted||cube.source_deleted||cube.source_unavailable||['cancelled','canceled','completed'].includes(cube.status))continue;
    if(cube.kind==='calendar_optional'||cube.kind==='calendar'){
      const onDate=calendarDates(cube).has(date);
      const inSpan=cube.all_day&&cube.source_date&&cube.source_end_date&&date>=cube.source_date&&date<cube.source_end_date;
      if(!onDate&&!inSpan)continue;
      const keys=occurrenceKeys(cube,date);
      if(keys.some(key=>seen.has(key)))continue;
      keys.forEach(key=>seen.add(key));
    }
    result.push(cube);
  }
  return result;
}
export function unpinnedPossible(ctx, day){
  const cubes=ctx.possibleOn?ctx.possibleOn(day):ctx.S.cubes.filter(k=>!k.goal&&k.day==null&&!k.repeatable_instance);
  return cubes.filter(k=>!k.pinned);
}
// Owner 2026-10-28: «в плане» is the whole working day — the day's cubes plus timed calendar
// events (lessons, webinars); overlapping events count once.
export function dayPlanMinutes(cubes,events){
  let sum=cubes.reduce((a,k)=>a+k.min,0),end=-Infinity;
  for(const e of [...events].sort((a,b)=>a.at-b.at)){const from=Math.max(e.at,end),to=e.at+e.min;if(to>from){sum+=to-from;end=to;}}
  return sum;
}
export function addCubeButton(ctx,id,inDay=false){
  return `<button class="addcap" data-act="new-cube" data-arg="${ctx.esc(id)}" title="${inDay?t('Добавить кубик в этот день'):t('Добавить кубик в цель')}">`+
    `<b>+</b><small>${t('кубик')}</small></button>`;
}
// Owner 2026-10-28: a timeline cube names its goal at the bottom; a long title keeps its first two meaningful words.
function goalTag(title){
  const text=String(title||'').trim(),words=text.split(/\s+/).filter(w=>w.length>2);
  return text.length>18&&words.length>2?words.slice(0,2).join(' '):text;
}
export function trackBlock(ctx,e,{axis,position,size,live=false}){
  const horizontal=axis==='h',end=e.at+e.min,time=`${ctx.clk(e.at)}–${ctx.clk(end)}`,title=ctx.esc(e.title);
  const geometry=horizontal?`left:${position}%;width:${size}%`:`top:${position}px;height:${size}px`;
  if(e.fixed){
    const fixedTitle=horizontal?`${title} · ${time} ${t('· из календаря')}`:`${title} ${t('· из календаря')}`;
    return `<div class="${axis}b fixed" style="${geometry}" title="${fixedTitle}">${title}<small>${time}</small></div>`;
  }
  const done=e.pct>=100,drag=!e.isGhost&&!done;
  const cls=(e.isGhost?' ghost':'')+(done?' done':'')+(live?' live':'');
  const cube=e.isGhost?'':` data-cube="${ctx.esc(e.id)}"`,dragAttr=drag?` data-drag="${ctx.esc(e.id)}"`:'';
  const resize=side=>drag?`<span class="rz ${side}" data-resize="${side}" data-id="${ctx.esc(e.id)}"></span>`:'';
  const first=resize(horizontal?'l':'t'),last=resize(horizontal?'r':'b'),mark=done?'✓ ':'';
  const goalTitle=e.goal?ctx.goal(e.goal)?.title:'',tag=goalTitle?`<em class="gt">${ctx.esc(goalTag(goalTitle))}</em>`:'';
  const hint=horizontal?` title="${title} · ${time}${goalTitle?' · '+ctx.esc(goalTitle):''}"`:'';
  return `<div class="${axis}b${cls}"${cube}${dragAttr} style="${geometry};--c:${ctx.colorOf(e)};--p:${e.pct}%"${hint}>`+
    `${first}<i></i>${mark}${title}<small>${time}</small>${tag}${last}</div>`;
}
export function installPool(ctx){
  const {S,U,esc}=ctx;
  const activeGoals=()=>S.goals.filter(g=>!ctx.isDone(g)&&ctx.visible(g));
  // Owner 2026-10-29: in Day the goal shelf is a multi-select of goal chips. Until the owner picks,
  // the goals of the day are selected; after that U.poolGoals is exactly the owner's choice (may be empty).
  function poolGoals(day){
    if(U.screen==='day'){
      const active=activeGoals();
      if(Array.isArray(U.poolGoals))return U.poolGoals.map(id=>active.find(g=>g.id===id)).filter(Boolean);
      return active.filter(g=>g.s<=day&&g.e>=day);
    }
    const selected=activeGoals().find(g=>g.id===U.poolGoal);
    if(selected)return [selected];
    return activeGoals().filter(g=>g.s<=day&&g.e>=day);
  }
  function togglePoolGoal(id){
    const current=poolGoals(U.day).map(g=>g.id);
    U.poolGoals=current.includes(id)?current.filter(x=>x!==id):[...current,id];
  }
  // Nearest goals first: those running on the day, then by distance to their dates.
  function nearGoals(day){
    const dist=g=>g.s<=day&&day<=g.e?0:Math.min(Math.abs(g.s-day),Math.abs(g.e-day));
    return activeGoals().sort((a,b)=>dist(a)-dist(b)||a.e-b.e||a.s-b.s);
  }
  function goalChips(){
    const list=nearGoals(U.day);if(!list.length)return '';
    const chosen=new Set(poolGoals(U.day).map(g=>g.id));
    const chips=list.map(g=>{
      const on=chosen.has(g.id),range=g.s===g.e?ctx.ddmm(g.s):`${ctx.ddmm(g.s)}–${ctx.ddmm(g.e)}`;
      // A pill like variant B of the stand: colour dot, name (cut to one size), share done.
      return `<button class="gchip${on?' on':''}" data-act="pool-goal" data-arg="${esc(g.id)}" data-goal="${esc(g.id)}" aria-pressed="${on}" `+
        `title="${esc(g.title)} · ${range} · ${g.pct}%" style="--c:${ctx.goalColor(g)}"><span class="dot"></span><b>${esc(g.title)}</b><small>${g.pct}%</small></button>`;
    }).join('');
    return `<div class="goal-chips" data-scroll-strip tabindex="0" aria-label="${t('Цели')}">${chips}</div>`;
  }


  Object.assign(ctx,{possibleOn:day=>possibleOn(S.cubes,ctx.iso(day)),poolGoals,goalChips,togglePoolGoal});
}
