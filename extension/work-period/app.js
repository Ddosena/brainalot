import {t,localeTag} from '../i18n.js';
import {ui,uiMarkup} from './ui-text.js';
import {periodRequest} from './transport.js';
import {createAutosaveQueue} from './autosave.js';
const DAY_MS = 86400000;
let resolveReady,rejectReady;
const firstModel=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
firstModel.catch(()=>{});
export const ready=()=>firstModel;
const readyListeners=new Set();let modelAnnounced=false;
export function onModelReady(callback){readyListeners.add(callback);if(modelAnnounced)callback();return ()=>readyListeners.delete(callback);}
export const ARROW_TOP_GUTTER=24,ARROW_BOTTOM_GUTTER=96;
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function addDays(date, days) { return new Date(Date.parse(`${date}T12:00:00Z`) + days * DAY_MS).toISOString().slice(0,10); }
export function monday(date) { const day = new Date(`${date}T12:00:00Z`).getUTCDay(); return addDays(date, -(day || 7) + 1); }
export const weekDates = start => Array.from({length:7}, (_, i) => addDays(start, i));
export const windowWeeks = start => Array.from({length:4}, (_, i) => addDays(monday(start), i * 7));
export function dailyLimitMinutes(source,date){return source.settings?.daily_limit_minutes_by_date?.[date] ?? source.settings?.daily_limit_minutes ?? 300;}
export function minutesLabel(minutes) { const n = Math.max(0, Number(minutes) || 0); return `${Math.floor(n / 60) ? ui`${Math.floor(n / 60)} ч` : ''}${n % 60 ? ui` ${n % 60} мин` : ''}`.trim() || t('0 мин'); }
export function periodSpan(start, end, weekStart, visibleStart = weekStart) {
  if (!start || !end || start > end || end < visibleStart || start > addDays(weekStart,6)) return null;
  const first = start > visibleStart ? start : visibleStart;
  const last = end < addDays(weekStart,6) ? end : addDays(weekStart,6);
  return {start: Math.round((Date.parse(first) - Date.parse(weekStart)) / DAY_MS) + 1, end: Math.round((Date.parse(last) - Date.parse(weekStart)) / DAY_MS) + 2};
}
export function placementState(cube, date) { return cube.selected_date === date ? (cube.start ? 'timed' : 'selected') : cube.selected_date ? 'elsewhere' : 'suggestion'; }
export function availableCubes(model, date) {
  const projects = new Set((model.projects || []).filter(project => project.visible !== false).map(project => project.id));
  const knownProjects=new Set((model.projects || []).map(project=>project.id));
  const tasks = new Map((model.tasks || []).map(task => [task.id,task]));
  return (model.cubes || []).filter(cube => {
    const task = tasks.get(cube.task_id);
    const allocations=cube.source_task_allocations || [],compositeVisible=allocations.length && (cube.selected_date===date || allocations.some(part=>!part.project_id || (model.projects || []).find(project=>project.id===part.project_id)?.visible!==false));
    if ((!allocations.length && cube.project_id && knownProjects.has(cube.project_id) && !projects.has(cube.project_id)) || (allocations.length && !compositeVisible) || cube.available === false || task?.owned === false || ['cancelled','canceled','done','completed'].includes(cube.status) || Number(cube.progress_percent) >= 100) return false;
    if (cube.selected_date === date) return true;
    if (cube.kind === 'calendar_optional' && cube.availability !== 'any_day' && cube.calendar_role !== 'evergreen' && (cube.source_date || cube.planned_date) !== date) return false;
    if (['prep','weekly_review'].includes(cube.kind) && cube.planned_date && cube.planned_date !== date) return false;
    if(cube.returned)return true;
    return !task?.period_explicit || ((!task.start || date >= task.start) && (!task.end || date <= task.end));
  });
}
export function cubeParents(cube) {return cube.source_task_allocations?.length ? cube.source_task_allocations : cube.task_id ? [{task_id:cube.task_id,project_id:cube.project_id,minutes:cube.minutes}] : [];}
export function cubePool(cube,source,date) {const parents=cubeParents(cube),tasks=new Map((source.tasks || []).map(task=>[task.id,task]));return cube.kind==='task' && parents.length && parents.every(part=>{const task=tasks.get(part.task_id);return task?.start && task?.end && task.start<=date && task.end>=date;}) ? 'task' : 'other';}
export function dayShelfPools(source,date) {
  const pools={task:[],other:[]},tasks=new Map((source.tasks || []).map(task=>[task.id,task]));
  for(const cube of availableCubes(source,date)){if(['selected','timed'].includes(placementState(cube,date)))continue;const parents=cubeParents(cube),pool=cubePool(cube,source,date),course=cube.kind==='task' && parents.some(part=>tasks.has(part.task_id));if(pool==='task')pools.task.push(cube);else if(!course || cube.returned || cube.source_task_allocations?.length)pools.other.push(cube);}
  pools.other.sort((a,b)=>shelfPriority(a,date)-shelfPriority(b,date));return pools;
}
export function cubeDeletePermission(cube) {if(cube.can_delete!==undefined)return {allowed:cube.can_delete===true,reason:cube.delete_block_reason || t('Удаление недоступно.')};return {allowed:!Number(cube.progress_percent) && !Number(cube.actual_minutes) && !['done','completed','cancelled','canceled'].includes(cube.status),reason:t('У кубика есть факт работы.')};}
export function dayChosenCubes(source,date){return (source.cubes || []).filter(cube=>cube.selected_date===date && !isDone(cube) && ((cube.source_task_allocations || []).length || (source.projects || []).find(project=>project.id===cube.project_id)?.visible!==false) && (source.tasks || []).find(task=>task.id===cube.task_id)?.owned!==false);}
export function mergeEligibility(cubes) {
  if (cubes.length < 2 || cubes.length > 10) return {ok:false,reason:t('Выберите от двух до десяти кубиков.')};
  if (cubes.some(cube => cube.kind !== 'task' || (!cube.task_id && !cube.source_task_allocations?.length) || cube.can_delete===false || Number(cube.progress_percent) > 0 || Number(cube.actual_minutes) > 0 || ['done','completed','cancelled','canceled'].includes(cube.status))) return {ok:false,reason:t('Объединяются только рабочие кубики без факта; разные источники сохраняются отдельно.')};
  return {ok:true,minutes:cubes.reduce((sum,cube) => sum + Number(cube.minutes || 0),0)};
}
export function taskTimelineState(task, today) {
  const status = String(task.status || task.source_status || '').toLowerCase();
  if (['done','completed','complete','finished'].includes(status) || Number(task.progress_percent) >= 100) return {key:'done',label:task.status_label || t('Завершено')};
  if (['active','in-progress','in_progress','started','current'].includes(status) || Number(task.progress_percent) > 0 || Number(task.work_progress_percent) > 0) return {key:'current',label:task.status_label || t('В работе')};
  if ((task.date || task.start) > today) return {key:'future',label:t('Впереди')};
  return {key:'unknown',label:task.source_status || task.status || t('Статус не задан')};
}
export function eventTimeLabel(event) {
  const ordinary = event.all_day ? t('Весь день') : [event.start,event.end].filter(Boolean).join('–');
  if (!event.conflict) return ordinary;
  const choices = (event.alternatives?.length ? event.alternatives : [event]).map(item => {
    if (typeof item === 'string') return item;
    const time = [item.start,item.end].filter(Boolean).join('–');
    const source = {calendar:t('Календарь'),work:'Work'}[item.kind];
    return source ? `${source} ${time}` : time;
  });
  return [...new Set(choices.filter(Boolean))].join(' · ');
}
export function compactEventTimeLabel(event) {return event.all_day ? t('Весь день') : [event.start,event.end].filter(Boolean).join('–');}
const eventTimeDetails=event=>eventTimeLabel(event).replaceAll('Work',t('План'));
export function deadlineLabel(kind) {
  return ({provisional:t('Предварительный срок'),inferred_provisional:t('Предварительный срок'),planning_target:t('Плановый ориентир'),source_sheet:t('Срок по таблице'),source_brief:t('Срок по ТЗ'),source_screenshot:t('Дедлайн по источнику'),confirmed:t('Подтверждённый срок')})[kind] || t('Дата · тип срока не уточнён');
}
export const canMoveCalendarEvent = event=>event.calendar_role === 'flexible' && event.movable === true;
export const calendarOfferLabel = cube=>cube.calendar_role === 'evergreen' ? t('Всегда актуально') : t('Возможное');
export const cubeDurationLabel = cube=>cube.duration_unknown || cube.requires_estimate ? t('Длительность не указана') : `${cube.duration_provisional?'≈':''}${minutesLabel(cube.minutes)}`;
export const shelfPriority = (cube,date)=>cube.kind === 'calendar_optional' ? (cube.calendar_role === 'evergreen' ? 1 : 0) : cube.planned_date === date ? 2 : 3;
export const TIMELINE_PIXELS_PER_MINUTE=2;
export function formatTimelineMinute(value) {const minute=((Math.round(value)%1440)+1440)%1440,day=Math.floor(value/1440);return `${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}${day ? ` +${day}` : ''}`;}
export function timelinePlacement(date,startMinute,minutes) {const day=Math.floor(startMinute/1440),clock=((startMinute%1440)+1440)%1440;return {date:addDays(date,day),start:formatTimelineMinute(clock),minutes};}
export function timelineHitMinute(clientY,canvasTop,startMinute,pixelsPerMinute=TIMELINE_PIXELS_PER_MINUTE,grabMinutes=0,step=5) {return Math.round((startMinute+(clientY-canvasTop)/pixelsPerMinute-grabMinutes)/step)*step;}
export function resizedTimelineSpan(startMinute,minutes,edge,targetMinute,step=5) {
  const end=startMinute+minutes,target=Math.round(targetMinute/step)*step;
  return edge==='start' ? {startMinute:Math.min(end-1,target),minutes:end-Math.min(end-1,target)} : {startMinute,minutes:Math.max(1,target-startMinute)};
}
export function cubeResizePermission(cube) {
  if (Number(cube.actual_minutes)>0 || Number(cube.progress_percent)>0) return {allowed:false,reason:t('У кубика уже есть факт работы: длительность закреплена.')};
  if (cube.can_resize===true) return {allowed:true,reason:''};
  return {allowed:false,reason:cube.resize_block_reason || t('Длительность сохраняет объём задачи или источника. Изменить её здесь нельзя.')};
}
export function timelineGeometry(cubes,events,settings={}) {
  const blocks=[...cubes.map(cube=>({kind:'cube',id:cube.id,item:cube,startMinute:timeMinutes(cube.start),minutes:Number(cube.minutes)})),...events.filter(event=>event.start && !event.all_day).map(event=>{let minutes=Number(event.duration_minutes);if(!minutes){minutes=timeMinutes(event.end)-timeMinutes(event.start);if(minutes<0)minutes+=1440;}return {kind:'event',id:event.id,item:event,startMinute:timeMinutes(event.start),minutes};})].filter(block=>block.minutes>0).sort((a,b)=>a.startMinute-b.startMinute || a.id.localeCompare(b.id));
  const startMinute=Math.floor(Math.min(timeMinutes(settings.day_start || '11:00'),...blocks.map(block=>block.startMinute))/60)*60,endMinute=Math.ceil(Math.max(timeMinutes(settings.day_end || '20:00'),...blocks.map(block=>block.startMinute+block.minutes))/60)*60;
  let component=[],laneEnds=[],componentEnd=-Infinity;
  const finish=()=>{for(const block of component)block.laneCount=laneEnds.length;component=[];laneEnds=[];};
  for(const block of blocks){if(block.startMinute>=componentEnd)finish();let lane=laneEnds.findIndex(end=>end<=block.startMinute);if(lane<0)lane=laneEnds.length;laneEnds[lane]=block.startMinute+block.minutes;block.lane=lane;component.push(block);componentEnd=Math.max(component.length===1 ? -Infinity : componentEnd,block.startMinute+block.minutes);}
  finish();
  return {startMinute,endMinute,pixelsPerMinute:TIMELINE_PIXELS_PER_MINUTE,height:(endMinute-startMinute)*TIMELINE_PIXELS_PER_MINUTE,blocks:blocks.map(block=>({...block,top:(block.startMinute-startMinute)*TIMELINE_PIXELS_PER_MINUTE,height:block.minutes*TIMELINE_PIXELS_PER_MINUTE}))};
}
export function eventSummary(events) {
  const prioritized = [...events].sort((a,b) => Number(Boolean(b.conflict)) - Number(Boolean(a.conflict)));
  return {primary:prioritized[0] || null,count:events.length,additional:Math.max(0,events.length - 1),hasConflict:events.some(event=>event.conflict)};
}
export function currentProjectTasks(source, projectId) {
  return (source.tasks || []).filter(task=>task.project_id === projectId && task.owned !== false && task.start && task.end && task.start <= source.today && task.end >= source.today && !['done','completed','cancelled','canceled'].includes(task.status) && Number(task.progress_percent || 0) < 100);
}
export function periodTasksForWeek(source, weekStart, maskPast = true) {
  const visibleStart = maskPast && source.today > weekStart ? source.today : weekStart;
  const visibleProjects = new Set((source.projects || []).filter(project=>project.visible !== false).map(project=>project.id));
  return (source.tasks || []).filter(task=>visibleProjects.has(task.project_id) && task.owned !== false).map(task=>({task,span:periodSpan(task.start,task.end,weekStart,visibleStart)})).filter(item=>item.span);
}
export function timeArrowDate(task) {
  if (task.deadline) return {date:task.deadline,basis:'deadline',label:deadlineLabel(task.deadline_kind)};
  if (task.date) return {date:task.date,basis:'source_date',label:t('Дата из источника')};
  if (task.start) return {date:task.start,basis:'planning_start',label:t('Начало планового периода')};
  return null;
}
export function timeArrowStatus(task,today) {
  const sourceDone = task.full_completion===true;
  const workDone = task.work_progress_percent !== null && task.work_progress_percent !== undefined && Number(task.work_progress_percent) >= 100;
  if (sourceDone) return {key:'done',label:t('Задание полностью готово по источнику'),sourceDone,workDone};
  if (task.deadline && task.deadline < today && (['confirmed','source_sheet','source_brief','owner_defined','owner_override'].includes(task.deadline_kind) || (task.read_only && task.deadline_kind==='source_screenshot'))) return {key:'overdue',label:t('Просрочено'),sourceDone:false,workDone};
  if (Number(task.work_progress_percent) > 0) return {key:'current',label:t('Есть прогресс Work'),sourceDone:false,workDone};
  const dated = timeArrowDate(task);
  return {key:dated?.date >= today ? 'upcoming' : 'unknown',label:dated?.date >= today ? t('Впереди') : t('Статус не подтверждён'),sourceDone:false,workDone};
}
export function shortTaskLabel(title) {
  const quoted = String(title || '').match(/«([^»]+)»/);
  const clean = (quoted?.[1] || title || t('Задача')).replace(/^(Презентация к [^«]+|Дополнительный материал|Итоговый проект\.?|Введение в)\s*/i,'').trim();
  const words = clean.split(/\s+/).slice(0,4);
  while (words.length > 1 && (words.join(' ').length > 32 || /^(и|в|с|по|для|к|из|на)$/i.test(words.at(-1)))) words.pop();
  return words.join(' ') || clean || String(title || t('Задача'));
}
export function arrowMonthBands(start,end,pixelsPerDay) {
  const result=[];let month=`${start.slice(0,7)}-01`;
  while(month<=end){const nextDate=new Date(`${month}T12:00:00Z`);nextDate.setUTCMonth(nextDate.getUTCMonth()+1);const next=nextDate.toISOString().slice(0,10),first=month<start ? start : month,last=next>end ? addDays(end,1) : next;result.push({month,start:first,end:last,top:24+(Date.parse(first)-Date.parse(start))/DAY_MS*pixelsPerDay,height:(Date.parse(last)-Date.parse(first))/DAY_MS*pixelsPerDay});month=next;}
  return result;
}
export function arrowZoomAnchor(scrollTop,cursorY,oldPixelsPerDay,newPixelsPerDay) {return Math.max(0,24+(scrollTop+cursorY-24)/oldPixelsPerDay*newPixelsPerDay-cursorY);}
export function arrowZoomBounds(days,viewportHeight) {return {min:Math.min(48,Math.max(Number.EPSILON,(viewportHeight-ARROW_TOP_GUTTER-ARROW_BOTTOM_GUTTER)/Math.max(1,days))),max:48};}
export function placeArrowLabelBoxes(items,height,todayY,{gap=8,padding=8,todayHeight=20}={}) {
  // A caption belongs to its real date. Suppress crowded captions; never move
  // one into a different day and connect it back with a vertical leader.
  const reserved=[{top:todayY-todayHeight/2,height:todayHeight}],result=[];
  const candidates=[...items].sort((a,b)=>(b.priority || 0)-(a.priority || 0) || a.y-b.y);
  for(const item of candidates){
    const anchorOffset=item.anchorOffset ?? item.height/2,top=item.y-anchorOffset;
    if(top<padding || top+item.height>height-padding)continue;
    if(reserved.some(box=>top<box.top+box.height+gap && top+item.height+gap>box.top))continue;
    const placed={...item,top,anchorOffset,center:top+item.height/2};
    reserved.push(placed);result.push(placed);
  }
  return result.sort((a,b)=>a.y-b.y);
}
export function projectArrowModel(source,view={}) {
  const projects = (source.projects || []).filter(project=>project.visible !== false);
  const rows=projects.flatMap(project=>{
    const tasks=project.tasks || [],indexed=new Map(tasks.map(task=>[task.id,task]));
    if(Array.isArray(project.timeline_entries))return project.timeline_entries.filter(entry=>entry.visible!==false && entry.owned!==false && indexed.get(entry.task_id)?.owned!==false).map(entry=>{const original=indexed.get(entry.task_id) || {id:entry.task_id,title:entry.title},task={...original,title:(entry.owner_override || project.kind==='goal' || entry.event_kind==='milestone') ? entry.title || entry.label || original.title : original.title,full_completion:Object.hasOwn(entry,'full_completion') ? entry.full_completion : original.full_completion,personal_goal:project.kind==='goal'};return {task,project,entryId:entry.id,eventKind:entry.event_kind,date:entry.date,basis:entry.event_kind==='start' ? 'window_start' : entry.event_kind==='deadline' ? 'deadline' : 'milestone',label:entry.event_label || (entry.event_kind==='start' ? t('Начало разработки') : entry.event_kind==='deadline' ? t('Дедлайн') : t('Отметка')),state:timeArrowStatus(task,source.today),entry};});
    return tasks.filter(task=>task.owned!==false && task.timeline_visible!==false && !task.auxiliary && !task.parent_id).map(task=>({task,project,entryId:task.id,eventKind:task.deadline ? 'deadline' : 'source_date',...(timeArrowDate(task) || {date:null,basis:null,label:t('Дата не задана')}),state:timeArrowStatus(task,source.today)}));
  });
  const undated=[...rows.filter(row=>!row.date),...projects.flatMap(project=>Array.isArray(project.timeline_entries) ? (project.tasks || []).filter(task=>task.owned!==false && (project.undated_task_ids || []).includes(task.id)).map(task=>({task,project,date:null,basis:null,label:t('Текущий срок не указан'),state:timeArrowStatus(task,source.today)})) : [])];
  const dated = rows.filter(row=>row.date).sort((a,b)=>a.date.localeCompare(b.date) || a.project.id.localeCompare(b.project.id) || a.task.id.localeCompare(b.task.id));
  const range = [...dated.map(row=>row.date),...projects.flatMap(project=>[project.start,project.end]).filter(Boolean),source.today].sort();
  const start = view.rangeStart || range[0], end = view.rangeEnd || range.at(-1), days = Math.max(0,Math.round((Date.parse(end)-Date.parse(start))/DAY_MS));
  const byDate = new Map();
  dated.forEach(row=>{if (!byDate.has(row.date)) byDate.set(row.date,[]);byDate.get(row.date).push(row);});
  const groups = [...byDate].map(([date,items])=>({date,items}));
  const pixelsPerDay=view.pixelsPerDay || Math.max(8,groups.length*44/Math.max(1,days));
  const y = date=>24 + (Date.parse(date)-Date.parse(start)) / DAY_MS * pixelsPerDay;
  groups.forEach(group=>{group.y=y(group.date);group.showLabel=true;group.labelY=group.y;});
  const markerDiameter=Math.min(18,pixelsPerDay*2),markerGap=Math.min(2,pixelsPerDay);
  groups.forEach((group,index)=>{const recent=groups.slice(0,index).filter(other=>group.y-other.y<markerDiameter+markerGap-1e-7),start=group.items.every(row=>row.eventKind==='start'),candidates=start ? [70,94,118] : [94,70,118];group.nodeX=candidates.find(x=>recent.every(other=>Math.abs(other.nodeX-x)>=markerDiameter+markerGap)) ?? 118;group.markerDiameter=markerDiameter;});
  const tickDates = new Set([start,end]);
  if(pixelsPerDay>=22){for(let date=start;date<=end;date=addDays(date,1))tickDates.add(date);}else if(pixelsPerDay>=7){for(let date=monday(start);date<=end;date=addDays(date,7))if(date>=start)tickDates.add(date);}else for(const band of arrowMonthBands(start,end,pixelsPerDay))tickDates.add(band.start);
  const periods=projects.flatMap(project=>project.kind==='goal' && project.start && project.end ? [{task:{id:`${project.id}:goal`,title:project.title},project,start:project.start,end:project.end,top:y(project.start),height:y(project.end)-y(project.start)}] : (project.tasks || []).filter(task=>task.owned!==false && task.kind==='package' && task.window_start && task.window_end && Array.isArray(project.timeline_entries)).map(task=>({task,project,start:task.window_start,end:task.window_end,top:y(task.window_start),height:y(task.window_end)-y(task.window_start)})));
  return {start,end,days,pixelsPerDay,height:Math.max(ARROW_TOP_GUTTER+ARROW_BOTTOM_GUTTER,y(end)+ARROW_BOTTOM_GUTTER),endY:y(end),todayY:y(source.today),groups,undated,periods,months:arrowMonthBands(start,end,pixelsPerDay),ticks:[...tickDates].sort().map(date=>({date,y:y(date)}))};
}
export function axisDateLabels(ticks,todayY,{lineHeight=15.4,todayLineHeight=20,gap=9}={}) {
  const labels=[], minimumGap=lineHeight+gap, todayGap=(lineHeight+todayLineHeight)/2+gap;
  for (const tick of [...ticks].sort((a,b)=>a.y-b.y)) {
    if (Math.abs(tick.y-todayY)<todayGap) continue;
    if (labels.length && tick.y-labels.at(-1).y<minimumGap) continue;
    labels.push(tick);
  }
  return labels;
}
const shortDate = date => date ? new Intl.DateTimeFormat(localeTag(),{day:'numeric',month:'short',timeZone:'UTC'}).format(new Date(`${date}T12:00:00Z`)) : t('Без даты');
const fullDate = date => new Intl.DateTimeFormat(localeTag(),{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(`${date}T12:00:00Z`));
export const axisDayLabel = date => date ? date.slice(8,10) : '';
const compactDate = date => date ? `${date.slice(8,10)}.${date.slice(5,7)}` : '';
const shortDay = date => new Intl.DateTimeFormat(localeTag(),{weekday:'short',timeZone:'UTC'}).format(new Date(`${date}T12:00:00Z`)).toLocaleUpperCase(localeTag());
const icon = direction => `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="${direction === 'left' ? 'M8 2 3 6l5 4Z' : 'm4 2 5 4-5 4Z'}"/></svg>`;
const colorOf = project => ({teal:'#77ddd2',purple:'#c5b6ed'})[project?.color] || (/^#[\da-f]{3,8}$/i.test(project?.color || '') ? project.color : '#86a5d6');
const attr = escapeHtml;
const isDone = cube => ['done','completed','cancelled','canceled'].includes(cube.status) || Number(cube.progress_percent) >= 100;
const taskName = (model,id) => model.tasks.find(task => task.id === id)?.title || t('Задача');
const projectName = (model,id) => model.projects.find(project => project.id === id)?.title || t('Общее');
const projectColor = (model,id) => colorOf(model.projects.find(project => project.id === id));
export function inspectorBox(rect,viewportWidth,viewportHeight) {
  const margin=12,gap=12,maxWidth=Math.min(480,viewportWidth-24),right=viewportWidth-rect.right-gap-margin,left=rect.left-gap-margin;
  if(right>=Math.min(320,maxWidth))return {left:rect.right+gap,top:margin,width:Math.min(maxWidth,right),maxHeight:viewportHeight-24};
  if(left>=Math.min(320,maxWidth)){const width=Math.min(maxWidth,left);return {left:rect.left-gap-width,top:margin,width,maxHeight:viewportHeight-24};}
  const above=rect.top-gap-margin,below=viewportHeight-rect.bottom-gap-margin,showAbove=above>below,height=Math.max(60,Math.min(viewportHeight-24,showAbove ? above : below));
  return {left:margin,top:showAbove ? Math.max(margin,rect.top-gap-height) : rect.bottom+gap,width:maxWidth,maxHeight:height};
}
export function editableNameMarkup(kind,item,location,openAction) {
  const action=openAction===undefined ? kind==='cube' ? 'open-cube' : kind==='event' ? 'open-calendar-contents' : null : openAction;
  const identity=kind==='cube' ? `data-cube-id="${attr(item.id)}"` : kind==='event' ? `data-event-id="${attr(item.id)}"` : `data-task-id="${attr(item.id)}"`;
  const title=action ? ui`<button type="button" class="name-open-button cube-title" data-action="${action}" ${identity} data-inline-name="${kind}" data-name-id="${attr(item.id)}" data-name-location="${attr(location)}" title="${attr(item.title)}" aria-label="Открыть содержимое: ${attr(item.title)}"><strong>${escapeHtml(item.title)}</strong></button>` : `<span class="name-display"><strong>${escapeHtml(item.title)}</strong></span>`;
  const deletion=kind==='cube' ? cubeDeletePermission(item) : null;
  return ui`<span class="inline-name-row" data-title-kind="${kind}" data-name-id="${attr(item.id)}" data-name-location="${attr(location)}">${title}<button type="button" class="name-edit-button" data-action="rename-title" data-title-edit aria-label="Изменить название в этом плане: ${attr(item.title)}" title="Изменить название; также двойной щелчок или F2"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 2 4 4-8 8H2v-4Z M8 4l4 4"/></svg></button>${deletion ? `<button type="button" class="name-edit-button cube-delete-button" data-action="delete-cube" data-cube-delete data-cube-id="${attr(item.id)}" aria-label="${attr(deletion.allowed ? ui`Удалить кубик: ${item.title}` : deletion.reason)}" title="${attr(deletion.allowed ? t('Удалить кубик') : deletion.reason)}"${deletion.allowed ? '' : ' disabled'}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M6 4V2h4v2M4 4l1 10h6l1-10M7 7v4M9 7v4"/></svg></button>` : ''}</span>`;
}
let commandSettled=Promise.resolve();
let model, context, fingerprint = '', busy = false, contextPending = 0, contextSequence = 0, contextQueue = Promise.resolve(), lastContextConflictRevision = -1, fetchPending = false, refreshQueued=false, poll, lastTimelineFocus='';
let hidePast = true, mergeMode = false, mergeIds = new Set(), drafts = new Map(), timelineGroupDate = null, timelineEntryId=null;
let internalRender=false;
let inlineEdit=null,inspectorPlacement=null,inspectorReturnAnchor=null,inspectorProvenance=false,currentDragCubeId=null,rulerEditor=null;
const ownContextRevisions=new Map();
const formAutosave=createAutosaveQueue({save:saveEditorSnapshot,onError:error=>notify(error.message,true)});
let arrowPixelsPerDay=null,arrowViewportClientHeight=null;try{const stored=typeof window!=='undefined' ? Number(window.localStorage?.getItem('period-planner-arrow-scale')) : 0;if(stored>0 && stored<=48)arrowPixelsPerDay=stored;}catch{}
let arrowDisplayMode='split',arrowSyncingScroll=false;try{if(window.localStorage?.getItem('period-planner-arrow-layout')==='overlay')arrowDisplayMode='overlay';}catch{}
let resizeState = null, timelineGesture = null, nativeDragActive=false, gestureCommitPending=false, suppressTimelineClick=false,arrowPan=null;
const gestureActive=()=>Boolean(resizeState || timelineGesture || nativeDragActive || gestureCommitPending || arrowPan);
const hasDOM = typeof document !== 'undefined';

function contextFor(source) {
  const supplied = source.context || {};
  return {mode:'week',date:source.today,week_start:monday(source.today),window_start:monday(source.today),expanded_week:null,calendar_event_id:null,...supplied};
}
function namedObject(kind,id) {if(kind==='entry'){const project=model.projects.find(project=>(project.timeline_entries || []).some(entry=>entry.id===id)),entry=project?.timeline_entries.find(entry=>entry.id===id);return entry ? {...entry,project_id:project.id,title:entry.title || entry.label || ''} : null;}return kind==='cube' || kind==='merge' ? model.cubes.find(item=>item.id===id) : kind==='event' ? model.events.find(item=>item.id===id) : kind==='task' ? model.tasks.find(item=>item.id===id) : model.projects.flatMap(project=>project.tasks || []).find(item=>item.id===id);}
function inspectorStyle() {return inspectorPlacement ? `left:${inspectorPlacement.left}px;top:${inspectorPlacement.top}px;width:${inspectorPlacement.width}px;max-height:${inspectorPlacement.maxHeight}px;right:auto;bottom:auto;` : '';}
function positionInspector(anchor,kind,id) {
  if(!anchor?.getBoundingClientRect)return;
  const rect=anchor.getBoundingClientRect();inspectorPlacement=inspectorBox(rect,window.innerWidth || 1024,window.innerHeight || 768);inspectorReturnAnchor={kind,id,location:anchor.closest('[data-name-location]')?.dataset.nameLocation,scrollY:window.scrollY || 0};
}
function renderInspectorHosts(options={}) {
  const root=document.getElementById('planner');
  if(options.updateDay){const workspace=root.querySelectorAll('.day-workspace')[0];if(workspace)workspace.outerHTML=renderWorkspace();for(const day of root.querySelectorAll('.day-cell')){const selected=day.dataset.date===context.date;day.classList.toggle('is-selected',selected);day.querySelector('.day-select')?.setAttribute('aria-pressed',String(selected));}for(const week of root.querySelectorAll('.week-block'))week.classList.toggle('is-selected-week',week.dataset.weekStart===monday(context.date));if(!context.task_id)for(const editor of root.querySelectorAll('.task-editor,.axis-selection'))editor.remove();}
  const cubeHost=document.getElementById('cube-inspector-host'),eventHost=document.getElementById('event-inspector-host');
  if(!cubeHost || !eventHost){render();return;}
  cubeHost.innerHTML=renderCubeEditor();eventHost.innerHTML=renderCalendarEventEditor();
  for(const host of [cubeHost,eventHost])for(const input of host.querySelectorAll('[data-draft]'))if(drafts.has(input.dataset.draft)){if(input.type==='checkbox')input.checked=Boolean(drafts.get(input.dataset.draft));else input.value=drafts.get(input.dataset.draft);}
}
function renderInlineNameEditor() {
  if(!inlineEdit)return '';
  const edit=inlineEdit,key=`inline-name:${edit.kind}:${edit.id}`,position=edit.position || {left:12,top:100,width:300};
  return ui`<div class="inline-name-editor${edit.kind==='merge' ? ' is-merge' : ''}" role="group" aria-label="${edit.kind==='merge' ? t('Объединить кубики') : t('Название в этом плане')}" style="left:${position.left}px;top:${position.top}px;width:${position.width}px;">${edit.kind==='merge' ? ui`<span class="inline-merge-total">Объединить · ${minutesLabel(edit.minutes)}</span>` : ''}<input id="inline-name-input" data-inline-title="${edit.kind}" data-name-id="${attr(edit.id)}" data-draft="${attr(key)}" aria-label="Название в этом плане" value="${attr(edit.value)}"${edit.saving ? ' readonly' : ''}>${edit.kind==='merge' ? `<button type="button" data-action="save-inline-name" aria-label="${t('Объединить')}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3 3 7-7"/></svg></button>` : ''}<button type="button" data-action="cancel-inline-name" aria-label="Отменить изменение названия" title="Отменить · Escape"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></div>`;
}
function showInlineNameEditor(focus=false) {
  const before=internalRender;internalRender=true;try{
  const host=document.getElementById('inline-name-editor-host');if(host)host.innerHTML=renderInlineNameEditor();else render();
  if(focus){const input=document.getElementById('inline-name-input');input?.focus({preventScroll:true});input?.select();}
  }finally{internalRender=before;}
}
export function beginInlineRename(kind,id,location,rect={left:12,top:100,width:260,right:272,bottom:125}) {
  const object=namedObject(kind,id);if(!object)return null;
  if(inlineEdit?.saving){notify(t('Дождитесь сохранения названия.'),true);return null;}
  const width=Math.min(420,Math.max(240,rect.width || 260),(window.innerWidth || 1024)-24),position={left:Math.max(12,Math.min(rect.left,(window.innerWidth || 1024)-width-12)),top:Math.max(12,Math.min(rect.top-3,(window.innerHeight || 768)-54)),width};
  const edit={kind,id,location,original:object.title,value:object.title,position,baseRevision:model.revision,saving:false};inlineEdit=edit;
  edit.ready=contextQueue.then(()=>{if(kind!=='merge')edit.baseRevision=model.revision;if(namedObject(kind,id)?.title!==edit.original)edit.conflicted=true;});
  showInlineNameEditor(true);return edit;
}
export function cancelInlineRename() {if(!inlineEdit)return;drafts.delete(`inline-name:${inlineEdit.kind}:${inlineEdit.id}`);inlineEdit=null;showInlineNameEditor();}
export async function commitInlineRename(value) {
  const edit=inlineEdit;if(!edit || edit.saving)return null;
  edit.value=String(value ?? document.getElementById('inline-name-input')?.value ?? edit.value);drafts.set(`inline-name:${edit.kind}:${edit.id}`,edit.value);
  await edit.ready;
  if(edit.conflicted || namedObject(edit.kind,edit.id)?.title!==edit.original || (edit.kind==='merge' && edit.snapshots.some((snapshot,index)=>mergeSnapshot(namedObject('cube',edit.mergeIds[index]))!==snapshot))){edit.conflicted=true;notify(edit.kind==='merge' ? t('Кубики уже изменены. Название сохранено в поле; Escape отменит объединение.') : t('Название уже изменено в другом окне. Ваш текст сохранён в поле; Escape покажет актуальное название.'),true);return null;}
  const title=edit.value.trim();if(!title){notify(t('Название не может быть пустым.'),true);return null;}
  if(title===edit.original && edit.kind!=='merge'){cancelInlineRename();return model;}
  edit.saving=true;
  const actions={cube:['cube_rename','cube_id'],event:['calendar_event_rename','event_id'],assignment:['assignment_rename','task_id'],task:['task_rename','task_id'],entry:['ruler_entry_upsert','entry_id']};
  const result=edit.kind==='merge' ? await command('cube_merge',{cube_ids:edit.mergeIds,title},{expectedRevision:edit.baseRevision}) : await command(actions[edit.kind][0],{[actions[edit.kind][1]]:edit.id,title,...(edit.kind==='entry' ? {ruler_id:namedObject('entry',edit.id).project_id} : {})},{expectedRevision:edit.baseRevision});
  edit.saving=false;
  if(result){if(inlineEdit===edit){drafts.delete(`inline-name:${edit.kind}:${edit.id}`);inlineEdit=null;}render();}
  else{edit.conflicted=true;showInlineNameEditor(true);}
  return result;
}
function mergeSnapshot(cube){return cube ? JSON.stringify({id:cube.id,title:cube.title,minutes:cube.minutes,task_id:cube.task_id,source_task_allocations:cube.source_task_allocations,selected_date:cube.selected_date,start:cube.start,actual_minutes:cube.actual_minutes,progress_percent:cube.progress_percent,status:cube.status}) : 'missing';}
export function beginMergePrompt(targetId,draggedId,rect={left:12,top:150,width:240}) {
  const cubes=[targetId,draggedId].map(id=>namedObject('cube',id)),eligibility=cubes.every(Boolean) ? mergeEligibility(cubes) : {ok:false,reason:t('Кубик уже изменён.')};if(!eligibility.ok){notify(eligibility.reason,true);return null;}
  const edit=beginInlineRename('merge',targetId,'merge',rect);if(!edit)return null;edit.mergeIds=[targetId,draggedId];edit.snapshots=cubes.map(mergeSnapshot);edit.minutes=eligibility.minutes;edit.baseRevision=model.revision;edit.ready=contextQueue.then(()=>{});edit.position.top=Math.min(edit.position.top,(window.innerHeight || 768)-90);showInlineNameEditor(true);return edit;
}
export async function deleteCube(cubeId){await formAutosave.flush();const cube=namedObject('cube',cubeId);if(!cube)return null;const permission=cubeDeletePermission(cube);if(!permission.allowed){notify(permission.reason,true);return null;}if(inlineEdit?.id===cubeId)cancelInlineRename();const result=await command('cube_delete',{cube_id:cubeId});if(result && context.cube_id===cubeId)await updateContext({cube_id:null},{partialInspector:true});if(result)notify(t('Кубик удалён.'));return result;}
export async function returnCubeToPool(cubeId,pool){await formAutosave.flush();const cube=namedObject('cube',cubeId);if(!cube)return null;const expected=cubePool(cube,model,context.date);if(pool!==expected){notify(ui`Верните кубик в «${expected==='task' ? t('По задачам дня') : t('Другие')}».`,true);return null;}if(!cube.selected_date)return model;return command('cube_return',{cube_id:cubeId});}
function clearDragHints(){const root=document.getElementById('planner');for(const target of root.querySelectorAll('.is-merge-target,.is-return-target,.is-invalid-drop'))target.classList.remove('is-merge-target','is-return-target','is-invalid-drop');}
function dragHints(hit,cubeId){clearDragHints();const cube=namedObject('cube',cubeId);if(!cube || !hit)return;const pool=hit.closest('[data-drop-pool]');if(pool)pool.classList.add(pool.dataset.dropPool===cubePool(cube,model,context.date) ? 'is-return-target' : 'is-invalid-drop');const target=hit.closest('.cube,.timeline-block[data-timeline-kind="cube"]'),targetId=target?.dataset.cubeId || target?.dataset.timelineId;if(targetId && targetId!==cubeId && mergeEligibility([namedObject('cube',targetId),cube].filter(Boolean)).ok)target.classList.add('is-merge-target');}
function notify(message, error = false) {
  const node = document.getElementById('notice');
  node.hidden = false; node.className = `notice ${error ? 'is-error' : 'is-success'}`; node.textContent = message;
}
function setSync(message, error = false) { const node = document.getElementById('sync-status'); if(node){node.textContent = message; node.classList.toggle('is-error',error);} }
function normalizeModel(value) { return {...value,projects:value.projects || [],tasks:value.tasks || [],cubes:value.cubes || [],events:value.events || [],warnings:value.warnings || [],settings:value.settings || {}}; }
function acceptModel(value, force = false) {
  const next = normalizeModel(value);
  if(!force && model && next.revision<model.revision)return;
  const nextContext = contextFor(next);
  const nextFingerprint = `${next.revision}|${next.source_fingerprint||''}|${JSON.stringify(nextContext)}`;
  if (!force && nextFingerprint === fingerprint) { setSync(ui`Сохранено · версия ${next.revision}`); return; }
  model = next; context = nextContext; fingerprint = nextFingerprint;
  if(inlineEdit && !inlineEdit.saving && namedObject(inlineEdit.kind,inlineEdit.id)?.title!==inlineEdit.original){const latest=namedObject(inlineEdit.kind,inlineEdit.id);if(latest&&inlineEdit.kind!=='merge'&&inlineEdit.value===inlineEdit.original){inlineEdit.original=latest.title;inlineEdit.value=latest.title;inlineEdit.baseRevision=model.revision;inlineEdit.conflicted=false;drafts.delete(`inline-name:${inlineEdit.kind}:${inlineEdit.id}`);}else inlineEdit.conflicted=true;}
  mergeIds = new Set([...mergeIds].filter(id => model.cubes.some(cube => cube.id === id && cube.available !== false)));
  render(); setSync(ui`Сохранено · версия ${model.revision}`);
  if(!modelAnnounced){modelAnnounced=true;for(const callback of readyListeners)callback();}
}
export async function refresh(force=false) {
  if (fetchPending || busy || contextPending || (!force && gestureActive())) {if(fetchPending || gestureActive())refreshQueued=true;return;}
  fetchPending = true;
  try { const response = await periodRequest('/api/model',{cache:'no-store'}); if (!response.ok) throw new Error(t('Не удалось прочитать план. Повторим подключение.')); const value = await response.json(); if (!busy && !contextPending && (force || !gestureActive())) acceptModel(value); }
  catch (error) { setSync(error.message,true); if (!model) document.getElementById('planner').innerHTML = uiMarkup('<div class="loading-state">План пока недоступен. Подключение повторится автоматически.</div>'); }
  finally { fetchPending = false;if(refreshQueued && !busy && !contextPending && !gestureActive()){refreshQueued=false;void refresh();} }
}
export async function command(action, data, options={}) {
  const intentRevision = options.expectedRevision ?? model?.revision;
  if(!options.autosave)await formAutosave.flush();
  // Choosing a screen is persisted too. Its own revision must settle before a work edit.
  await contextQueue;
  if (intentRevision < lastContextConflictRevision) {notify(t('Пока выбирался экран, план изменился в другом окне или агентом. Мы перечитали его; проверьте данные и повторите действие.'),true);return null;}
  if (busy) { notify(t('Дождитесь сохранения текущего изменения.'),true); return null; }
  let settleCommand;commandSettled=new Promise(resolve=>{settleCommand=resolve;});
  busy = true; setSync(t('Сохраняем…'));
  document.getElementById('planner').setAttribute('aria-busy','true');
  try {
    let expectedRevision=options.expectedRevision ?? model.revision;
    if(options.expectedRevision!==undefined)while(ownContextRevisions.has(expectedRevision))expectedRevision=ownContextRevisions.get(expectedRevision);
    const response = await periodRequest('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:crypto.randomUUID(),expected_revision:expectedRevision,action,data})});
    const value = await response.json();
    if (!response.ok) {
      if (response.status === 409) { busy = false; await refresh(true); throw new Error(t('План уже изменился в другом окне или агентом. Мы перечитали его; проверьте данные и повторите действие.')); }
      throw new Error(value.error || value.message || t('Не удалось сохранить изменение.'));
    }
    if(Number.isInteger(value.revision) && value.revision===expectedRevision+1){ownContextRevisions.set(expectedRevision,value.revision);if(ownContextRevisions.size>200)ownContextRevisions.delete(ownContextRevisions.keys().next().value);}
    acceptModel(value,true);
    notify(({period_set:t('Период задачи сохранён. Объём и дедлайн не изменены.'),period_clear:t('Возвращён исходный период задачи. Её объём и дедлайн сохранены.'),cube_select:t('Кубик выбран на день.'),cube_return:t('Кубик возвращён на полку.'),cube_move:t('Кубик перенесён.'),cube_time:t('Время кубика сохранено.'),cube_merge:t('Кубики объединены. Общая длительность сохранена.'),cube_rename:t('Название кубика сохранено.'),task_rename:t('Название задачи сохранено.'),cube_cancel:t('Оставшийся объём кубика отменён.'),project_visibility:t('Видимость проекта сохранена.'),calendar_event_move:t('Блок перенесён в плане Work.'),calendar_offer_estimate:t('Длительность необязательного дела сохранена.')})[action] || t('Изменение сохранено.'));
    return value;
  } catch (error) { notify(error.message,true); setSync(t('Изменение не сохранено'),true); return null; }
  finally { busy = false;settleCommand(); document.getElementById('planner').setAttribute('aria-busy','false'); }
}
export async function updateContext(patch,viewOptions={}) {
  if (!model) return;
  const intentRevision=model.revision,requestId=crypto.randomUUID();
  const dateChanged=Boolean(patch.date && patch.date!==context.date);
  if (dateChanged) {
    patch={...patch};
    for (const field of ['cube_id','task_id','calendar_event_id']) if (!(field in patch)) patch[field]=null;
  }
  context = {...context,...patch}; model.context = {...context};if(viewOptions.partialInspector)renderInspectorHosts({updateDay:dateChanged});else render();
  const sequence = ++contextSequence; contextPending++;
  contextQueue = contextQueue.then(async () => {
    try {
      await commandSettled;
      if(intentRevision<lastContextConflictRevision)return;
      const beforeRevision = model.revision;
      const response = await periodRequest('/api/context',{method:'POST',body:JSON.stringify({request_id:requestId,expected_revision:beforeRevision,patch})});
      const saved=await response.json();
      if (response.status===409 || (response.ok && Number.isInteger(saved.revision) && saved.revision!==beforeRevision+1)) {
        const current=await periodRequest('/api/model');
        if(!current.ok)throw new Error(t('План изменился в другом окне. Не удалось перечитать его; дождитесь восстановления подключения.'));
        const latest=await current.json();
        lastContextConflictRevision=latest.revision;
        acceptModel(latest,true);
        notify(t('План изменился в другом окне или агентом. Обновлённые данные загружены; проверьте их перед изменением работы.'),true);
        return;
      }
      if(!response.ok)throw new Error(saved.error || t('Не удалось сохранить выбранный экран.'));
      if (Number.isInteger(saved.revision)) model.revision = saved.revision;
      if(Number.isInteger(saved.revision)){ownContextRevisions.set(beforeRevision,saved.revision);if(ownContextRevisions.size>200)ownContextRevisions.delete(ownContextRevisions.keys().next().value);}
      if (sequence === contextSequence) { context = {...context,...(saved.context || {})}; model.context = {...context}; fingerprint = `${model.revision}|${model.source_fingerprint||''}|${JSON.stringify(context)}`; }
    } catch (error) { notify(error.message,true); }
    finally { contextPending--; }
  });
  return contextQueue;
}
function dayEvents(date) { return model.events.filter(event => event.date === date && (!event.project_id || model.projects.find(project => project.id === event.project_id)?.visible !== false)); }
function renderEvent(event, compact = false, location='calendar') {
  const movable = canMoveCalendarEvent(event), role = event.calendar_role || 'fixed';
  return `<div class="calendar-event calendar-role-${role}${event.conflict ? ' is-conflict' : ''}${compact ? ' compact' : ''}" data-event-id="${attr(event.id)}" data-calendar-role="${role}" data-action="${movable ? 'open-calendar-event' : 'open-calendar-contents'}"${movable ? ' draggable="true"' : ''}><span class="event-time-line"><time>${escapeHtml(compactEventTimeLabel(event))}</time>${event.conflict ? ui`<button class="event-uncertainty" data-action="open-calendar-contents" data-event-id="${attr(event.id)}" aria-label="Уточнить время: ${attr(eventTimeDetails(event))}" title="${attr(eventTimeDetails(event))}"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M6 6a2 2 0 0 1 4 0c0 1-2 1-2 3M8 11v.1"/></svg></button>` : ''}</span>${editableNameMarkup('event',event,location)}</div>`;
}
function renderEventSummary(events, compact) {
  const {primary,count,additional,hasConflict} = eventSummary(events);
  if (!primary) return '';
  const narrowTitle = /вебинар|эфир/i.test(primary.title) ? t('Вебинар') : /урок|занят|мастер-класс/i.test(primary.title) ? t('Занятие') : /дедлайн|срок/i.test(primary.title) ? t('Срок') : t('Событие');
  return ui`<div class="day-event-summary calendar-role-${primary.calendar_role || 'fixed'}${compact ? ' for-month' : ' for-narrow'}${hasConflict ? ' has-conflict' : ''}" data-event-summary="${attr(primary.id)}"${compact ? ` data-event-id="${attr(primary.id)}"${canMoveCalendarEvent(primary) ? ' draggable="true"' : ''}` : ''} aria-label="${attr(ui`${count} событий; ${primary.title}${hasConflict ? t('; время не подтверждено') : ''}${additional ? ui`; ещё ${additional}` : ''}`)}"><span class="event-count"><span class="summary-wide">${count} ${count===1 ? t('событие') : count<5 ? t('события') : t('событий')}</span><span class="summary-narrow">${count} соб.</span></span><strong data-title-kind="event" data-name-id="${attr(primary.id)}" data-name-location="summary" data-action="open-calendar-contents" data-event-id="${attr(primary.id)}"><span class="summary-wide">${escapeHtml(primary.title)}</span><span class="summary-narrow">${narrowTitle}</span></strong><span class="summary-event-time">${escapeHtml(compactEventTimeLabel(primary))}</span>${hasConflict ? ui`<button class="event-uncertainty summary-conflict" data-action="open-calendar-contents" data-event-id="${attr(primary.id)}" aria-label="Уточнить время: ${attr(eventTimeDetails(primary))}" title="${attr(eventTimeDetails(primary))}"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M6 6a2 2 0 0 1 4 0c0 1-2 1-2 3M8 11v.1"/></svg></button>` : ''}</div>`;
}
function taskRibbons(weekStart, compact) {
  const ribbons = periodTasksForWeek(model,weekStart,hidePast);
  return ui`<div class="ribbon-area${compact ? ' is-compact' : ''}" aria-label="Периоды задач">${ribbons.length ? ribbons.map(({task,span}) => ui`<div class="ribbon-track"><div class="task-ribbon${context.task_id === task.id ? ' is-active' : ''}" data-testid="task-ribbon" data-task-id="${attr(task.id)}" style="grid-column:${span.start}/${span.end};--project:${projectColor(model,task.project_id)}"><button class="resize-handle start" data-resize="start" data-task-id="${attr(task.id)}" data-week-start="${weekStart}" aria-label="Изменить начало периода: ${attr(task.title)}" title="Потяните; стрелки клавиатуры — по одному дню"><span></span></button><button class="ribbon-title" data-action="open-task" data-task-id="${attr(task.id)}" title="${attr(task.title)}"><strong>${escapeHtml(task.title)}</strong><span>${compactDate(task.start)}–${compactDate(task.end)} · ${minutesLabel(task.effective_remaining_minutes ?? task.remaining_minutes ?? task.minutes)}${task.deadline ? ` · ${deadlineLabel(task.deadline_kind).toLowerCase()} ${compactDate(task.deadline)}` : ''}</span></button><button class="resize-handle end" data-resize="end" data-task-id="${attr(task.id)}" data-week-start="${weekStart}" aria-label="Изменить конец периода: ${attr(task.title)}" title="Потяните; стрелки клавиатуры — по одному дню"><span></span></button></div></div>`).join('') : uiMarkup('<p class="ribbon-empty">На этой неделе нет заданных периодов.</p>')}</div>`;
}
function monthPeriodSummary(weekStart) {
  const items = periodTasksForWeek(model,weekStart,hidePast), expanded = context.expanded_week === weekStart;
  return ui`<div class="month-period-summary" data-testid="month-period-summary" data-week-start="${weekStart}" data-period-count="${items.length}"><button data-action="show-periods" data-week-start="${weekStart}" aria-expanded="${expanded}" aria-controls="month-period-details"><strong>Периоды задач · ${items.length}</strong><span>${expanded ? t('Скрыть список') : t('Открыть список')}</span></button></div>`;
}
function renderMonthPeriodDetails() {
  if (context.mode !== 'month' || !context.expanded_week) return '';
  const week = context.expanded_week, items = periodTasksForWeek(model,week,hidePast);
  return ui`<section class="month-period-details inline-editor" id="month-period-details" data-testid="week-period-details" data-week-start="${week}" aria-label="Все периоды задач выбранной недели"><div class="editor-heading"><div><h3>Периоды · ${shortDate(week)} — ${shortDate(addDays(week,6))}</h3><p class="editor-note">Откройте задачу, чтобы изменить даты; в неделе доступны ручки растягивания.</p></div><button data-action="enter-week" data-week-start="${week}">Открыть неделю</button></div>${items.length ? `<ul>${items.map(({task})=>`<li><button data-action="open-task" data-task-id="${attr(task.id)}"><strong>${escapeHtml(task.title)}</strong><span>${compactDate(task.start)}–${compactDate(task.end)} · ${minutesLabel(task.effective_remaining_minutes ?? task.remaining_minutes ?? task.minutes)} · ${escapeHtml(projectName(model,task.project_id))}</span></button></li>`).join('')}</ul>` : uiMarkup('<p class="editor-note">В этой неделе нет видимых периодов задач.</p>')}</section>`;
}
function renderDay(date, compact = false) {
  const past = hidePast && date < model.today;
  const chosen = model.cubes.filter(cube => cube.selected_date === date && !isDone(cube) && model.projects.find(project => project.id === cube.project_id)?.visible !== false);
  const suggested = availableCubes(model,date).filter(cube => !cube.selected_date && cube.planned_date === date);
  const events = dayEvents(date);
  const headingTag=past ? 'div' : 'button';
  const heading=`<${headingTag} class="day-label${past ? '' : ' day-select'}"${past ? '' : ui` type="button" data-action="select-day" data-date="${date}" aria-pressed="${date === context.date}" aria-label="Выбрать ${attr(fullDate(date))}"`}><span>${shortDay(date)}</span><strong>${date.slice(8,10)}</strong>${date === model.today ? uiMarkup('<small>сегодня</small>') : ''}</${headingTag}>`;
  return `<div class="day-cell${date === context.date ? ' is-selected' : ''}${date === model.today ? ' is-today' : ''}${past ? ' is-past' : ''}" data-testid="day-cell" data-action="select-day" data-date="${date}" role="group" aria-label="${attr(fullDate(date))}${date === context.date ? t(', выбран') : ''}${events.length ? ui`, событий ${events.length}${events.some(event=>event.conflict) ? t(', конфликт времени') : ''}` : ''}">${heading}${past ? uiMarkup('<span class="past-label">Прошло</span>') : `${renderEventSummary(events,compact)}${compact ? '' : `<div class="day-events">${events.map(event => renderEvent(event,false,'week')).join('')}</div>`}<div class="day-counts">${chosen.length ? ui`<b><span class="summary-wide">${chosen.length} выбрано · ${minutesLabel(chosen.reduce((sum,cube) => sum + Number(cube.minutes || 0),0))}</span><span class="summary-narrow">${chosen.length} выбр.</span></b>` : uiMarkup('<span class="choose-day-label">Выбрать день</span>')}${suggested.length ? ui`<small><span class="summary-wide">${suggested.length} ${suggested.length === 1 ? t('предложен') : t('предложено')}</span><span class="summary-narrow">${suggested.length} предл.</span></small>` : ''}</div>`}</div>`;
}
export function calendarMarkup(source, ctx, mask = true) {
  const previousModel = model, previousContext = context, previousMask = hidePast;
  model = normalizeModel(source); context = ctx; hidePast = mask;
  try { return renderCalendar(); } finally { model = previousModel; context = previousContext; hidePast = previousMask; }
}
function renderCalendar() {
  const month = context.mode === 'month';
  const weeks = month ? windowWeeks(context.window_start || context.week_start) : [context.week_start];
  return ui`<section class="calendar ${month ? 'is-month' : 'is-week'}" data-testid="calendar" aria-label="Планирование периода"><div class="${month ? 'month-grid' : 'week-grid'}">${weeks.map(week => `<section class="week-block${monday(context.date) === week ? ' is-selected-week' : ''}${context.expanded_week === week ? ' is-expanded' : ''}" data-week-start="${week}">${month ? ui`<div class="week-edge"><button data-action="enter-week" data-week-start="${week}" aria-label="Открыть неделю ${attr(shortDate(week))} — ${attr(shortDate(addDays(week,6)))}"><span>${compactDate(week)}</span><span>${compactDate(addDays(week,6))}</span></button><button class="expand-week" data-action="expand-week" data-week-start="${week}" aria-label="${context.expanded_week === week ? t('Сжать') : t('Увеличить')} высоту недели и ${context.expanded_week === week ? t('скрыть') : t('показать')} список периодов ${compactDate(week)}" aria-pressed="${context.expanded_week === week}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4-3 4 3M4 10l4 3 4-3"/></svg></button></div>` : ''}<div class="week-content">${month ? monthPeriodSummary(week) : taskRibbons(week,false)}<div class="days-grid">${weekDates(week).map(date => renderDay(date,month)).join('')}</div></div></section>`).join('')}</div></section>${renderMonthPeriodDetails()}<div id="event-inspector-host">${renderCalendarEventEditor()}</div>`;
}
function renderTaskEditor() {
  const found = model.tasks.find(item => item.id === context.task_id);
  const task = found?.owned === false ? null : found;
  const historical = !task && (model.projects.flatMap(project => (project.tasks || []).map(item => ({...item,project_id:project.id}))).find(item => item.id === context.task_id) || found);
  const unscheduled = model.tasks.filter(item => !item.start && item.owned !== false && model.projects.find(project => project.id === item.project_id)?.visible !== false);
  if (historical) return ui`<section class="task-editor inline-editor" aria-label="Сведения о задаче"><div class="editor-heading"><div><span class="type-label">${historical.owned===false ? t('Справочная задача') : historical.read_only ? t('Задание курса') : t('Задача из истории')} · ${escapeHtml(projectName(model,historical.project_id))}</span><h3>${escapeHtml(historical.title)}</h3></div><button class="text-button" data-action="close-task">Закрыть</button></div><p class="editor-note">${escapeHtml(historical.read_only ? sourceStateLabel(historical) : historical.status_label || taskTimelineState(historical,model.today).label)}${historical.date ? ` · ${shortDate(historical.date)}` : ''}${historical.deadline ? ` · ${deadlineLabel(historical.deadline_kind).toLowerCase()} ${shortDate(historical.deadline)}` : ''}</p><p class="editor-note">${historical.owned===false ? t('Справочный объём не входит в вашу работу и полку кубиков.') : historical.read_only ? t('Даты и состав задания приведены по источнику.') : t('Историческая запись сохраняет сведения из источника. Планирование доступно для текущих задач.')}</p></section>`;
  if (!task) return unscheduled.length ? ui`<details class="without-period"><summary>Задачи без периода · ${unscheduled.length}</summary><div>${unscheduled.map(item => `<button data-action="open-task" data-task-id="${attr(item.id)}">${escapeHtml(item.title)}</button>`).join('')}</div></details>` : '';
  return ui`<section class="task-editor inline-editor" aria-label="Редактор периода задачи"><div class="editor-heading"><div><span class="type-label">Задача · ${escapeHtml(projectName(model,task.project_id))}</span><h3>${editableNameMarkup('task',task,'task-inspector',null)}</h3></div><button class="text-button" data-action="close-task">Закрыть</button></div><form data-form="period" data-task-id="${attr(task.id)}" class="period-form"><label>Начало периода<input type="date" name="start" value="${attr(task.start || context.date)}" data-draft="period-start-${attr(task.id)}" required></label><label>Конец периода<input type="date" name="end" value="${attr(task.end || context.date)}" data-draft="period-end-${attr(task.id)}" required></label>${task.period_explicit ? ui`<button type="button" data-action="clear-period" data-task-id="${attr(task.id)}">Вернуть исходный период</button>` : ''}</form><p class="editor-note">Период определяет дни для выбора кубиков. Объём ${minutesLabel(task.minutes)}${task.deadline ? ui` и ${deadlineLabel(task.deadline_kind).toLowerCase()} ${shortDate(task.deadline)}` : ''} сохраняются.${Number(task.cancelled_minutes) ? ui` Отдельно отменено ${minutesLabel(task.cancelled_minutes)}; осталось в работе ${minutesLabel(task.effective_remaining_minutes)}.` : ''}</p></section>`;
}
function mergeDropGlyph(cube){return ui`<button type="button" class="merge-drop-glyph" data-merge-target="${attr(cube.id)}" aria-label="Объединить с этим кубиком" title="Объединить"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M2 4h4l5 5h5M2 14h4l5-5M13 6l3 3-3 3"/></svg></button>`;}
function cubeMarkup(cube,state){
  const optional=cube.kind==='calendar_optional',parents=cubeParents(cube),count=new Set(parents.map(part=>part.task_id)).size,draggable=!Number(cube.progress_percent) && !Number(cube.actual_minutes),label=optional ? calendarOfferLabel(cube) : cube.kind==='brainalot'?'Brainalot':count>1 ? ui`${count} задачи` : projectName(model,cube.project_id);
  return `<article class="cube${optional ? ` calendar-role-${cube.calendar_role}` : ''}${context.cube_id===cube.id ? ' is-active' : ''}" data-cube-id="${attr(cube.id)}" data-cube-state="${state}"${optional ? ` data-calendar-role="${attr(cube.calendar_role)}"` : ''} draggable="${draggable}" style="--project:${projectColor(model,cube.project_id)}"><div class="cube-top"><span class="project-dot"></span><span>${escapeHtml(label)}</span></div>${editableNameMarkup('cube',cube,state==='selected' ? 'selected' : 'shelf')}<div class="cube-meta"><span>${cubeDurationLabel(cube)}</span>${state==='elsewhere' ? `<span>${compactDate(cube.selected_date)}${cube.start ? ` · ${cube.start}` : ''}</span>` : ''}</div>${['suggestion','elsewhere'].includes(state) ? `<div class="cube-actions"><button class="primary small" data-action="select-cube" data-cube-id="${attr(cube.id)}">${state==='elsewhere' ? t('Перенести') : t('Выбрать')}</button></div>` : ''}${mergeDropGlyph(cube)}</article>`;
}
function renderMerge() {
  const selected = model.cubes.filter(cube => mergeIds.has(cube.id));
  const eligibility = mergeEligibility(selected);
  return ui`<form data-form="merge" class="merge-form"><p>Выберите не начатые кубики одной задачи. Итоговый кубик сохранит их общий объём.</p><label>Название общего кубика<input name="title" data-draft="merge-title" placeholder="Например, создать презентацию" required></label><div class="merge-total">Выбрано ${selected.length} · ${minutesLabel(selected.reduce((sum,cube) => sum + Number(cube.minutes || 0),0))}</div><button class="primary" type="submit"${eligibility.ok ? '' : ' disabled'}>Объединить выбранные</button>${!eligibility.ok ? `<small>${escapeHtml(eligibility.reason)}</small>` : ''}</form>`;
}
function timeMinutes(time) { const [h,m] = (time || '00:00').split(':').map(Number); return h * 60 + m; }
function endTime(start, minutes) { const total = timeMinutes(start) + Number(minutes); return `${String(Math.floor(total / 60)).padStart(2,'0')}:${String(total % 60).padStart(2,'0')}`; }
function renderTimeline(timed, events) {
  const geometry=timelineGeometry(timed,events,model.settings),hours=Array.from({length:(geometry.endMinute-geometry.startMinute)/60+1},(_,index)=>geometry.startMinute+index*60);
  const blocks=geometry.blocks.map(block=>{
    const item=block.item,cube=block.kind==='cube',permission=cube ? cubeResizePermission(item) : {allowed:false,reason:t('Длительность события закреплена источником Calendar.')};
    const draggable=cube ? !Number(item.progress_percent) && !Number(item.actual_minutes) : canMoveCalendarEvent(item);
    const timeLabel=`${formatTimelineMinute(block.startMinute)}–${formatTimelineMinute(block.startMinute+block.minutes)} · ${minutesLabel(block.minutes)}`;
    const handles=cube && permission.allowed ? ['start','end'].map(edge=>ui`<button class="timeline-resize-handle is-${edge}" data-timeline-resize="${edge}" data-cube-id="${attr(item.id)}" aria-label="Изменить ${edge==='start' ? t('начало') : t('конец')}: ${attr(item.title)}; стрелки вверх и вниз5минут, Shift1минута" title="${edge==='start' ? t('Начало') : t('Конец')} блока; стрелки5мин, Shift1мин"><span></span></button>`).join('') : '';
    const content=cube ? `<div class="timed-cube${item.kind==='calendar_optional' ? ` calendar-role-${item.calendar_role}` : ''}" data-action="open-cube" data-cube-id="${attr(item.id)}"${item.kind==='calendar_optional' ? ` data-calendar-role="${attr(item.calendar_role)}"` : ''} draggable="true" aria-label="${attr(`${item.title}; ${timeLabel}${permission.allowed ? t('; длительность можно менять ручками') : `; ${permission.reason}`}`)}"><span data-block-time>${timeLabel}</span>${editableNameMarkup('cube',item,'timed')}</div>` : renderEvent(item,false,'timed');
    return `<div class="timeline-block${block.minutes<20 ? ' is-short' : ''}${cube && context.cube_id===item.id ? ' is-selected' : ''}" data-timeline-id="${attr(item.id)}" data-timeline-kind="${block.kind}" data-start-minute="${block.startMinute}" data-duration-minutes="${block.minutes}" data-editable-drag="${draggable}" style="top:${block.top}px;height:${block.height}px;left:${100*block.lane/block.laneCount}%;width:calc(${100/block.laneCount}% - 4px);">${content}${handles}${cube ? mergeDropGlyph(item) : ''}<span class="timeline-block-tooltip">${editableNameMarkup(block.kind==='cube' ? 'cube' : 'event',item,'timed-tooltip')}<br>${escapeHtml(timeLabel)}${!permission.allowed && cube ? `<br>${escapeHtml(permission.reason)}` : ''}</span></div>`;
  }).join('');
  return ui`<div class="day-timeline minute-timeline" data-testid="day-timeline" data-pixels-per-minute="${geometry.pixelsPerMinute}" aria-label="Таймлайн выбранного дня"><div class="timeline-intro"><span>Время</span><span>5мин · Shift1мин</span></div><output class="timeline-preview" id="timeline-preview" aria-live="polite"></output><div class="timeline-scroll"><div class="timeline-canvas" data-timeline-canvas data-date="${context.date}" data-start-minute="${geometry.startMinute}" data-end-minute="${geometry.endMinute}" data-pixels-per-minute="${geometry.pixelsPerMinute}" style="height:${geometry.height}px;">${hours.map(minute=>`<div class="minute-hour" data-axis-minute="${minute}" style="top:${(minute-geometry.startMinute)*geometry.pixelsPerMinute}px"><time>${formatTimelineMinute(minute)}</time><span></span></div>`).join('')}<div class="timeline-blocks">${blocks}</div></div></div></div>`;
}
function renderCubeEditor() {
  const cube = model.cubes.find(item => item.id === context.cube_id);
  if (!cube) return '';
  const optional = cube.kind === 'calendar_optional', sourceDay = cube.source_date || cube.planned_date;
  const durationEditable=cubeResizePermission(cube).allowed || optional && cube.source_duration_minutes==null;
  const estimate=durationEditable ? ui`<label>Длительность, мин<input type="number" name="minutes" min="1" max="1440" step="1" value="${attr(cube.minutes || 30)}" data-draft="cube-estimate-${attr(cube.id)}" required></label>` : '';
  return ui`<section class="cube-editor inline-editor${optional ? ` calendar-role-${cube.calendar_role}` : ''}" id="cube-editor" style="${inspectorStyle()}" aria-label="Настройки кубика"><div class="editor-heading"><div><span class="type-label">${optional ? escapeHtml(calendarOfferLabel(cube)) : cube.source_task_allocations?.length>1 ? ui`Кубик · ${new Set(cube.source_task_allocations.map(part=>part.task_id)).size} задачи` : ui`Кубик · ${escapeHtml(taskName(model,cube.task_id))}`}</span><h3 data-inspector-heading tabindex="-1">${editableNameMarkup('cube',cube,'inspector',null)}</h3></div><button class="text-button" data-action="close-cube">Закрыть</button></div><p class="editor-note">${cubeDurationLabel(cube)} · ${Number(cube.progress_percent) || 0}% готово${Number(cube.actual_minutes) ? ui` · факт ${minutesLabel(cube.actual_minutes)}` : ''}${optional && sourceDay ? ui` · предложено ${shortDate(sourceDay)}` : ''}</p><form data-form="cube-placement" data-cube-id="${attr(cube.id)}" class="period-form"><label>Выбранный день<input type="date" name="date" value="${attr(cube.selected_date || context.date)}"${optional && cube.calendar_role === 'optional_day' && sourceDay ? ` min="${sourceDay}" max="${sourceDay}"` : ''} data-draft="cube-date-${attr(cube.id)}" required></label><label>Начало<input type="time" name="start" value="${attr(cube.start || '')}" step="60" data-draft="cube-time-${attr(cube.id)}"></label>${estimate}${cube.selected_date ? ui`<button type="button" data-action="return-cube" data-cube-id="${attr(cube.id)}">Вернуть на полку</button>` : ''}</form>${cube.source_task_allocations?.length ? ui`<details class="inspector-provenance"><summary>Связанные задачи · ${new Set(cube.source_task_allocations.map(part=>part.task_id)).size}</summary><ul>${cube.source_task_allocations.map(part=>`<li>${escapeHtml(taskName(model,part.task_id))} · ${minutesLabel(part.minutes)}</li>`).join('')}</ul></details>` : ''}${cube.source_title ? ui`<details class="inspector-provenance"><summary>Источник</summary><p>${escapeHtml(cube.source_title)}</p></details>` : ''}${cube.output ? ui`<p class="editor-note">Результат: ${escapeHtml(cube.output)}</p>` : ''}${cube.steps?.length ? ui`<details class="cube-steps"><summary>Шаги работы</summary><ol>${cube.steps.map(step => `<li>${escapeHtml(typeof step === 'string' ? step : step.title || step.text || '')}</li>`).join('')}</ol></details>` : ''}</section>`;
}
function renderCalendarEventEditor() {
  const event = model.events.find(item=>item.id === context.calendar_event_id);
  if (!event) return '';
  const movable = canMoveCalendarEvent(event), duration = event.duration_minutes ?? Math.max(0,timeMinutes(event.end)-timeMinutes(event.start));
  return ui`<section class="calendar-event-editor inline-editor calendar-role-${event.calendar_role || 'fixed'}" id="calendar-event-editor" style="${inspectorStyle()}" data-testid="calendar-event-editor" aria-label="Содержимое события"><div class="editor-heading"><div><span class="type-label">Событие</span><h3 data-inspector-heading tabindex="-1">${editableNameMarkup('event',event,'inspector',null)}</h3></div><button class="text-button" data-action="close-calendar-event">Закрыть</button></div><p class="editor-note">${shortDate(event.date)} · ${escapeHtml(compactEventTimeLabel(event))}${event.conflict ? ' ?' : ''}${duration ? ` · ${minutesLabel(duration)}` : ''}</p>${movable ? ui`<form data-form="calendar-event-move" data-event-id="${attr(event.id)}" class="period-form"><label>День<input type="date" name="date" value="${event.date}" data-draft="event-date-${attr(event.id)}" required></label><label>Начало<input type="time" name="start" value="${attr(event.start)}" step="60" data-draft="event-start-${attr(event.id)}" required></label></form>` : ''}<details class="inspector-provenance"${inspectorProvenance ? ' open' : ''}><summary>Источник</summary><p>${escapeHtml(event.source_title || event.title)}</p><p>${escapeHtml(eventTimeDetails(event))}</p><p>${shortDate(event.source_date || event.date)} · ${escapeHtml(event.source_start || event.start || '')}</p>${event.move_block_reason ? `<p>${escapeHtml(event.move_block_reason)}</p>` : ''}</details></section>`;
}
function renderSelectedDayDetails(events) {
  const tasks = model.tasks.filter(task=>task.owned !== false && model.projects.find(project=>project.id === task.project_id)?.visible !== false && task.start && task.end && task.start <= context.date && task.end >= context.date);
  return `<div class="selected-day-details" data-testid="selected-day-details">${tasks.length ? ui`<section class="selected-day-periods" aria-label="Периоды задач выбранного дня"><h3>Периоды задач на день</h3><div>${tasks.map(task=>`<button data-action="open-task" data-task-id="${attr(task.id)}"><strong>${escapeHtml(task.title)}</strong><span>${compactDate(task.start)}–${compactDate(task.end)} · ${escapeHtml(projectName(model,task.project_id))}</span></button>`).join('')}</div></section>` : ''}${events.length ? ui`<section class="selected-day-events" aria-label="Все события выбранного дня"><h3>События дня · ${events.length}</h3><div>${[...events].sort((a,b)=>Number(Boolean(b.conflict))-Number(Boolean(a.conflict))).map(event=>renderEvent(event,false,'day-details')).join('')}</div></section>` : ''}</div>`;
}
function renderWorkspace(){
  const pools=dayShelfPools(model,context.date),chosen=dayChosenCubes(model,context.date),selected=chosen.filter(cube=>!cube.start),timed=chosen.filter(cube=>cube.start),minutes=chosen.reduce((sum,cube)=>sum+Number(cube.minutes || 0),0),events=dayEvents(context.date);
  const poolHtml=(kind,title)=>ui`<section class="source-pool" data-drop-pool="${kind}" aria-label="${title}; сюда можно вернуть кубик"><header><h3>${title}</h3><span>${pools[kind].length}</span></header><div class="cube-grid">${pools[kind].length ? pools[kind].map(cube=>cubeMarkup(cube,placementState(cube,context.date))).join('') : uiMarkup('<span class="pool-empty">Нет кубиков</span>')}</div></section>`;
  return ui`<section class="day-workspace" aria-label="Работа выбранного дня"><header class="workspace-heading"><div><h2>${shortDay(context.date)} · ${compactDate(context.date)} <span>План дня</span></h2><p>${escapeHtml(fullDate(context.date))}</p></div><div class="day-summary"><strong>${minutesLabel(minutes)}</strong><span>Лимит ${minutesLabel(dailyLimitMinutes(model,context.date))}${minutes>(dailyLimitMinutes(model,context.date)) ? t(' · превышен') : ''}</span></div></header>${renderSelectedDayDetails(events)}<div class="workspace-columns"><section class="shelf" data-testid="cube-shelf" aria-label="Доступные кубики"><div class="source-pools">${poolHtml('task',t('По задачам дня'))}${poolHtml('other',t('Другие'))}</div></section><section class="day-plan" aria-labelledby="day-plan-heading"><header class="section-heading"><div><h3 id="day-plan-heading">Выбрано</h3><span>${selected.length} без времени · ${timed.length} по времени</span></div></header><div class="selected-cubes" data-drop-selected data-date="${context.date}">${selected.map(cube=>cubeMarkup(cube,'selected')).join('') || uiMarkup('<span class="pool-empty">Пусто</span>')}</div>${events.filter(event=>event.all_day || !event.start).map(event=>renderEvent(event,false,'day-plan')).join('')}${renderTimeline(timed,events)}</section></div><div id="cube-inspector-host">${renderCubeEditor()}</div></section>`;
}
function arrowStatusIcon(state) {
  const drawing = state === 'done' ? '<path d="m3.5 8 3 3 6-6"/>' : state === 'overdue' ? '<path d="M8 2 14 13H2Z M8 6v3 M8 11v.1"/>' : state === 'current' ? '<circle cx="8" cy="8" r="5"/><path d="M8 5v3l2 1"/>' : `<circle cx="8" cy="8" r="4.5"${state === 'unknown' ? ' stroke-dasharray="2 2"' : ''}/>`;
  return `<svg viewBox="0 0 16 16" aria-hidden="true">${drawing}</svg>`;
}
function sourceStateLabel(task) {
  if(task.personal_goal)return task.full_completion===true ? t('Отмечено готовым') : t('Не отмечено готовым');
  if(task.read_only)return task.full_completion===true ? t('Задание полностью готово по источнику') : t('Полная готовность задания не подтверждена');
  return task.status_label || ({done:t('Готово по источнику'),active:t('Активна по источнику'),waiting:t('Ожидает по источнику')})[task.source_status] || task.source_status || t('Статус источника не задан');
}
function workStateLabel(task) { return task.work_progress_percent === null || task.work_progress_percent === undefined ? t('Нет отметки о прогрессе') : ui`${Number(task.work_progress_percent)}% готовности`; }
function axisTaskTooltip(row) {
  return ui`<strong>${escapeHtml(row.task.title)}</strong><span>${escapeHtml(t(row.label))} ${shortDate(row.date)}</span><span>Источник: ${escapeHtml(sourceStateLabel(row.task))}</span><span>План: ${escapeHtml(workStateLabel(row.task))}</span>`;
}
function renderAxisSelection(axis) {
  const rows = [...axis.groups.flatMap(group=>group.items),...axis.undated], selected = rows.find(row=>row.entryId===timelineEntryId && row.task.id===context.task_id) || rows.find(row=>row.task.id === context.task_id);
  if (!selected) return '';
  const candidates=timelineGroupDate && timelineGroupDate===selected.date ? rows.filter(row=>row.date===selected.date) : [selected],items=[...new Map(candidates.map(row=>[(row.project.kind==='goal' || row.eventKind==='milestone') ? row.entryId : row.task.id,row])).values()];
  return ui`<section class="axis-selection" data-testid="time-arrow-details" aria-label="Сведения о выбранных заданиях"><header><strong>${items.length > 1 ? ui`Задания · ${compactDate(selected.date)}` : t('Сведения о задании')}</strong><button class="text-button small" data-action="close-task">Закрыть</button></header>${items.map(row=>{const task=row.task,components=task.components || (task.component_ids || []).map(id=>(row.project.tasks || []).find(item=>item.id===id)).filter(Boolean),historical=task.historical_window;return ui`<article><h3>${task.full_completion===true ? `<span class="assignment-full-check">${arrowStatusIcon('done')}</span>` : ''}${row.entryId && (row.project.kind==='goal' || row.eventKind==='milestone') ? editableNameMarkup('entry',{id:row.entryId,title:task.title},'entry-detail',null) : editableNameMarkup('assignment',task,'assignment-detail',null)}</h3><dl><dt>Отметка</dt><dd>${escapeHtml(t(row.label))}${row.date ? ` · ${shortDate(row.date)}` : ''}</dd>${task.window_start && task.window_end ? ui`<dt>Окно</dt><dd>${shortDate(task.window_start)} — ${shortDate(task.window_end)}</dd>` : ''}<dt>Готовность</dt><dd>${escapeHtml(sourceStateLabel(task))}</dd><dt>План</dt><dd>${escapeHtml(workStateLabel(task))}</dd></dl>${historical ? ui`<p class="editor-note">Историческое окно ${compactDate(historical.start)}–${compactDate(historical.end)}. Текущий срок не подтверждён.</p>` : ''}${components.length ? `<ul class="axis-component-list">${components.map(component=>`<li>${component.full_completion===true ? `<span class="assignment-full-check">${arrowStatusIcon('done')}</span>` : '<span class="assignment-open-mark"></span>'}<div>${editableNameMarkup('assignment',component,'component-detail',null)}<span>${component.individual_due ? ui`Срок ${shortDate(component.individual_due)}` : t('Отдельный срок не указан; используется общее окно пакета.')}</span><span>${escapeHtml(sourceStateLabel(component))}</span></div></li>`).join('')}</ul>` : ''}${task.source_refs?.length ? ui`<details class="axis-source-refs"><summary>Источники · ${task.source_refs.length}</summary><ul>${task.source_refs.map(ref=>`<li>${escapeHtml(typeof ref==='string' ? ref : `${ref.source || ref.url || ''}${ref.locator ? ` · ${ref.locator}` : ''}`)}</li>`).join('')}</ul></details>` : ''}${row.state.key==='overdue' ? uiMarkup('<p class="axis-overdue-note">Срок из источника прошёл; завершение не отмечено.</p>') : ''}</article>`;}).join('')}</section>`;
}
function arrowWindowHeight() {return arrowViewportClientHeight || Math.max(360,Math.min((window.innerWidth || 1440)<=620 ? 620 : 680,(window.innerHeight || 900)-220));}
function effectiveArrowModel() {const base=projectArrowModel(model),bounds=arrowZoomBounds(base.days,arrowWindowHeight());return projectArrowModel(model,{pixelsPerDay:Math.max(bounds.min,Math.min(bounds.max,arrowPixelsPerDay ?? base.pixelsPerDay))});}
function renderProjectArrow(axis,options={}) {
  const dailyTicks = Array.from({length:axis.days+1},(_,index)=>24+index*axis.pixelsPerDay),bounds=arrowZoomBounds(axis.days,arrowWindowHeight());
  const markers=axis.groups.map((group,index)=>{const row=group.items[0],state=group.items.find(item=>item.state.key==='overdue')?.state || row.state,taskCount=new Set(group.items.map(item=>item.task.id)).size,kinds=[...new Set(group.items.map(item=>item.eventKind))],eventLabel=kinds.length>1 ? t('Начало / дедлайн') : kinds[0]==='start' ? t('Начало разработки') : kinds[0]==='deadline' ? t('Дедлайн') : kinds[0]==='milestone' ? t('Отметка') : t('Дата источника'),fullCheck=group.items.length===1 && row.eventKind!=='start' && row.task.full_completion===true;
    return `<div class="axis-node is-${state.key}${group.items.some(item=>item.task.id===context.task_id) ? ' is-selected' : ''}" style="top:${group.y}px;left:${group.nodeX}px;width:${group.markerDiameter}px;height:${group.markerDiameter}px;--project:${colorOf(row.project)}"><button style="width:${group.markerDiameter}px;height:${group.markerDiameter}px;padding:0;border-width:${Math.min(1,group.markerDiameter/4)}px;" data-action="open-time-group" data-axis-date-group="${group.date}" data-first-task-id="${attr(row.task.id)}" data-axis-entry-id="${attr(row.entryId)}" data-time-task-id="${attr(row.task.id)}" data-time-task-ids="${attr(group.items.map(item=>item.task.id).join(' '))}" data-time-entry-id="${attr(row.entryId)}" data-time-entry-ids="${attr(group.items.map(item=>item.entryId).join(' '))}" data-date="${group.date}" data-date-basis="${row.basis}" data-event-kind="${kinds.length===1 ? kinds[0] : 'group'}" data-group-count="${group.items.length}" data-state="${state.key}" aria-label="${attr(`${shortDate(group.date)}; ${eventLabel}; ${group.items.map(item=>item.task.title).join('; ')}`)}" aria-describedby="axis-tip-${index}">${group.markerDiameter<14 ? `<span class="axis-plain-dot" style="width:${Math.min(5,group.markerDiameter/2)}px;height:${Math.min(5,group.markerDiameter/2)}px;"></span>` : group.items.length>1 ? `<span class="axis-group-count">${group.items.length}</span>` : fullCheck ? arrowStatusIcon('done') : state.key==='overdue' && row.eventKind!=='start' ? arrowStatusIcon('overdue') : '<span class="axis-plain-dot"></span>'}</button><div class="axis-marker-tooltip" role="tooltip" id="axis-tip-${index}">${group.items.map(axisTaskTooltip).join('<hr>')}</div></div>${group.showLabel ? `<button class="axis-group-label is-${state.key}" data-label-index="${index}" data-label-source-y="${group.y}" data-label-node-x="${group.nodeX}" data-label-priority="${group.items.some(item=>item.task.id===context.task_id) ? 10000 : state.key==='overdue' ? 5000 : 2000-Math.abs(group.y-axis.todayY)}" data-action="open-time-group" data-axis-date-group="${group.date}" data-first-task-id="${attr(row.task.id)}" data-axis-entry-id="${attr(row.entryId)}" style="top:${group.labelY}px;visibility:hidden;"><strong class="axis-event-label">${axisDayLabel(group.date)} · ${options.compact && kinds[0]==='start' ? t('Начало') : eventLabel}</strong><span class="axis-task-short">${taskCount>1 ? ui`${taskCount} задания` : escapeHtml(options.compact ? shortTaskLabel(row.task.title || row.task.short_name).split(' ').slice(0,2).join(' ') : shortTaskLabel(row.task.title || row.task.short_name))}</span>${state.key==='overdue' && row.eventKind!=='start' ? uiMarkup('<span class="axis-state-label">Просрочено</span>') : ''}</button>` : ''}`;
  }).join('');
  return ui`<div class="time-arrow-range">${compactDate(axis.start)}.${axis.start.slice(0,4)} — ${compactDate(axis.end)}.${axis.end.slice(0,4)}</div><div class="time-arrow-frame" data-arrow-frame><div class="time-arrow-viewport" data-arrow-viewport tabindex="0" aria-label="Стрела проектов; колесо меняет масштаб, фон можно перетаскивать"><div class="project-time-arrow" data-testid="project-time-arrow" data-zoom-band="${axis.pixelsPerDay<=bounds.min*1.15 ? 'out' : 'normal'}" data-start="${axis.start}" data-end="${axis.end}" data-pixels-per-day="${axis.pixelsPerDay}" style="height:${axis.height}px">${axis.months.map((band,index)=>`<div class="axis-month-band${index%2 ? ' is-alternate' : ''}" data-month="${band.month.slice(0,7)}" style="top:${band.top}px;height:${band.height}px">${band.height>=24 ? `<span>${new Intl.DateTimeFormat(localeTag(),{month:band.height>=120 ? 'long' : 'short',...(band.height>=120 ? {year:'numeric'} : {}),timeZone:'UTC'}).format(new Date(`${band.month}T12:00:00Z`))}</span>` : ''}</div>`).join('')}${axis.periods.map(period=>`<span class="axis-project-period" data-period-task-id="${attr(period.task.id)}" data-window-start="${period.start}" data-window-end="${period.end}" title="${attr(ui`${period.task.title}; начало ${shortDate(period.start)}; дедлайн ${shortDate(period.end)}`)}" style="top:${period.top}px;height:${period.height}px;--project:${colorOf(period.project)}"></span>`).join('')}${dailyTicks.map(y=>`<span class="axis-day-tick" style="top:${y}px"></span>`).join('')}${axisDateLabels(axis.ticks,axis.todayY).map(tick=>`<time class="axis-date-tick" datetime="${tick.date}" data-axis-date="${tick.date}" style="top:${tick.y}px">${axisDayLabel(tick.date)}</time>`).join('')}<svg class="axis-leaders" width="144" height="${axis.height}" viewBox="0 0 144 ${axis.height}" aria-hidden="true">${axis.groups.map((group,index)=>`<path data-label-leader="${index}" d="M${group.nodeX} ${group.y}H142"/>`).join('')}</svg>${markers}<div class="time-arrow-today" data-testid="time-arrow-today" data-date="${model.today}" style="top:${axis.todayY}px"><time datetime="${model.today}" data-axis-date="${model.today}">${axisDayLabel(model.today)}</time><span>Сегодня</span></div></div></div><div class="arrow-fixed-spine" data-arrow-fixed-spine aria-hidden="true"></div><svg class="arrow-fixed-tip" data-arrow-fixed-tip viewBox="0 0 16 12" aria-hidden="true"><path d="m2 2 6 7 6-7"/></svg></div>`;
}
function renderArrowTracks(shared) {
  const projects=model.projects.filter(project=>project.visible!==false),split=arrowDisplayMode==='split' && projects.length>1;
  const tracks=split ? projects.map(project=>({id:project.id,title:project.title,axis:projectArrowModel({...model,projects:[project]},{pixelsPerDay:shared.pixelsPerDay,rangeStart:shared.start,rangeEnd:shared.end})})) : projects.length ? [{id:projects.length===1 ? projects[0].id : 'overlay',title:projects.length===1 ? projects[0].title : '',axis:shared}] : [];
  return `<div class="arrow-tracks${split ? ' is-split' : ' is-overlay'}" data-arrow-layout="${split ? 'split' : 'overlay'}" data-arrows-count="${tracks.length}">${tracks.map(track=>`<section class="arrow-track${split ? ' is-compact' : ''}" data-arrow-track="${attr(track.id)}"><header>${track.title ? `<button data-action="ruler-edit" data-ruler-id="${attr(track.id)}" title="${attr(track.title)}">${escapeHtml(track.title)}</button>` : ''}</header>${renderProjectArrow(track.axis,{compact:split})}</section>`).join('')}</div>`;
}
function renderProjects() {
  const axis=effectiveArrowModel(),references=model.projects.filter(project=>project.visible!==false).flatMap(project=>(project.tasks || []).filter(task=>task.owned===false).map(task=>({...task,project_id:project.id})));
  return ui`<aside class="projects-panel" data-testid="projects" aria-labelledby="projects-heading"><header><h2 id="projects-heading">Проекты</h2><div class="ruler-tools"><button data-action="ruler-create" aria-label="Создать цель или линейку" title="Создать цель"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10M8 3v10"/></svg></button><button data-action="ruler-edit" aria-label="Настроить линейку" title="Настроить линейку"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 2 4 4-8 8H2v-4Z M8 4l4 4"/></svg></button></div></header><div class="project-choices">${model.projects.map(project=>`<label><input type="checkbox" data-action="project-visibility" data-project-id="${attr(project.id)}"${project.visible===false ? '' : ' checked'}><span class="project-dot" style="--project:${colorOf(project)}"></span><strong>${escapeHtml(project.title)}</strong></label>`).join('')}</div>${model.projects.filter(project=>project.visible!==false).length>1 ? ui`<div class="arrow-layout-toggle"><button data-action="arrow-layout" data-layout="split" aria-pressed="${arrowDisplayMode==='split'}" title="Раздельные линейки">Раздельно</button><button data-action="arrow-layout" data-layout="overlay" aria-pressed="${arrowDisplayMode==='overlay'}" title="Общая линейка">Вместе</button></div>` : ''}${renderAxisSelection(axis)}<div data-arrow-panel>${renderArrowTracks(axis)}</div>${axis.undated.length ? ui`<details class="axis-outside"><summary>Без текущего срока · ${axis.undated.length}</summary><ul>${axis.undated.map(row=>`<li><button data-action="open-task" data-axis-task="true" data-task-id="${attr(row.task.id)}">${escapeHtml(row.task.title)}</button></li>`).join('')}</ul></details>` : ''}${references.length ? ui`<details class="axis-outside reference-tasks" data-testid="time-arrow-references"><summary>Справочно · ${references.length}</summary><ul>${references.map(task=>`<li><button data-action="open-task" data-task-id="${attr(task.id)}">${escapeHtml(task.title)}</button></li>`).join('')}</ul></details>` : ''}</aside>`;
}
export function openRulerEditor(rulerId,create=false,anchor){const project=model.projects.find(item=>item.id===rulerId) || model.projects.find(item=>item.visible!==false) || model.projects[0];const id=create ? `goal:${crypto.randomUUID()}` : project?.id;if(!id)return null;rulerEditor={id,create,baseRevision:model.revision,entryId:null,newEntry:false,box:anchor?.getBoundingClientRect ? inspectorBox(anchor.getBoundingClientRect(),window.innerWidth || 1024,window.innerHeight || 768) : null};render();return rulerEditor;}
export function closeRulerEditor(){if(rulerEditor)clearDraftPrefix(`ruler:${rulerEditor.id}:`);rulerEditor=null;render();}
function renderRulerEditor(){
  if(!rulerEditor)return '';const editor=rulerEditor,project=model.projects.find(item=>item.id===editor.id),custom=editor.create || project?.kind==='goal',entries=project?.timeline_entries || [],entry=entries.find(item=>item.id===editor.entryId),entryDraft=editor.newEntry ? {id:editor.entryId,title:'',date:null,event_kind:'milestone',full_completion:null,visible:true} : entry,key=`ruler:${editor.id}:`,box=editor.box,style=box ? `left:${box.left}px;top:${box.top}px;width:${box.width}px;max-height:${box.maxHeight}px;right:auto;bottom:auto;` : '';
  return ui`<section class="ruler-manager inline-editor" id="ruler-manager" style="${style}" aria-label="Редактор целей и линеек"><header class="editor-heading"><h3>${editor.create ? t('Новая цель') : t('Линейка')}</h3><button class="text-button" data-action="ruler-close">Закрыть</button></header>${!editor.create ? ui`<label class="manager-select-label">Проект<select data-action="ruler-select">${model.projects.map(item=>`<option value="${attr(item.id)}"${item.id===editor.id ? ' selected' : ''}>${escapeHtml(item.title)}</option>`).join('')}</select></label>` : ''}<form data-form="ruler-save" class="ruler-form"><label>Название<input name="title" value="${attr(project?.title || '')}" data-draft="${attr(key)}title" required></label>${custom ? ui`<div class="manager-dates"><label>Начало<input type="date" name="start" value="${attr(project?.start || '')}" data-draft="${attr(key)}start"></label><label>Срок<input type="date" name="end" value="${attr(project?.end || '')}" data-draft="${attr(key)}end"></label></div>` : ''}<details class="manager-description"><summary>Описание</summary><textarea name="description" rows="2" data-draft="${attr(key)}description">${escapeHtml(project?.description || '')}</textarea></details>${!editor.create ? ui`<label class="manager-visible"><input type="checkbox" name="visible"${project?.visible!==false ? ' checked' : ''} data-draft="${attr(key)}visible">Показывать</label>` : ''}${editor.create ? `<button type="submit" class="primary">${t('Создать')}</button>` : ''}</form>${!editor.create ? ui`<div class="ruler-entry-heading"><h3>Отметки</h3><button data-action="ruler-entry-new" aria-label="Добавить отметку"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10M8 3v10"/></svg></button></div><ul class="ruler-entry-list">${entries.map(item=>ui`<li${item.visible===false ? ' class="is-hidden"' : ''}><button data-action="ruler-entry-edit" data-entry-id="${attr(item.id)}"><time>${item.date ? compactDate(item.date) : '—'}</time><span>${escapeHtml(item.title || item.label || t('Отметка'))}</span></button><button class="manager-icon" data-action="ruler-entry-visible" data-entry-id="${attr(item.id)}" data-visible="${item.visible===false}" aria-label="${item.visible===false ? t('Показать') : t('Скрыть')} отметку" title="${item.visible===false ? t('Показать') : t('Скрыть')}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1 8s3-5 7-5 7 5 7 5-3 5-7 5-7-5-7-5Z"/><circle cx="8" cy="8" r="2"/></svg></button><button class="manager-icon" data-action="ruler-entry-delete" data-entry-id="${attr(item.id)}" aria-label="Удалить отметку" title="Удалить"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M6 4V2h4v2M4 4l1 10h6l1-10"/></svg></button></li>`).join('')}</ul>${entryDraft ? ui`<form data-form="ruler-entry-save" class="ruler-form ruler-entry-form" id="ruler-entry-form"><label>Название отметки<input name="title" value="${attr(entryDraft.title || entryDraft.label || '')}" data-draft="${attr(key)}entry:${attr(entryDraft.id)}:title" required></label><div class="manager-dates"><label>Дата<input type="date" name="date" value="${attr(entryDraft.date || '')}" data-draft="${attr(key)}entry:${attr(entryDraft.id)}:date"></label><label>Тип<select name="event_kind" data-draft="${attr(key)}entry:${attr(entryDraft.id)}:kind"><option value="milestone"${entryDraft.event_kind==='milestone' ? ' selected' : ''}>Отметка</option><option value="start"${entryDraft.event_kind==='start' ? ' selected' : ''}>Начало</option><option value="deadline"${entryDraft.event_kind==='deadline' ? ' selected' : ''}>Дедлайн</option></select></label><label>Готовность<select name="full_completion" data-draft="${attr(key)}entry:${attr(entryDraft.id)}:done"><option value="unknown"${entryDraft.full_completion==null ? ' selected' : ''}>Неизвестно</option><option value="false"${entryDraft.full_completion===false ? ' selected' : ''}>Не готово</option><option value="true"${entryDraft.full_completion===true ? ' selected' : ''}>Готово</option></select></label></div><label class="manager-visible"><input name="visible" type="checkbox"${entryDraft.visible!==false ? ' checked' : ''} data-draft="${attr(key)}entry:${attr(entryDraft.id)}:visible">Показывать</label>${editor.newEntry ? `<button type="submit" class="primary">${t('Создать отметку')}</button>` : ''}${entryDraft.source_date || entryDraft.source_title ? ui`<details class="inspector-provenance"><summary>Источник</summary><p>${escapeHtml(entryDraft.source_title || '')}</p><p>${shortDate(entryDraft.source_date)}</p></details>` : ''}</form>` : ''}<button class="text-button ruler-delete" data-action="ruler-delete">${custom ? t('Удалить цель') : t('Скрыть линейку')}</button>` : ''}</section>`;
}
export async function saveRuler(data){if(!rulerEditor)return null;const editor=rulerEditor,project=model.projects.find(item=>item.id===editor.id),custom=editor.create || project?.kind==='goal',payload={ruler_id:editor.id,title:String(data.title || '').trim(),description:String(data.description ?? project?.description ?? ''),...(custom ? {start:Object.hasOwn(data,'start') ? data.start || null : project?.start || null,end:Object.hasOwn(data,'end') ? data.end || null : project?.end || null} : {}),...(!editor.create ? {visible:data.visible===undefined ? project?.visible!==false : Boolean(data.visible)} : {})};const result=await command(editor.create ? 'ruler_create' : 'ruler_update',payload,{expectedRevision:editor.baseRevision});if(result){for(const field of ['title','description','start','end','visible'])drafts.delete(`ruler:${editor.id}:${field}`);editor.create=false;editor.baseRevision=model.revision;render();}return result;}
export async function saveRulerEntry(data){if(!rulerEditor?.entryId)return null;const editor=rulerEditor,payload={ruler_id:editor.id,entry_id:editor.entryId,title:String(data.title || '').trim(),date:data.date || null,event_kind:data.event_kind || 'milestone',full_completion:data.full_completion===true || data.full_completion==='true' ? true : data.full_completion===false || data.full_completion==='false' ? false : null,visible:Boolean(data.visible)};const result=await command('ruler_entry_upsert',payload,{expectedRevision:editor.baseRevision});if(result){clearDraftPrefix(`ruler:${editor.id}:entry:${editor.entryId}:`);editor.entryId=null;editor.newEntry=false;editor.baseRevision=model.revision;render();}return result;}
async function rulerAction(action,entryId,visible){if(!rulerEditor)return null;const editor=rulerEditor,result=await command(action,{ruler_id:editor.id,...(entryId ? {entry_id:entryId} : {}),...(visible!==undefined ? {visible} : {})},{expectedRevision:editor.baseRevision});if(result){if(action==='ruler_delete'){closeRulerEditor();if(context.project_id===editor.id)await updateContext({project_id:null,task_id:null});}else{if(editor.entryId===entryId){editor.entryId=null;editor.newEntry=false;}editor.baseRevision=model.revision;render();}}return result;}
function settleSingleArrowLabels(panel) {
  const arrow=panel.querySelector('[data-testid="project-time-arrow"]'),today=panel.querySelector('[data-testid="time-arrow-today"]');if(!arrow || !today)return;
  const viewport=panel.querySelector('[data-arrow-viewport]'),frame=panel.querySelector('[data-arrow-frame]');
  if(viewport){arrowViewportClientHeight=viewport.clientHeight;if(frame){frame.style.setProperty('--arrow-axis-left',`${arrow.getBoundingClientRect().left+viewport.scrollLeft-frame.getBoundingClientRect().left+78}px`);frame.style.setProperty('--arrow-bottom-gutter',`${viewport.offsetHeight-viewport.clientHeight}px`);}const minimum=arrowZoomBounds((Date.parse(arrow.dataset.end)-Date.parse(arrow.dataset.start))/DAY_MS,viewport.clientHeight).min,scale=Number(arrow.dataset.pixelsPerDay);const out=panel.querySelector('[data-zoom="out"]'),inside=panel.querySelector('[data-zoom="in"]');if(out)out.disabled=scale<=minimum+1e-7;if(inside)inside.disabled=scale>=48-1e-7;if(arrowPixelsPerDay!==null && arrowPixelsPerDay<minimum && Math.abs(scale-minimum)>1e-7){zoomArrowPanel(viewport,minimum,0);return;}}
  const labels=[...arrow.querySelectorAll('.axis-group-label')],items=labels.map((label,index)=>({id:index,y:Number(label.dataset.labelSourceY),height:label.getBoundingClientRect().height || 40,anchorOffset:3+(label.querySelector('.axis-event-label')?.getBoundingClientRect().height || 15.4)/2,priority:Number(label.dataset.labelPriority)})),height=parseFloat(arrow.style.height),todayY=parseFloat(today.style.top),todayHeight=today.querySelector('span')?.getBoundingClientRect().height || 20;
  const placed=new Map(placeArrowLabelBoxes(items,height,todayY,{gap:9,todayHeight}).map(item=>[item.id,item]));
  labels.forEach((label,index)=>{const position=placed.get(index),leader=arrow.querySelector(`[data-label-leader="${index}"]`);label.dataset.labelVisible=String(Boolean(position));label.style.display=position ? 'grid' : 'none';label.style.visibility='visible';if(position){label.style.top=`${position.top}px`;label.style.transform='none';if(leader){leader.style.display='';leader.setAttribute('d',`M${label.dataset.labelNodeX} ${label.dataset.labelSourceY}H142`);}}else if(leader)leader.style.display='none';});
}
function settleArrowLabels(panel) {const tracks=[...panel.querySelectorAll('[data-arrow-track]')];if(tracks.length)for(const track of tracks){if(track.isConnected!==false)settleSingleArrowLabels(track);}else settleSingleArrowLabels(panel);}
export function arrowLayoutMetrics() {
  const root=document.getElementById('planner'),viewport=root.querySelectorAll('[data-arrow-viewport]')[0];if(!viewport)return null;
  const rectValue=rect=>({left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height});
  return {viewport:rectValue(viewport.getBoundingClientRect()),tracks:[...root.querySelectorAll('[data-arrow-viewport]')].map(item=>rectValue(item.getBoundingClientRect())),labels:[...root.querySelectorAll('.axis-group-label[data-label-visible="true"],.axis-date-tick,.axis-month-band>span,.time-arrow-today>span,.time-arrow-today>time')].map(element=>({kind:element.className || element.parentElement.className,text:element.textContent,rect:rectValue(element.getBoundingClientRect())}))};
}
function zoomArrowPanel(viewport,newScale,cursorY) {
  const panel=viewport.closest('[data-arrow-panel]'),arrow=viewport.querySelector('[data-testid="project-time-arrow"]');if(!panel || !arrow)return;
  const oldScale=Number(arrow.dataset.pixelsPerDay),oldScroll=viewport.scrollTop,oldLeft=viewport.scrollLeft,bounds=arrowZoomBounds((Date.parse(arrow.dataset.end)-Date.parse(arrow.dataset.start))/DAY_MS,viewport.clientHeight),scale=Math.max(bounds.min,Math.min(bounds.max,newScale));
  if(Math.abs(scale-oldScale)<1e-7)return;
  arrowPixelsPerDay=scale;try{window.localStorage?.setItem('period-planner-arrow-scale',String(scale));}catch{}
  const axis=projectArrowModel(model,{pixelsPerDay:scale});panel.innerHTML=renderArrowTracks(axis);
  settleArrowLabels(panel);for(const next of panel.querySelectorAll('[data-arrow-viewport]')){next.scrollTop=arrowZoomAnchor(oldScroll,cursorY,oldScale,scale);next.scrollLeft=oldLeft;}
}
export function onArrowWheel(event) {
  const viewport=event.target.closest('[data-arrow-viewport]');if(!viewport || !model)return;
  event.preventDefault();const arrow=viewport.querySelector('[data-testid="project-time-arrow"]');if(!arrow)return;
  const delta=event.deltaY*(event.deltaMode===1 ? 16 : event.deltaMode===2 ? viewport.clientHeight : 1),scale=Number(arrow.dataset.pixelsPerDay)*Math.exp(-delta*.0015);
  zoomArrowPanel(viewport,scale,event.clientY-viewport.getBoundingClientRect().top);
}
function beginArrowPan(event) {
  if(event.button!==0 || event.target.closest('button,a,summary'))return;
  const viewport=event.target.closest('[data-arrow-viewport]');if(!viewport)return;
  arrowPan={viewport,id:event.pointerId,x:event.clientX,y:event.clientY,top:viewport.scrollTop,left:viewport.scrollLeft};event.preventDefault();try{viewport.setPointerCapture(event.pointerId);}catch{}
}
function moveArrowPan(event) {if(!arrowPan || event.pointerId!==arrowPan.id)return;event.preventDefault();const top=arrowPan.top-(event.clientY-arrowPan.y),left=arrowPan.left-(event.clientX-arrowPan.x);arrowPan.viewport.scrollTop=top;arrowPan.viewport.scrollLeft=left;if(Math.abs(arrowPan.viewport.scrollTop-top)>.1){arrowPan.top=arrowPan.viewport.scrollTop;arrowPan.y=event.clientY;}if(Math.abs(arrowPan.viewport.scrollLeft-left)>.1){arrowPan.left=arrowPan.viewport.scrollLeft;arrowPan.x=event.clientX;}}
function finishArrowPan(event) {if(!arrowPan || event.pointerId!==arrowPan.id)return;arrowPan=null;void refresh();}
function syncArrowScroll(event) {const source=event.target;if(!source.matches?.('[data-arrow-viewport]') || arrowSyncingScroll)return;arrowSyncingScroll=true;const panel=source.closest('[data-arrow-panel]');for(const other of panel?.querySelectorAll('[data-arrow-viewport]') || [])if(other!==source){other.scrollTop=source.scrollTop;other.scrollLeft=source.scrollLeft;}arrowSyncingScroll=false;}
function showArrowTooltip(event) {const marker=event.target.closest('[data-time-entry-id]'),host=document.getElementById('arrow-tooltip-host');if(!marker || !host)return;const ids=new Set((marker.dataset.timeEntryIds || marker.dataset.timeEntryId).split(' ')),rows=projectArrowModel(model).groups.flatMap(group=>group.items).filter(row=>ids.has(row.entryId)),rect=marker.getBoundingClientRect(),width=Math.min(260,(window.innerWidth || 1024)-24),left=rect.right+width+22<(window.innerWidth || 1024) ? rect.right+10 : Math.max(12,rect.left-width-10),top=Math.max(12,Math.min(rect.top,(window.innerHeight || 768)-330));host.innerHTML=`<div class="arrow-tooltip-portal" role="tooltip" style="left:${left}px;top:${top}px;width:${width}px;">${rows.map(axisTaskTooltip).join('<hr>')}</div>`;}
function hideArrowTooltip() {const host=document.getElementById('arrow-tooltip-host');if(host)host.innerHTML='';}
function render() {
  const node = document.getElementById('planner');const before=internalRender;internalRender=true;try{
  const oldArrowViewport=node.querySelectorAll('[data-arrow-viewport]')[0],arrowScrollMemory=oldArrowViewport ? {top:oldArrowViewport.scrollTop,left:oldArrowViewport.scrollLeft} : null;
  const focused = document.activeElement;
  const focusDraft = focused?.dataset.draft;
  const focusTimeline=focused?.dataset.timelineResize ? {edge:focused.dataset.timelineResize,id:focused.dataset.cubeId} : null;
  const selection = focused && 'selectionStart' in focused ? [focused.selectionStart,focused.selectionEnd] : null;
  const scrollPositions = [...node.querySelectorAll('.cube-grid,.project-task-list,.ribbon-area,.timeline-scroll,[data-arrow-viewport]')].map(item => item.scrollTop);
  const start = context.mode === 'month' ? context.window_start || context.week_start : context.week_start;
  const end = addDays(start,context.mode === 'month' ? 27 : 6);
  node.innerHTML = ui`<div class="planner-layout"><div class="planning-main"><header class="planner-toolbar"><div><h1>План работы</h1></div><div class="view-switch" aria-label="Масштаб плана"><button data-action="set-mode" data-mode="${context.mode==='month'?'week':'month'}" aria-pressed="${context.mode==='month'}">${t('4 недели')}</button></div></header><div class="calendar-nav"><div class="range-navigation"><button class="triangle-button" data-action="navigate" data-direction="-1" aria-label="${context.mode==='month' ? t('Предыдущие четыре недели') : t('Предыдущая неделя')}">${icon('left')}</button><span class="range-label">${shortDate(start)} — ${shortDate(end)}</span><button class="triangle-button" data-action="navigate" data-direction="1" aria-label="${context.mode==='month' ? t('Следующие четыре недели') : t('Следующая неделя')}">${icon('right')}</button></div><label class="past-toggle"><input type="checkbox" data-action="show-past"${hidePast ? '' : ' checked'}>Прошедшие даты</label></div>${model.warnings.length ? ui`<details class="warnings"><summary>Уточнения к плану · ${model.warnings.length}</summary><ul>${model.warnings.map(warning => `<li>${escapeHtml(typeof warning === 'string' ? warning : warning.message || warning.title || JSON.stringify(warning))}</li>`).join('')}</ul></details>` : ''}${renderCalendar()}${renderTaskEditor()}${renderWorkspace()}</div>${renderProjects()}</div><div id="inline-name-editor-host">${renderInlineNameEditor()}</div><div id="arrow-tooltip-host"></div><div id="ruler-manager-host">${renderRulerEditor()}</div>`;
  [...node.querySelectorAll('[data-draft]')].forEach(input => {if (drafts.has(input.dataset.draft)) {if(input.type==='checkbox')input.checked=Boolean(drafts.get(input.dataset.draft));else input.value = drafts.get(input.dataset.draft);}});
  if (focusDraft) {const restore = [...node.querySelectorAll('[data-draft]')].find(input => input.dataset.draft === focusDraft); if (restore) {restore.focus({preventScroll:true}); try {if (selection?.[0] !== null) restore.setSelectionRange(...selection);} catch { /* Date and time inputs do not have a text selection. */ }}}
  else if(focusTimeline){const restore=[...node.querySelectorAll('[data-timeline-resize]')].find(item=>item.dataset.timelineResize===focusTimeline.edge && item.dataset.cubeId===focusTimeline.id);restore?.focus({preventScroll:true});}
  [...node.querySelectorAll('.cube-grid,.project-task-list,.ribbon-area,.timeline-scroll,[data-arrow-viewport]')].forEach((item,i) => {item.scrollTop = scrollPositions[i] || 0;});
  if(arrowScrollMemory)for(const viewport of node.querySelectorAll('[data-arrow-viewport]')){viewport.scrollTop=arrowScrollMemory.top;viewport.scrollLeft=arrowScrollMemory.left;}
  const focusKey=`${context.date}|${context.cube_id || ''}`,timelineScroll=node.querySelectorAll('.timeline-scroll')[0];
  if(focusKey!==lastTimelineFocus && timelineScroll){const block=[...node.querySelectorAll('[data-timeline-id]')].find(item=>item.dataset.timelineId===context.cube_id);if(block)timelineScroll.scrollTop=Math.max(0,block.offsetTop-timelineScroll.clientHeight/3);lastTimelineFocus=focusKey;}
  [...node.querySelectorAll('[data-arrow-panel]')].forEach(settleArrowLabels);
  node.setAttribute('aria-busy',String(busy));
  }finally{internalRender=before;}
}
function clearDraftPrefix(prefix) { for (const key of drafts.keys()) if (key.startsWith(prefix)) drafts.delete(key); }
async function onClick(event) {
  if(event.target.closest('[data-inline-title]'))return;
  if(suppressTimelineClick && event.target.closest('.timeline-block')){suppressTimelineClick=false;event.preventDefault();return;}
  const target = event.target.closest('[data-action]');
  if (!target || target.matches('input')) return;
  const action = target.dataset.action;
  if(action==='ruler-create'){openRulerEditor(null,true,target);return;}
  if(action==='ruler-edit'){openRulerEditor(target.dataset.rulerId,false,target);return;}
  if(action==='ruler-close'){if(!rulerEditor?.create&&!rulerEditor?.newEntry)await formAutosave.flush();closeRulerEditor();return;}
  if(action==='ruler-delete'){await rulerAction('ruler_delete');return;}
  if(action==='ruler-entry-new' || action==='ruler-entry-edit'){if(!rulerEditor)return;rulerEditor.newEntry=action==='ruler-entry-new';rulerEditor.entryId=rulerEditor.newEntry ? `${rulerEditor.id}:entry:${crypto.randomUUID()}` : target.dataset.entryId;render();document.getElementById('ruler-entry-form')?.scrollIntoView?.({block:'nearest'});return;}
  if(action==='ruler-entry-delete'){await rulerAction('ruler_entry_delete',target.dataset.entryId);return;}
  if(action==='ruler-entry-visible'){await rulerAction('ruler_entry_upsert',target.dataset.entryId,target.dataset.visible==='true');return;}
  if(action==='delete-cube'){await deleteCube(target.dataset.cubeId);return;}
  if(action==='arrow-layout'){arrowDisplayMode=target.dataset.layout;try{window.localStorage?.setItem('period-planner-arrow-layout',arrowDisplayMode);}catch{}render();return;}
  if(action==='rename-title'){const host=target.closest('[data-title-kind]');if(host)beginInlineRename(host.dataset.titleKind,host.dataset.nameId,host.dataset.nameLocation,host.getBoundingClientRect());return;}
  if(action==='save-inline-name'){await commitInlineRename();return;}
  if(action==='cancel-inline-name'){cancelInlineRename();return;}
  if(action==='arrow-zoom'){const panel=target.closest('[data-arrow-panel]'),viewport=panel?.querySelector('[data-arrow-viewport]'),arrow=viewport?.querySelector('[data-testid="project-time-arrow"]');if(viewport && arrow){const current=Number(arrow.dataset.pixelsPerDay),next=target.dataset.zoom==='reset' ? projectArrowModel(model).pixelsPerDay : current*(target.dataset.zoom==='in' ? 1.35 : 1/1.35);zoomArrowPanel(viewport,next,viewport.clientHeight/2);panel.querySelector(`[data-zoom="${target.dataset.zoom}"]`)?.focus({preventScroll:true});}return;}
  if (action === 'select-day') {if (hidePast && target.dataset.date < model.today) return; await updateContext({date:target.dataset.date,week_start:monday(target.dataset.date),cube_id:null});}
  if (action === 'set-mode') await updateContext({mode:target.dataset.mode,window_start:context.window_start || context.week_start});
  if (action === 'navigate') {const next = addDays(context.mode === 'month' ? context.window_start : context.week_start,Number(target.dataset.direction) * (context.mode === 'month' ? 28 : 7)); await updateContext(context.mode === 'month' ? {window_start:next,week_start:next,date:next,expanded_week:null} : {week_start:next,date:next});}
  if (action === 'enter-week') await updateContext({mode:'week',week_start:target.dataset.weekStart,date:monday(context.date) === target.dataset.weekStart ? context.date : target.dataset.weekStart});
  if (action === 'expand-week') await updateContext({expanded_week:context.expanded_week === target.dataset.weekStart ? null : target.dataset.weekStart});
  if (action === 'show-periods') {const expanded = context.expanded_week === target.dataset.weekStart ? null : target.dataset.weekStart; await updateContext({expanded_week:expanded}); if (expanded) document.getElementById('month-period-details')?.scrollIntoView?.({block:'nearest',behavior:'auto'});}
  if (action === 'open-task') {if (target.dataset.axisTask){timelineGroupDate=null;timelineEntryId=null;} await updateContext({task_id:target.dataset.taskId});}
  if (action === 'open-time-group') {timelineGroupDate=target.dataset.axisDateGroup;timelineEntryId=target.dataset.axisEntryId;await updateContext({task_id:target.dataset.firstTaskId});}
  if (action === 'close-task') {await formAutosave.flush();await updateContext({task_id:null});}
  if(event.detail>1 && target.closest('[data-title-kind]'))return;
  if (action === 'open-cube') {if(context.cube_id!==target.dataset.cubeId || !target.closest('#cube-editor'))await openCubeContents(target.dataset.cubeId,target.closest('[data-title-kind]') || target);}
  if (action === 'close-cube') {await formAutosave.flush();await updateContext({cube_id:null},{partialInspector:true});restoreInspectorAnchor();}
  if (action === 'clear-period') {clearDraftPrefix('period-'); await command('period_clear',{task_id:target.dataset.taskId});}
  if (action === 'select-cube') {const cube = model.cubes.find(item => item.id === target.dataset.cubeId);await command('cube_drop',{cube_id:cube.id,date:context.date,start:cube.selected_date ? cube.start || null : null});}
  if (action === 'open-calendar-event' || action==='open-calendar-contents') await openCalendarContents(target.dataset.eventId,target.closest('[data-title-kind]') || target,{sourceDetails:target.classList.contains('event-uncertainty')});
  if (action === 'close-calendar-event') {await formAutosave.flush();await updateContext({calendar_event_id:null},{partialInspector:true});restoreInspectorAnchor();}
  if (action === 'return-cube') {await formAutosave.flush();clearDraftPrefix(`cube-date-${target.dataset.cubeId}`); clearDraftPrefix(`cube-time-${target.dataset.cubeId}`); await command('cube_return',{cube_id:target.dataset.cubeId});}
  if (action === 'toggle-merge') {mergeMode = !mergeMode; if (!mergeMode) mergeIds.clear(); render();}
}
async function onChange(event) {
  const target = event.target;
  const form=target.closest?.('form[data-form]');if(form){if(target.dataset.draft)drafts.set(target.dataset.draft,target.type==='checkbox'?Boolean(target.checked):target.value);if(scheduleEditorAutosave(form,{immediate:true}))return;}
  if(target.dataset.action==='ruler-select'){await formAutosave.flush();openRulerEditor(target.value);return;}
  if (target.dataset.action === 'show-past') {hidePast = !target.checked; render();}
  if (target.dataset.action === 'project-visibility') await command('project_visibility',{project_id:target.dataset.projectId,visible:target.checked});
  if (target.dataset.action === 'merge-pick') {if (target.checked) mergeIds.add(target.dataset.cubeId); else mergeIds.delete(target.dataset.cubeId); render();}
}
const ordinaryForms=new Set(['period','task-rename','cube-rename','cube-placement','calendar-event-move','ruler-save','ruler-entry-save']);
function editorSnapshot(form){
  const type=form.dataset.form,editor=type.startsWith('ruler-') && rulerEditor ? {id:rulerEditor.id,entryId:rulerEditor.entryId,create:rulerEditor.create,newEntry:rulerEditor.newEntry} : null;
  if(!ordinaryForms.has(type) || type==='ruler-save' && editor?.create || type==='ruler-entry-save' && editor?.newEntry)return null;
  const controls=Array.from(form.elements||[]).filter(control=>control.name && !control.disabled && !['submit','button','reset'].includes(control.type)),data={};
  for(const control of controls){if(control.type==='radio'&&!control.checked)continue;data[control.name]=control.type==='checkbox'?Boolean(control.checked):control.value;}
  const draftValues=controls.filter(control=>control.dataset?.draft).map(control=>[control.dataset.draft,control.type==='checkbox'?Boolean(control.checked):control.value]);
  const changedFields=controls.filter(control=>control.dataset?.draft && drafts.has(control.dataset.draft)).map(control=>control.name);
  const ids={...form.dataset},identity=ids.cubeId||ids.taskId||ids.eventId||(type==='ruler-entry-save'?editor?.entryId:editor?.id)||'';
  return {key:`${type}:${identity}`,type,ids,editor,data,draftValues,changedFields,expectedRevision:model.revision,valid:typeof form.checkValidity!=='function'||form.checkValidity()};
}
export function scheduleEditorAutosave(form,{immediate=false}={}){const snapshot=editorSnapshot(form);if(snapshot)formAutosave.schedule(snapshot,{immediate});return Boolean(snapshot);}
function saveError(){return new Error(document.getElementById('notice')?.textContent || t('Не удалось сохранить изменение.'));}
async function saveEditorSnapshot(snapshot){
  if(snapshot.valid===false)throw new Error(t('Проверьте введённые значения.'));
  const {type,data,ids,editor}=snapshot,options={expectedRevision:snapshot.expectedRevision,preserveDrafts:true,autosave:true,changedFields:snapshot.changedFields};
  await contextQueue;await commandSettled;
  let result;
  if(type==='period'){
    const current=model.tasks.find(task=>task.id===ids.taskId),start=snapshot.changedFields.includes('start')?data.start:current?.start,end=snapshot.changedFields.includes('end')?data.end:current?.end;
    if(!start||!end||start>end)throw new Error(t('Конец периода не может быть раньше начала.'));
    result=await command('period_set',{task_id:ids.taskId,start,end},options);
  }else if(type==='task-rename'||type==='cube-rename'){
    const title=String(data.title||'').trim();if(!title)throw new Error(t('Название не может быть пустым.'));
    result=await command(type==='task-rename'?'task_rename':'cube_rename',{[type==='task-rename'?'task_id':'cube_id']:ids.taskId||ids.cubeId,title},options);
  }else if(type==='cube-placement')result=await saveCubePlacement(ids.cubeId,data,options);
  else if(type==='calendar-event-move')result=await moveCalendarEvent(ids.eventId,data,options);
  else if(type==='ruler-save'){
    const project=model.projects.find(item=>item.id===editor.id),custom=project?.kind==='goal',title=String(data.title||'').trim();
    if(!title)throw new Error(t('Название не может быть пустым.'));
    const patch={ruler_id:editor.id};for(const field of snapshot.changedFields){if(field==='title')patch.title=title;else if(field==='description')patch.description=String(data.description||'');else if(custom&&['start','end'].includes(field))patch[field]=data[field]||null;else if(field==='visible')patch.visible=Boolean(data.visible);}
    result=Object.keys(patch).length>1?await command('ruler_update',patch,options):model;
  }else if(type==='ruler-entry-save'){
    const title=String(data.title||'').trim();if(!title)throw new Error(t('Название не может быть пустым.'));
    const patch={ruler_id:editor.id,entry_id:editor.entryId};for(const field of snapshot.changedFields){if(field==='title')patch.title=title;else if(field==='date')patch.date=data.date||null;else if(field==='event_kind')patch.event_kind=data.event_kind||'milestone';else if(field==='full_completion')patch.full_completion=data.full_completion==='true'?true:data.full_completion==='false'?false:null;else if(field==='visible')patch.visible=Boolean(data.visible);}
    result=Object.keys(patch).length>2?await command('ruler_entry_upsert',patch,options):model;
  }
  if(!result)throw saveError();
  for(const [key,value] of snapshot.draftValues)if(drafts.has(key)&&String(drafts.get(key))===String(value))drafts.delete(key);
  if(editor && rulerEditor?.id===editor.id)rulerEditor.baseRevision=model.revision;
  return result;
}
async function onSubmit(event) {
  const form=event.target.closest('form[data-form]');if(!form)return;
  event.preventDefault();
  if(scheduleEditorAutosave(form,{immediate:true})){await formAutosave.flush();return model;}
  const data=Object.fromEntries(new FormData(form)),type=form.dataset.form;
  if(type==='ruler-save')return saveRuler({...data,visible:form.elements.visible?.checked});
  if(type==='ruler-entry-save')return saveRulerEntry({...data,visible:form.elements.visible?.checked});
  if(type==='cube-cancel')return command('cube_cancel',{cube_id:form.dataset.cubeId,reason:data.reason.trim()});
  if(type==='merge'){const selected=model.cubes.filter(cube=>mergeIds.has(cube.id)),check=mergeEligibility(selected);if(!check.ok)return notify(check.reason,true);const result=await command('cube_merge',{cube_ids:selected.map(cube=>cube.id),title:data.title.trim()});if(result){mergeIds.clear();mergeMode=false;clearDraftPrefix('merge-title');render();}return result;}
}
export async function saveCubePlacement(cubeId,data,options={}) {
  let cube=model.cubes.find(item=>item.id===cubeId),result;
  if(!cube){notify(t('Кубик уже изменён. Обновите план.'),true);return null;}
  const changed=options.changedFields||['date','start',...(data.minutes!==undefined?['minutes']:[])];
  data={...data,date:changed.includes('date')?data.date:cube.selected_date||context.date,start:changed.includes('start')?data.start:cube.start||null};const minutes=data.minutes!==undefined?Number(data.minutes):null;
  if(changed.includes('minutes') && minutes!==Number(cube.minutes)){
    if(!Number.isInteger(minutes)||minutes<1||minutes>1440){notify(t('Укажите длительность от 1 до 1440 минут.'),true);return null;}
    if(cube.kind==='calendar_optional'&&!cube.selected_date)result=await command('calendar_offer_estimate',{cube_id:cubeId,minutes},options);
    else result=await command('cube_resize',{cube_id:cubeId,date:cube.selected_date||null,start:cube.start||null,minutes},options);
    if(!result)return null;cube=model.cubes.find(item=>item.id===cubeId);
  }
  if(changed.some(field=>field==='date'||field==='start')){
    if(!data.date){notify(t('Требуется дата.'),true);return null;}
    const sameDate=cube.selected_date===data.date,action=cube.requires_estimate||cube.duration_unknown?'cube_drop':cube.selected_date?(sameDate?'cube_time':'cube_move'):'cube_select';
    const payload=action==='cube_time'?{cube_id:cubeId,start:data.start||null}:{cube_id:cubeId,date:data.date,start:data.start||null};
    result=await command(action,payload,options);if(!result)return null;
  }
  if(!options.preserveDrafts)for(const key of [`cube-date-${cubeId}`,`cube-time-${cubeId}`,`cube-estimate-${cubeId}`])clearDraftPrefix(key);
  return result||model;
}
function restoreInspectorAnchor() {if(!inspectorReturnAnchor)return;const anchor=[...document.getElementById('planner').querySelectorAll('[data-title-kind]')].find(item=>item.dataset.titleKind===inspectorReturnAnchor.kind && item.dataset.nameId===inspectorReturnAnchor.id && (!inspectorReturnAnchor.location || item.dataset.nameLocation===inspectorReturnAnchor.location));anchor?.querySelector('button')?.focus({preventScroll:true});}
export async function openCubeContents(cubeId,anchor) {if(!namedObject('cube',cubeId))return null;if(anchor)positionInspector(anchor,'cube',cubeId);await updateContext({cube_id:cubeId,calendar_event_id:null},{partialInspector:true});return context;}
export async function openCalendarContents(eventId,anchor,options={}) {
  const event=model.events.find(item=>item.id === eventId);
  if(!event)return null;if(anchor)positionInspector(anchor,'event',eventId);inspectorProvenance=Boolean(options.sourceDetails);
  await updateContext({date:event.date,week_start:monday(event.date),calendar_event_id:eventId,cube_id:null},{partialInspector:true});
  return context;
}
export async function openCalendarEvent(eventId) {return openCalendarContents(eventId);}
export async function moveCalendarEvent(eventId,data,options={}) {
  const event=model.events.find(item=>item.id === eventId);
  if (!event || !canMoveCalendarEvent(event)) {notify(t('Этот календарный блок закреплён; перенос недоступен.'),true);return null;}
  data={...data,date:options.changedFields&&!options.changedFields.includes('date')?event.date:data.date,start:options.changedFields&&!options.changedFields.includes('start')?event.start:data.start};
  const result=await command('calendar_event_move',{event_id:eventId,date:data.date,start:data.start},options);
  if (!result) return null;
  await updateContext({date:data.date,week_start:monday(data.date),calendar_event_id:eventId});
  return model;
}
export async function resizeCube(cubeId,data) {
  const cube=model.cubes.find(item=>item.id===cubeId),permission=cube && cubeResizePermission(cube);
  if(!permission?.allowed){notify(permission?.reason || t('Кубик уже изменён; обновите план.'),true);return null;}
  const result=await command('cube_resize',{cube_id:cubeId,date:data.date,start:data.start,minutes:Number(data.minutes)});
  if(result && context.date!==data.date)await updateContext({date:data.date,week_start:monday(data.date),cube_id:cubeId});
  return result;
}
function timelinePreview(state) {
  state.block.style.top=`${(state.startMinute-Number(state.canvas.dataset.startMinute))*state.pixelsPerMinute}px`;
  state.block.style.height=`${state.minutes*state.pixelsPerMinute}px`;
  state.block.dataset.startMinute=String(state.startMinute);state.block.dataset.durationMinutes=String(state.minutes);
  const label=`${compactDate(state.date)} · ${formatTimelineMinute(state.startMinute)}–${formatTimelineMinute(state.startMinute+state.minutes)} · ${minutesLabel(state.minutes)}`;
  const output=document.getElementById('timeline-preview');if(output)output.textContent=label;
  const time=state.block.querySelector('[data-block-time]');if(time)time.textContent=label;
}
export function beginTimelineGesture(event) {
  if(event.button!==0 || busy || nativeDragActive || event.target.closest('[data-title-edit],[data-cube-delete],input,textarea,.inline-name-editor'))return;
  const block=event.target.closest('[data-timeline-id]');if(!block)return;
  const handle=event.target.closest('[data-timeline-resize]');if(!handle && block.dataset.editableDrag!=='true')return;
  const canvas=block.closest('[data-timeline-canvas]'),item=block.dataset.timelineKind==='cube' ? model.cubes.find(cube=>cube.id===block.dataset.timelineId) : model.events.find(item=>item.id===block.dataset.timelineId);
  if(!canvas || !item)return;
  if(handle && !cubeResizePermission(item).allowed){notify(cubeResizePermission(item).reason,true);return;}
  const ppm=Number(canvas.dataset.pixelsPerMinute),rect=block.getBoundingClientRect();
  timelineGesture={block,canvas,item,kind:block.dataset.timelineKind,edge:handle?.dataset.timelineResize || null,pointerId:event.pointerId,originX:event.clientX,originY:event.clientY,sourceStart:Number(block.dataset.startMinute),sourceMinutes:Number(block.dataset.durationMinutes),startMinute:Number(block.dataset.startMinute),minutes:Number(block.dataset.durationMinutes),date:canvas.dataset.date,pixelsPerMinute:ppm,grabMinutes:(event.clientY-rect.top)/ppm,grabX:event.clientX-rect.left,sourceWidth:rect.width || rect.right-rect.left,sourceLeft:block.style.left,sourceCssWidth:block.style.width,moved:false,valid:true,captured:false};
  currentDragCubeId=block.dataset.timelineKind==='cube' ? item.id : null;
  if(handle){try{block.setPointerCapture(event.pointerId);timelineGesture.captured=true;}catch{/* Synthetic DOM events have no active platform pointer. */}}
  if(handle){event.preventDefault();handle.focus?.({preventScroll:true});}
}
export function moveTimelineGesture(event) {
  const state=timelineGesture;if(!state || event.pointerId!==state.pointerId)return;
  if(!state.moved && Math.hypot(event.clientX-state.originX,event.clientY-state.originY)<4)return;
  if(!state.captured){try{state.block.setPointerCapture(event.pointerId);state.captured=true;}catch{}}
  state.moved=true;event.preventDefault?.();state.block.classList.add('is-gesture');
  const scroll=state.canvas.closest('.timeline-scroll'),viewport=scroll?.getBoundingClientRect();
  if(viewport){if(event.clientY>viewport.bottom-24)scroll.scrollTop+=12;else if(event.clientY<viewport.top+24)scroll.scrollTop-=12;}
  const rect=state.canvas.getBoundingClientRect(),hit=document.elementFromPoint?.(event.clientX,event.clientY),day=hit?.closest('.day-cell:not(.is-past)'),overCanvas=hit?.closest('[data-timeline-canvas]');
  if(!state.edge && state.kind==='cube'){dragHints(hit,state.item.id);const merge=hit?.closest('[data-merge-target]'),target=merge && namedObject('cube',merge.dataset.mergeTarget);state.mergeTargetId=target && target.id!==state.item.id && mergeEligibility([target,state.item]).ok ? target.id : null;state.mergeRect=merge?.getBoundingClientRect();state.returnPool=hit?.closest('[data-drop-pool]')?.dataset.dropPool;}
  state.valid=Boolean(day || overCanvas || (event.clientY>=rect.top && event.clientY<=rect.bottom && event.clientX>=rect.left && event.clientX<=rect.right));
  if(day && !state.edge){state.date=day.dataset.date;state.startMinute=state.sourceStart;state.minutes=state.sourceMinutes;}
  else{
    state.date=state.canvas.dataset.date;
    const target=timelineHitMinute(event.clientY,rect.top,Number(state.canvas.dataset.startMinute),state.pixelsPerMinute,state.edge ? 0 : state.grabMinutes,event.shiftKey ? 1 : 5);
    if(state.edge)Object.assign(state,resizedTimelineSpan(state.sourceStart,state.sourceMinutes,state.edge,target,event.shiftKey ? 1 : 5));
    else state.startMinute=target;
  }
  timelinePreview(state);
  if(!state.edge && !overCanvas && (state.returnPool || state.mergeTargetId || day)){state.block.classList.add('is-outside-canvas');state.block.style.left=`${event.clientX-state.grabX}px`;state.block.style.top=`${event.clientY-state.grabMinutes*state.pixelsPerMinute}px`;state.block.style.width=`${state.sourceWidth}px`;}else{state.block.classList.remove('is-outside-canvas');state.block.style.left=state.sourceLeft;state.block.style.width=state.sourceCssWidth;}
}
export async function finishTimelineGesture(event) {
  if(!timelineGesture || event.pointerId!==timelineGesture.pointerId)return;
  const state=timelineGesture;timelineGesture=null;currentDragCubeId=null;clearDragHints();
  if(state.captured)try{state.block.releasePointerCapture(event.pointerId);}catch{}
  if(!state.moved){await refresh();return;}
  suppressTimelineClick=true;setTimeout(()=>{suppressTimelineClick=false;},0);gestureCommitPending=true;let result;
  try{
    if(state.mergeTargetId){beginMergePrompt(state.mergeTargetId,state.item.id,state.mergeRect);return;}
    if(state.returnPool){result=await returnCubeToPool(state.item.id,state.returnPool);return;}
    if(!state.valid){notify(t('Отпустите блок над временем или выбранной датой.'),true);return;}
    const placement=timelinePlacement(state.date,state.startMinute,state.minutes);
    if(state.edge)result=await resizeCube(state.item.id,placement);
    else if(state.kind==='event')result=await moveCalendarEvent(state.item.id,placement);
    else result=await command(state.item.selected_date===placement.date ? 'cube_time' : 'cube_move',state.item.selected_date===placement.date ? {cube_id:state.item.id,start:placement.start} : {cube_id:state.item.id,date:placement.date,start:placement.start});
  }finally{gestureCommitPending=false;if(!result)render();await refresh();}
}
function beginResize(event) {
  const handle = event.target.closest('[data-resize]'); if (!handle || event.button !== 0) return;
  const task = model.tasks.find(item => item.id === handle.dataset.taskId), track = handle.closest('.ribbon-track');
  if (!task || !track) return;
  event.preventDefault();
  resizeState = {task,side:handle.dataset.resize,week:handle.dataset.weekStart,rect:track.getBoundingClientRect(),start:task.start,end:task.end,handle,ribbon:handle.closest('.task-ribbon')};
  handle.setPointerCapture?.(event.pointerId); resizeState.ribbon.classList.add('is-resizing');
}
function moveResize(event) {
  if (!resizeState) return;
  const state = resizeState;
  const date = addDays(state.week,Math.max(0,Math.min(6,Math.floor((event.clientX - state.rect.left) / state.rect.width * 7))));
  if (state.side === 'start') state.start = date > state.end ? state.end : date;
  else state.end = date < state.start ? state.start : date;
  const span = periodSpan(state.start,state.end,state.week);
  if (span) state.ribbon.style.gridColumn = `${span.start}/${span.end}`;
  state.ribbon.querySelector('.ribbon-title > span').textContent = ui`${compactDate(state.start)}–${compactDate(state.end)} · отпустите, чтобы сохранить период`;
}
async function finishResize() {
  if (!resizeState) return;
  const state = resizeState; resizeState = null;
  if (state.start === state.task.start && state.end === state.task.end) {render(); return;}
  await formAutosave.flush();const latest=model.tasks.find(task=>task.id===state.task.id);await command('period_set',{task_id:state.task.id,start:state.side==='start'?state.start:latest.start,end:state.side==='end'?state.end:latest.end});
}
async function keyResize(event) {
  if(event.target.matches?.('[data-inline-title]')){if(event.key==='Enter'){event.preventDefault();await commitInlineRename(event.target.value);}else if(event.key==='Escape'){event.preventDefault();cancelInlineRename();}return;}
  if(event.key==='F2'){const host=event.target.closest('[data-title-kind]');if(host){event.preventDefault();beginInlineRename(host.dataset.titleKind,host.dataset.nameId,host.dataset.nameLocation,host.getBoundingClientRect());return;}}
  if(event.key==='Escape' && event.target.closest('#ruler-manager')){event.preventDefault();if(!rulerEditor?.create&&!rulerEditor?.newEntry)await formAutosave.flush();closeRulerEditor();return;}
  const arrowViewport=event.target.closest('[data-arrow-viewport]');if(arrowViewport && ['+','=','-','0'].includes(event.key)){event.preventDefault();const plot=arrowViewport.querySelector('[data-testid="project-time-arrow"]'),current=Number(plot.dataset.pixelsPerDay);zoomArrowPanel(arrowViewport,event.key==='0' ? projectArrowModel(model).pixelsPerDay : current*(event.key==='-' ? 1/1.35 : 1.35),arrowViewport.clientHeight/2);return;}
  const timelineHandle=event.target.closest('[data-timeline-resize]');
  if(timelineHandle && ['ArrowUp','ArrowDown'].includes(event.key)){
    event.preventDefault();const cube=model.cubes.find(item=>item.id===timelineHandle.dataset.cubeId);if(!cube)return;
    const edge=timelineHandle.dataset.timelineResize,delta=(event.key==='ArrowUp' ? -1 : 1)*(event.shiftKey ? 1 : 5),start=timeMinutes(cube.start),target=(edge==='start' ? start : start+cube.minutes)+delta,span=resizedTimelineSpan(start,cube.minutes,edge,target,event.shiftKey ? 1 : 5);
    gestureCommitPending=true;try{await resizeCube(cube.id,timelinePlacement(cube.selected_date,span.startMinute,span.minutes));}finally{gestureCommitPending=false;await refresh();}return;
  }
  const handle = event.target.closest('[data-resize]');
  if (handle && ['ArrowLeft','ArrowRight'].includes(event.key)) {
    event.preventDefault(); const task = model.tasks.find(item => item.id === handle.dataset.taskId); const delta = (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 7 : 1);
    const start = handle.dataset.resize === 'start' ? addDays(task.start,delta) : task.start;
    const end = handle.dataset.resize === 'end' ? addDays(task.end,delta) : task.end;
    if (start > end) return notify(t('Период должен содержать хотя бы один день.'),true);
    await command('period_set',{task_id:task.id,start,end}); return;
  }
  const day = event.target.closest('.day-cell'); if (day && event.target === day && ['Enter',' '].includes(event.key)) {event.preventDefault(); day.click();}
}
function dragStart(event) {
  if(timelineGesture){event.preventDefault();return;}
  const block=event.target.closest('[data-timeline-id]'),calendar=event.target.closest('[data-event-id][draggable="true"]'),cube=event.target.closest('[data-cube-id][draggable="true"]');
  if(!calendar && !cube)return;
  nativeDragActive=true;currentDragCubeId=cube?.dataset.cubeId || null;event.dataTransfer.setData(calendar ? 'application/x-work-calendar-event' : 'application/x-work-cube',calendar ? calendar.dataset.eventId : cube.dataset.cubeId);
  event.dataTransfer.setData('application/x-work-grab-minutes',String(block ? Math.max(0,(event.clientY-block.getBoundingClientRect().top)/TIMELINE_PIXELS_PER_MINUTE) : 0));event.dataTransfer.effectAllowed='move';
}
function dragOver(event) {if(currentDragCubeId)dragHints(event.target,currentDragCubeId);const target = event.target.closest('[data-timeline-canvas],.day-cell:not(.is-past),[data-drop-pool],[data-merge-target],[data-drop-selected]'); if (target) {event.preventDefault();if(event.dataTransfer)event.dataTransfer.dropEffect = 'move';}}
function dropPlacement(event) {
  const canvas=event.target.closest('[data-timeline-canvas]');
  if(canvas){const minute=timelineHitMinute(event.clientY,canvas.getBoundingClientRect().top,Number(canvas.dataset.startMinute),Number(canvas.dataset.pixelsPerMinute),Number(event.dataTransfer.getData('application/x-work-grab-minutes')) || 0,event.shiftKey ? 1 : 5);return timelinePlacement(canvas.dataset.date,minute,0);}
  const selected=event.target.closest('[data-drop-selected]');if(selected)return {date:selected.dataset.date};const day=event.target.closest('.day-cell:not(.is-past)');return day ? {date:day.dataset.date} : null;
}
async function dropCube(event) {
  const merge=event.target.closest('[data-merge-target]'),pool=event.target.closest('[data-drop-pool]'),draggedId=event.dataTransfer.getData('application/x-work-cube');
  if(merge && draggedId){event.preventDefault();nativeDragActive=false;currentDragCubeId=null;clearDragHints();beginMergePrompt(merge.dataset.mergeTarget,draggedId,merge.getBoundingClientRect());return;}
  if(pool){event.preventDefault();nativeDragActive=false;currentDragCubeId=null;clearDragHints();if(draggedId){gestureCommitPending=true;try{await returnCubeToPool(draggedId,pool.dataset.dropPool);}finally{gestureCommitPending=false;await refresh();}}else notify(t('Событие остаётся в календаре.'),true);return;}
  const placement=dropPlacement(event);if(!placement)return;
  event.preventDefault();nativeDragActive=false;currentDragCubeId=null;clearDragHints();gestureCommitPending=true;
  try{
    const eventId=event.dataTransfer.getData('application/x-work-calendar-event');
    if(eventId){const calendar=model.events.find(item=>item.id===eventId);await moveCalendarEvent(eventId,{date:placement.date,start:placement.start || calendar?.start});return;}
    const cubeId=event.dataTransfer.getData('application/x-work-cube'),cube=model.cubes.find(item=>item.id===cubeId);if(!cube)return;
    const date=placement.date,start=placement.start || cube.start;
    await command('cube_drop',{cube_id:cubeId,date,start:start || null});
  }finally{gestureCommitPending=false;await refresh();}
}
export function plannerState(){return {model,contextValue:context,gestureActive:gestureActive(),inlineEdit,rulerEditor};}
export async function prepareLeave(){
  if(gestureActive())throw new Error(t('Завершите перемещение кубика перед переходом.'));
  await contextQueue;await commandSettled;
  if(inlineEdit){
    await inlineEdit.ready;
    if(inlineEdit.saving)await commandSettled;
    else await commitInlineRename(inlineEdit.value);
    if(inlineEdit)throw new Error(document.getElementById('notice')?.textContent||t('Сохраните название перед переходом.'));
  }
  await contextQueue;await commandSettled;
  await formAutosave.flush();
  if(drafts.size){
    const message=rulerEditor?.create?t('Завершите создание цели перед переходом.'):rulerEditor?.newEntry?t('Завершите создание отметки перед переходом.'):t('Проверьте введённые значения.');
    notify(message,true);throw new Error(message);
  }
  return {date:context.date,mode:'week'};
}
if (hasDOM) {
  const root = document.getElementById('planner');
  root.addEventListener('click',event=>onClick(event).catch(error=>notify(error.message,true))); root.addEventListener('change',event=>onChange(event).catch(error=>notify(error.message,true))); root.addEventListener('submit',event=>onSubmit(event).catch(error=>notify(error.message,true)));
  root.addEventListener('dblclick',event=>{const host=event.target.closest('[data-title-kind]');if(host){event.preventDefault();event.stopPropagation();beginInlineRename(host.dataset.titleKind,host.dataset.nameId,host.dataset.nameLocation,host.getBoundingClientRect());return;}if(event.target.closest('button,input'))return;const viewport=event.target.closest('[data-arrow-viewport]');if(viewport){event.preventDefault();zoomArrowPanel(viewport,projectArrowModel(model).pixelsPerDay,viewport.clientHeight/2);}});
  root.addEventListener('focusout',event=>{if(event.target.closest?.('[data-time-entry-id]'))hideArrowTooltip();if(internalRender || event.target.isConnected===false || inlineEdit?.saving || !event.target.matches?.('[data-inline-title]') || event.relatedTarget?.closest?.('.inline-name-editor') || inlineEdit?.kind==='merge')return;void commitInlineRename(event.target.value);});
  root.addEventListener('mouseover',showArrowTooltip);root.addEventListener('focusin',showArrowTooltip);root.addEventListener('mouseout',event=>{const marker=event.target.closest('[data-time-entry-id]');if(marker && event.relatedTarget?.closest?.('[data-time-entry-id]')!==marker)hideArrowTooltip();});
  root.addEventListener('input',event => {if(event.target.dataset.inlineTitle && inlineEdit)inlineEdit.value=event.target.value;if (event.target.dataset.draft) drafts.set(event.target.dataset.draft,event.target.type==='checkbox' ? event.target.checked : event.target.value);const form=event.target.closest?.('form[data-form]');if(form)scheduleEditorAutosave(form);});
  root.addEventListener('keydown',keyResize);root.addEventListener('wheel',onArrowWheel,{passive:false});root.addEventListener('pointerdown',beginResize);root.addEventListener('pointerdown',beginTimelineGesture);root.addEventListener('pointerdown',beginArrowPan);
  root.addEventListener('scroll',syncArrowScroll,true);
  document.addEventListener('pointermove',moveResize);document.addEventListener('pointermove',moveTimelineGesture);document.addEventListener('pointermove',moveArrowPan);document.addEventListener('pointerup',finishResize);document.addEventListener('pointerup',finishTimelineGesture);document.addEventListener('pointerup',finishArrowPan);document.addEventListener('pointercancel',()=>{if(nativeDragActive)return;resizeState=null;timelineGesture=null;arrowPan=null;if(model)render();void refresh();});
  root.addEventListener('dragstart',dragStart);root.addEventListener('dragover',dragOver);root.addEventListener('drop',dropCube);root.addEventListener('dragend',()=>{nativeDragActive=false;currentDragCubeId=null;clearDragHints();void refresh();});

  window.addEventListener('resize',()=>{[...document.getElementById('planner').querySelectorAll('[data-arrow-panel]')].forEach(settleArrowLabels);});
  refresh().then(()=>model?resolveReady(model):rejectReady(new Error(t('План пока недоступен. Подключение повторится автоматически.')))); poll = setInterval(refresh,750); window.addEventListener('pagehide',() => {clearInterval(poll);formAutosave.dispose();},{once:true});
}
