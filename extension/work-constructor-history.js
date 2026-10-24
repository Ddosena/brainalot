import { esc } from "./work-icons.js";
import { t } from "./i18n.js";
import { dateLabel,minutes,typeOf,COLORS,TYPES,shiftDate,monday,formatReadiness,clamp } from "./work-constructor-model.js?v=constructor-20261023-a";
import { renderGoalAward } from "./work-goal-art.js";
import {readableCardContents,compactCardMinutes,readableSource} from "./work-readable-cubes.js";
const safe=value=>esc(String(value??""));
const check='<svg class="cc-completion-check" viewBox="0 0 12 12" aria-hidden="true"><path d="m2 6 2.5 2.5L10 3"/></svg>';
export const goalEarned=goal=>goal?.status!=="cancelled"&&(goal?.status==="completed"||goal?.achieved||goal?.display_status==="achieved");
export function dayAwards(planning,date){return (planning?.result?.constructor?.goals||[]).filter(goal=>goalEarned(goal)&&(goal.scope==="day"?goal.date===date:goal.scope==="week"&&goal.date<=date&&shiftDate(goal.date,6)>=date));}
export function awardArt(entity,{preview=false}={}){const variant=entity?.award_variant||(preview?'enamel':null);return variant?'<span class="cc-goal-award-art'+(preview?' is-preview':'')+'" data-scope="'+(entity.scope==="week"?"week":"day")+'" data-award-variant="'+safe(variant)+'" aria-hidden="true">'+renderGoalAward(variant,entity.scope)+'</span>':"";}
export function previousWeekAward(planning,date){const start=shiftDate(monday(date),-7);return (planning?.result?.constructor?.goals||[]).find(g=>g.scope==='week'&&g.date===start&&goalEarned(g))||null;}
export function currentWeekProgress(planning,date){const start=monday(date),goal=(planning?.result?.constructor?.goals||[]).find(g=>g.scope==='week'&&g.date===start);return {start,end:shiftDate(start,6),goal,percent:clamp(goal?.percent||0,0,100)};}
export function historyCubes(planning,{includeArchived=false}={}){
  const read=planning?.result?.constructor||{},map=new Map();
  for(const c of Object.values(read.goal_cubes||{}))if(!c.alias_of)map.set(c.canonical_id||c.id,{...c,id:c.canonical_id||c.id});for(const c of Object.values(read.goal_cubes||{}))if(c.alias_of&&!map.has(c.canonical_id))map.set(c.canonical_id,{...c,id:c.canonical_id});
  for(const c of read.cubes||[])map.set(c.id,{...map.get(c.id),...c});
  for(const [id,report] of Object.entries(planning?.completed_cubes||{})){
    const meta=map.get(id)||(planning.result?.cubes||[]).find(c=>c.id===id)||planning.constructor?.cubes?.[id]||{},task=planning.tasks?.find(task=>task.id===report.task_id),details=planning.cube_details?.[id]||{};
    map.set(id,{...meta,...details,id,kind:meta.kind||"task",task_id:meta.task_id||report.task_id,title:details.title||meta.title||task?.title||"",minutes:meta.minutes??report.planned_minutes??0,progress_percent:report.percent,actual_minutes:report.actual_minutes||0,report_date:report.date});
  }
  return [...map.values()].filter(c=>includeArchived||!c.archived_goal_member||c.report_date||Number(c.progress_percent)>0||Number(c.actual_minutes)>0).map(c=>({...c,type:c.type||typeOf(c),taskTitle:planning.tasks?.find(task=>task.id===c.task_id)?.title||"",course:planning.tasks?.find(task=>task.id===c.task_id)?.course||""}));
}
export function goalMembers(planning,goal){const ids=new Set((goal?.cube_ids||[]).map(id=>planning?.result?.constructor?.goal_cubes?.[id]?.canonical_id||id));return historyCubes(planning,{includeArchived:true}).filter(c=>ids.has(c.id));}
export function monthGoals(planning,month){return (planning?.result?.constructor?.goals||[]).filter(g=>g.date?.startsWith(month)&&(g.achieved||g.status==="completed"||g.status==="cancelled"||g.display_status==="achieved"));}
export function memberDays(cubes){const groups=new Map();for(const c of cubes){const date=c.report_date||c.selected_date||c.planned_date;if(!date)continue;if(!groups.has(date))groups.set(date,[]);groups.get(date).push(c);}return [...groups].sort(([a],[b])=>a.localeCompare(b)).map(([date,cubes])=>({date,cubes}));}
export function weightedReadiness(cubes){
  const weighted=cubes.filter(c=>Number.isFinite(Number(c.minutes))&&Number(c.minutes)>0),sum=weighted.reduce((s,c)=>s+Number(c.minutes),0);
  if(!sum)return 0;if(weighted.every(c=>Number(c.progress_percent)>=100))return 100;
  return Math.min(99.9,Math.round(weighted.reduce((s,c)=>s+Number(c.minutes)*clamp(c.progress_percent,0,100),0)/sum));
}
export const statusLabel=goal=>goal?.status==="cancelled"?t("Неактуальна"):goal?.status==="completed"?t("Завершена вручную"):goal?.achieved||goal?.display_status==="achieved"?t("Достигнута"):t("В работе");
function token(c,{history=false,pending=false}={}){
  const done=Number(c.progress_percent)>=100,type=c.type||typeOf(c);
  const label=c.title+' · '+readableSource(c,TYPES[type])+' · '+formatReadiness(c.progress_percent)+'% · '+t('Факт')+' '+minutes(c.actual_minutes||0);
  return '<button class="'+(history?'cc-history-member ':'cc-trophy ')+'rc-card '+(done?'is-complete':'is-unfinished')+'" data-action="'+(history?'history-inspect':'trophy-inspect')+'" data-id="'+safe(c.id)+'" data-ctooltip="'+safe(c.title+' · '+readableSource(c,TYPES[type])+' · '+minutes(c.minutes))+'" aria-label="'+safe(label)+'" '+(pending?'style="visibility:hidden"':'')+'>'+readableCardContents(c,{finished:done,typeLabel:TYPES[type]||TYPES.task,duration:minutes(compactCardMinutes(c,done))})+'</button>';
}
export const SHELF_STYLES=[['rail',t('Рейка')],['steps',t('Ступени')],['cabinet',t('Витрина')]];
export function pedestalShelf(planning,date,pending=new Set()){
  const completed=historyCubes(planning).filter(c=>Number(c.progress_percent)>=100&&(c.report_date||c.selected_date||c.planned_date)===date);
  return '<section class="cc-pedestal-shelf cc-cube-shelf" data-testid="completed-pedestals"><header><h2>'+safe(t('Готово'))+'</h2><span>'+completed.length+'</span></header><div class="cc-shelf-objects">'+(completed.map(c=>token(c,{pending:pending.has(c.id)})).join('')||'<p class="cc-empty-shelf">'+safe(t('Здесь появятся завершённые кубики'))+'</p>')+'</div></section>';
}
export function awardsPanel(planning,date){
  const goals=planning?.result?.constructor?.goals||[],day=goals.find(g=>g.scope==='day'&&g.date===date&&goalEarned(g)),previous=previousWeekAward(planning,date),start=shiftDate(monday(date),-7);
  if(!day&&!previous)return '';
  const item=(goal,scope,label)=>!goal?'':'<div class="rc-earned-award">'+(goal?'<button data-action="month-goal" data-id="'+safe(goal.id)+'" aria-label="'+safe(label+' · '+(goal.title||'')+' · '+formatReadiness(goal.percent)+'%')+'">'+awardArt(goal)+'</button>':'')+'<span>'+safe(label)+(goal?'':' · '+safe(t('пока нет')))+'</span></div>';
  return '<aside class="cc-awards-panel" aria-label="'+safe(t('Награды'))+'">'+item(day,'day',t('Медаль дня'))+item(previous,'week',t('Кубок')+' '+dateLabel(start)+' — '+dateLabel(shiftDate(start,6)))+'</aside>';
}
export function monthHtml(planning,month,path=[]){
  const all=historyCubes(planning),goals=planning?.result?.constructor?.goals||[],step=path.at(-1),goal=step?.goalId?goals.find(g=>g.id===step.goalId):null,fromGoal=goal?goalMembers(planning,goal):[],goalTitle=goal?.title||(goal?.scope==="week"?t("Недельная задача"):t("Дневная задача"));
  let content="";
  if(!step){
    const trophies=monthGoals(planning,month);
    const card=g=>{const art=goalEarned(g)?awardArt(g):"";return '<button class="cc-goal-trophy '+(g.status==="cancelled"?"is-cancelled":"")+(art?' has-award':'')+'" data-scope="'+(g.scope==="week"?"week":"day")+'" data-action="month-goal" data-id="'+safe(g.id)+'">'+art+'<span>'+safe(g.scope==="week"?t("Недельная задача"):t("Дневная задача"))+" · "+safe(dateLabel(g.date))+'</span><strong>'+safe(g.title||dateLabel(g.date,true))+'</strong><small>'+safe(statusLabel(g))+" · "+formatReadiness(g.percent)+'% · '+(g.cube_ids||[]).length+" "+safe(t("куб."))+'</small></button>';};
    const victories=trophies.filter(g=>g.status!=="cancelled"),cancelled=trophies.filter(g=>g.status==="cancelled");
    content='<div class="cc-month-goals">'+victories.map(card).join("")+'</div>'+(cancelled.length?'<h3 class="cc-cancelled-heading">'+safe(t("Неактуальные задачи"))+'</h3><div class="cc-month-goals">'+cancelled.map(card).join("")+'</div>':"")+(!trophies.length?'<p class="cc-history-empty">'+safe(t("В этом месяце пока нет достигнутых или закрытых задач."))+'</p>':"");
    const days=memberDays(all.filter(c=>Number(c.progress_percent)>=100&&(c.report_date||c.selected_date||c.planned_date)?.startsWith(month)));
    content+='<section class="cc-history-days"><h2>'+safe(t("Кубики по дням"))+'</h2>'+days.map(day=>'<button data-action="month-day" data-date="'+day.date+'"><b>'+safe(dateLabel(day.date,true))+'</b><span>'+day.cubes.length+" "+safe(t("куб."))+'</span></button>').join("")+'</section>';
  }else if(goal?.scope==="week"&&!step.date){
    content='<header class="cc-history-heading">'+(goalEarned(goal)?awardArt(goal):"")+'<h2>'+safe(goalTitle)+'</h2><p>'+safe(statusLabel(goal))+" · "+formatReadiness(goal.percent)+'%</p></header><div class="cc-history-days">'+memberDays(fromGoal).map(day=>'<button data-action="month-day" data-date="'+day.date+'" data-goal-id="'+safe(goal.id)+'"><b>'+safe(dateLabel(day.date,true))+'</b><span>'+day.cubes.length+" "+safe(t("куб."))+" · "+formatReadiness(weightedReadiness(day.cubes))+'%</span></button>').join("")+'</div>';
  }else{
    const date=step.date||goal?.date,cubes=goal?fromGoal.filter(c=>(c.report_date||c.selected_date||c.planned_date)===date):all.filter(c=>(c.report_date||c.selected_date||c.planned_date)===date);
    content='<header class="cc-history-heading">'+(goalEarned(goal)?awardArt(goal):"")+'<h2>'+safe(goal?goalTitle:dateLabel(date,true))+'</h2><p>'+safe(dateLabel(date,true))+(goal?" · "+safe(statusLabel(goal)):"")+' · '+formatReadiness(weightedReadiness(cubes))+'%</p></header><div class="cc-history-members">'+cubes.map(c=>token(c,{history:true})).join("")+'</div><p class="cc-history-help">'+safe(t("Готовность показана заливкой. Фактическое время и подробности — при открытии кубика."))+'</p>';
  }
  return '<section class="cc-month" data-testid="month-history"><header><h2>'+safe(t("Результаты месяца"))+'</h2><input type="month" data-field="history-month" value="'+safe(month)+'" aria-label="'+safe(t("Месяц истории"))+'"></header><nav class="cc-history-breadcrumbs"><button data-action="month-root">'+safe(t("Месяц"))+'</button>'+path.map((part,index)=>'<span>/</span><button data-action="month-path" data-index="'+index+'">'+safe(part.date?dateLabel(part.date):part.goalId?.startsWith("week:")?t("Неделя"):t("День"))+'</button>').join("")+'</nav>'+content+'</section>';
}
