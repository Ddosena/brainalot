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
export function addCubeButton(ctx,id,inDay=false){
  return `<button class="addcap" data-act="new-cube" data-arg="${ctx.esc(id)}" title="${inDay?t('Добавить кубик в этот день'):t('Добавить кубик в цель')}">`+
    `<b>+</b><small>${t('кубик')}</small></button>`;
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
  const hint=horizontal?` title="${title} · ${time}"`:'';
  return `<div class="${axis}b${cls}"${cube}${dragAttr} style="${geometry};--c:${ctx.colorOf(e)};--p:${e.pct}%"${hint}>`+
    `${first}<i></i>${mark}${title}<small>${time}</small>${last}</div>`;
}
export function installPool(ctx){
  const {S,U,esc}=ctx;
  const activeGoals=()=>S.goals.filter(g=>!ctx.isDone(g)&&ctx.visible(g));
  function poolGoals(day){
    const selected=activeGoals().find(g=>g.id===U.poolGoal);
    if(selected)return [selected];
    const today=activeGoals().filter(g=>g.s<=day&&g.e>=day);
    return U.screen==='day'?[today[0]||activeGoals()[0]].filter(Boolean):today;
  }
  function projectOptions(project, list, selected){
    const goals=list.filter(g=>g.p===project.id);
    if(!goals.length)return '';
    const options=goals.map(g=>{
      const chosen=g.id===selected?.id?' selected':'';
      return `<option value="${esc(g.id)}"${chosen}>${esc(g.title)}</option>`;
    }).join('');
    return `<optgroup label="${esc(project.name)}" style="color:${project.c}">${options}</optgroup>`;
  }
  function goalPicker(){
    const list=activeGoals(),selected=poolGoals(U.day)[0];if(!list.length)return '';
    const options=S.projects.filter(p=>!p.hidden).map(p=>projectOptions(p,list,selected)).join('');
    return `<div class="goal-picker" style="--c:${ctx.goalColor(selected)}"><span aria-hidden="true"></span>`+
      `<select data-pool-goal aria-label="${t('Выбрать цель')}">${options}</select></div>`;
  }
  Object.assign(ctx,{possibleOn:day=>possibleOn(S.cubes,ctx.iso(day)),poolGoals,goalPicker});
}
