import {readableCardDimensions} from "./work-readable-cubes.js";
import {sourceImportance} from "./work-source-importance.js";
import {isDeletedCube} from './work-deletions.js';
import { t, localeTag } from "./i18n.js";
export const START=480, END=1320;
const number=value=>Number.isFinite(Number(value))?Number(value):0;
export const clamp=(value,min,max)=>Math.max(min,Math.min(max,number(value)));
export function formatReadiness(value,locale=localeTag()){
  const percent=clamp(value,0,100),display=percent<100?Math.min(99.9,Math.round(percent*10)/10):100;
  return new Intl.NumberFormat(locale,{maximumFractionDigits:1}).format(display);
}
export function cubeDimensions(minutes,variant="day"){return readableCardDimensions(undefined,variant);}
export function dayLoadReference(planning,date,summary){
  const recommendation=Number(planning?.result?.days?.find(day=>day.date===date)?.total_work_minutes)||0,budget=Math.max(0,Number(summary?.budget)||0),total=Math.max(0,Number(summary?.total)||0);
  return {total,budget,recommendation,fill:budget?clamp(total/budget*100,0,100):total?100:0,reference:budget?clamp(recommendation/budget*100,0,100):0};
}
// Read-only explanation of the canonical energy charge. Completed work uses
// its actual time (or the plan when no actual exists); unfinished selections
// retain their whole reservation, including actual overruns. Facts returned
// to the pool still belong to their report date.
export function dayLoadBreakdown(model){
  const summary=model.summary||{},date=model.date,selected=new Set((model.selected||[]).map(c=>c.id)),byId=new Map();
  for(const c of [...(model.all||[]),...(model.selected||[])])byId.set(c.canonical_id||c.id,c);
  const result={calendar:number(summary.fixed),unfinished:0,completedActual:0,completedEstimate:0,returnedActual:0,returnedEstimate:0,other:0,total:number(summary.total),plannedSelected:number(summary.selected),actual:number(summary.actual)};
  const required=new Set((model.events||[]).filter(e=>e.required).map(e=>e.stable_id||e.occurrence_id||e.id));
  for(const c of byId.values()){
    const chosen=selected.has(c.id),reportHere=c.report_date===date;if(!chosen&&!reportHere)continue;
    const plan=Math.max(0,number(c.minutes)),actual=Math.max(0,number(c.actual_minutes)),percent=clamp(c.progress_percent,0,100);
    let key,charge;
    if(chosen&&percent<100){key='unfinished';charge=Math.max(plan,actual);}
    else if(chosen){key=actual?'completedActual':'completedEstimate';charge=actual||plan;}
    else{key=actual?'returnedActual':'returnedEstimate';const value=plan*percent/100,floor=Math.floor(value);charge=actual||(value-floor===.5?(floor%2?floor+1:floor):Math.round(value));}
    if(c.kind==='calendar'&&required.has(c.source_ref))charge=Math.max(0,charge-plan);
    result[key]+=charge;
  }
  result.other=result.total-result.calendar-result.unfinished-result.completedActual-result.completedEstimate-result.returnedActual-result.returnedEstimate;
  return result;
}
export function dayTimelineEntries(model){
  return {events:(model.events||[]).filter(event=>event.required).map(event=>({id:event.stable_id||event.occurrence_id||event.id,title:event.title||event.summary,start:clock(event.start),minutes:clock(event.end)-clock(event.start),required:true})).filter(event=>event.start!==null&&event.minutes>0),cubes:(model.selected||[]).filter(cube=>cube.start!=null&&Number(cube.progress_percent)<100).map(cube=>({...cube,start:clock(cube.start)})).filter(cube=>cube.start!==null&&cube.minutes>0)};
}
export function trayCapacity(width,items,base=170,variant="day"){
  const sizes=items.map(item=>item.width?item:cubeDimensions(item.minutes,variant)),w=Math.max(1,...sizes.map(s=>s.width)),h=Math.max(1,...sizes.map(s=>s.height)),cellWidth=w+8,rowHeight=h+8,columns=Math.max(1,Math.floor((Math.max(0,width)-8)/cellWidth)),rows=Math.ceil(items.length/columns);
  return {columns,cellWidth,rowHeight,height:Math.max(base,rows*h+Math.max(0,rows-1)*8+16)};
}
export function freeSlots(occupied,start=START,end=END){
  const entries=occupied.filter(x=>x.start!=null).map(x=>({start:Math.max(start,number(x.start)),end:Math.min(end,number(x.start)+number(x.minutes))})).filter(x=>x.end>x.start).sort((a,b)=>a.start-b.start);
  const slots=[];let cursor=start;for(const entry of entries){if(entry.start>cursor)slots.push({start:cursor,end:entry.start});cursor=Math.max(cursor,entry.end);}if(cursor<end)slots.push({start:cursor,end});return slots;
}
export function nearestSlot(occupied,minutes,desired,start=START,end=END){
  if(!Number.isFinite(minutes)||minutes<=0)return null;let nearest=null;
  for(const slot of freeSlots(occupied,start,end)){if(slot.end-slot.start<minutes)continue;const lower=Math.ceil(slot.start/5)*5,upper=Math.floor((slot.end-minutes)/5)*5;if(lower>upper)continue;const candidate=clamp(Math.round(desired/5)*5,lower,upper);if(nearest===null||Math.abs(candidate-desired)<Math.abs(nearest-desired))nearest=candidate;}
  return nearest;
}
export function nearestPlanningSlot(planning,occupied,minutes,desired){
  const start=clock(planning?.settings?.work_start),end=clock(planning?.settings?.work_end);
  return nearestSlot(occupied,minutes,desired,start??START,end??END);
}
export function timelineResizeMinutes(planning,occupied,start,desired,max=240){
  if(!Number.isFinite(start))return null;
  let end=Math.min(END,clock(planning?.settings?.work_end)??END,start+max);
  for(const block of occupied){if(block.start==null||!Number.isFinite(block.start))continue;const stop=block.start+Number(block.minutes||0);if(block.start>=start)end=Math.min(end,block.start);else if(stop>start)end=start;}
  const upper=Math.floor((end-start)/5)*5;
  return upper<5?null:clamp(Math.round(Number(desired)/5)*5,5,upper);
}
export function clock(value){if(typeof value==="number")return Math.floor(value/60).toString().padStart(2,"0")+":"+Math.round(value%60).toString().padStart(2,"0");const match=String(value||"").match(/(?:^|T)(\d{2}):(\d{2})/);return match?Number(match[1])*60+Number(match[2]):null;}
export function minutes(value){const n=Math.max(0,Math.round(number(value)));return n>=60?(n%60?t("{hours} ч {minutes} мин",{hours:Math.floor(n/60),minutes:n%60}):t("{hours} ч",{hours:Math.floor(n/60)})):t("{minutes} мин",{minutes:n});}
export function shiftDate(value,amount){const d=new Date(value+"T12:00:00Z");d.setUTCDate(d.getUTCDate()+amount);return d.toISOString().slice(0,10);}
export function monday(value){return shiftDate(value,-((new Date(value+"T12:00:00Z").getUTCDay()+6)%7));}
export const nextMonday=value=>shiftDate(monday(value),7);
export function dateLabel(value,full=false){if(!value)return "";return new Intl.DateTimeFormat(localeTag(),{day:"numeric",month:"long",...(full?{year:"numeric",weekday:"long"}:{}),timeZone:"UTC"}).format(new Date(value+"T12:00:00Z"));}
export function todayIn(zone="Europe/Moscow"){const fields=Object.fromEntries(new Intl.DateTimeFormat("en-US",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date()).map(p=>[p.type,p.value]));return fields.year+"-"+fields.month+"-"+fields.day;}
export function validUrl(value){try{const url=new URL(String(value||"").trim());return ["http:","https:"].includes(url.protocol)&&url.hostname&&!url.username&&!url.password?url.href:null;}catch{return null;}}
export function normalizeResources(cube){return {links:Array.isArray(cube?.links)?cube.links:[],groups:Array.isArray(cube?.groups)?cube.groups:[]};}
export function resourcesForSave(cube){
  const raw=normalizeResources(cube),validLinks=links=>links.filter(l=>validUrl(l.url)).map(l=>({...l,url:validUrl(l.url)}));
  return {links:validLinks(raw.links),groups:raw.groups.filter(g=>String(g.title||"").trim()).map(g=>({...g,links:validLinks(g.links||[])})),resource_drafts:structuredClone(raw)};
}
export function resourcesForEdit(cube){return structuredClone(cube.resource_drafts||normalizeResources(cube));}
export function launchGroups(cube,groupId=null){
  const resources=normalizeResources(cube),all=[...(resources.links.length?[{id:"links",title:t("Материалы"),color:"cyan",links:resources.links}]:[]),...resources.groups],groups=groupId?all.filter(g=>g.id===groupId):all;
  return groups.map(g=>({...g,links:(g.links||[]).filter(l=>validUrl(l.url)).map(l=>({...l,url:validUrl(l.url)}))})).filter(g=>g.links.length);
}
export function typeOf(cube,segment){
  if(cube.kind&&cube.kind!=="task")return cube.kind;const title=String(segment?.title||cube.phase||cube.title||"").toLowerCase();
  if(/практи|скульпт/.test(title))return "practice";if(/слайд|презентац/.test(title))return "slides";if(/сценар/.test(title))return "script";return "research";
}
export const TYPES={task:t("Работа"),research:t("Исследование"),practice:t("Практика"),slides:t("Слайды"),script:t("Сценарий"),prep:t("Подготовка"),calendar:t("Календарь"),weekly_review:t("Разбор"),brainalot:"Brainalot"};
export const COLORS={task:"#77adc8",research:"#77adc8",practice:"#75bcb1",slides:"#b1a1d1",script:"#c9ac83",prep:"#9bc9b5",calendar:"#c4b18c",weekly_review:"#aabcc7",brainalot:"#b6b8d0"};
export function constructorModel(planning,date,dashboard={}){
  const result=planning.result||{},read=result.constructor||{},canonical=planning.constructor||{},day=read.days?.[date],legacyDay=result.days?.find(d=>d.date===date)||{date},initialized=!!day?.initialized;
  const tasks=planning.tasks||[],segments=planning.task_segments||[],persisted=(read.cubes||[]).filter(cube=>!isDeletedCube(planning,cube)),byId=new Map(persisted.map(c=>[c.id,c]));
  const decorate=c=>{const task=tasks.find(t=>t.id===c.task_id),segment=segments.find(s=>s.id===c.segment_id),report=planning.completed_cubes?.[c.id]||{};return {...c,importance_score:sourceImportance(c,planning,dashboard),minutes:number(c.minutes),title:c.title||task?.title||"",output:c.output||"",steps:c.steps||[],comment:c.comment||"",progress_percent:c.progress_percent??report.percent??0,actual_minutes:c.actual_minutes??report.actual_minutes??0,taskTitle:task?.title||"",course:task?.course||"",segmentTitle:segment?.title||"",type:typeOf(c,segment),...normalizeResources(c)};};
  let selected,possible,events,summary;
  if(initialized){
    selected=(day.selected_cube_ids||[]).map(id=>byId.get(id)).filter(Boolean).map(decorate);
    possible=(day.possible_cube_ids||[]).map(id=>byId.get(id)).filter(Boolean).map(decorate);
    events=day.events||[];summary={budget:day.budget_minutes??300,selected:day.selected_minutes??0,fixed:day.fixed_minutes??0,total:day.total_work_minutes??0,actual:day.actual_minutes??0,credited:day.credited_minutes??0,free:day.available_minutes??0,over:day.over_cap_minutes??0};
  }else{
    selected=(result.cubes||[]).filter(c=>!isDeletedCube(planning,c)&&c.date===date&&(c.manual||c.locked||number(c.progress_percent)>0||number(c.actual_minutes)>0||number(planning.completed_cubes?.[c.id]?.percent)>0||number(planning.completed_cubes?.[c.id]?.actual_minutes)>0)).map(c=>decorate({...c,kind:"task",selected_date:date}));
    // A future day may not be initialized yet, while its weekly proposals already
    // exist canonically. Project them without converting the day or charging work.
    possible=(day?.possible_cube_ids||[]).map(id=>byId.get(id)).filter(Boolean).map(decorate);
    events=(planning.calendar?.events||[]).filter(e=>String(e.start||"").slice(0,10)===date).map(e=>({...e,stable_id:e.occurrence_id||e.id,required:e.category!=="ignore"}));
    for(const prep of legacyDay.prep_cubes||[]){
      const id="constructor:"+prep.id;
      if(day?.hidden_cube_ids?.includes(id)||possible.some(c=>c.id===id||c.kind==="prep"&&c.source_ref===prep.id))continue;
      possible.push(decorate({id,kind:"prep",source_ref:prep.id,candidate:true,title:t("Подготовка к уроку {time}",{time:prep.lesson_start||""}),minutes:prep.minutes,planned_date:date}));
    }
    summary={budget:legacyDay.budget_minutes??legacyDay.day_limit_minutes??planning.settings?.day_limit_minutes??300,selected:legacyDay.builder_selected_minutes??selected.reduce((s,c)=>s+c.minutes,0),fixed:legacyDay.fixed_minutes||0,total:legacyDay.builder_total_work_minutes??legacyDay.total_work_minutes??0,actual:legacyDay.reported_actual_minutes??selected.reduce((s,c)=>s+c.actual_minutes,0),credited:selected.reduce((s,c)=>s+c.minutes*c.progress_percent/100,0),free:legacyDay.builder_available_minutes??legacyDay.unused_capacity_minutes??0,over:legacyDay.builder_over_cap_minutes||0};
  }
  const sources=[["today",t("Сегодня")],["backlog_week",t("На неделе")],["week",t("Ближайшие дни")]].flatMap(([key,label])=>(dashboard[key]||[]).filter(n=>n.kind==="task"&&n.status!=="done").map(n=>decorate({id:"brainalot-"+n.id,kind:"brainalot",source_ref:n.id,title:n.title,minutes:60,output:"",steps:[],comment:"",links:[],groups:[],sourceLabel:label,type:"brainalot",candidate:true})));
  const used=new Set([...selected,...possible,...persisted.filter(c=>c.kind==="brainalot")].map(c=>c.kind+":"+c.source_ref));for(const source of sources)if(!isDeletedCube(planning,source)&&!used.has(source.kind+":"+source.source_ref)&&!day?.hidden_cube_ids?.includes(source.id)){possible.push(source);used.add(source.kind+":"+source.source_ref);}
  const hidden=(day?.hidden_cube_ids||[]).map(id=>byId.get(id)).filter(Boolean).map(decorate),main=day?.main_task_id||legacyDay.focus_task_id||tasks[0]?.id||null,priority=legacyDay.focus_task_id||null;
  return {date,initialized,main,priority,goal:day?.goal||"",positions:day?.positions||canonical.days?.[date]?.positions||{},selected,possible,hidden,events,summary,tasks,segments,pool:result.task_pool||[],all:persisted.map(decorate)};
}
