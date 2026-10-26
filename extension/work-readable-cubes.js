import {esc} from './work-icons.js';
import {renderAwardArt} from './work-trophy-art.js';
import {validImportanceScore} from './importance-score.js';
import {t} from './i18n.js';
import {isDeletedCube} from './work-deletions.js';
export const READABLE_LAYOUTS=[{id:'compact',label:t("Компактные блоки"),width:89,height:62}];
export const normalizeOrbitHalf=value=>value==='morning'?'morning':'afternoon';
export const orbitHalfAt=minutes=>typeof minutes==='number'&&Number.isFinite(minutes)&&minutes>=0&&minutes<1440?(minutes<720?'morning':'afternoon'):null;
export function setReadableLayout(){return 'compact';}
export function readableLayout(){return 'compact';}
export function readableCardDimensions(_value='compact',_variant='day',viewportWidth=globalThis.innerWidth??Infinity){return viewportWidth<=600?{width:86,height:61}:{width:89,height:62};}
export function readableSource(c,typeLabel=t("Задача")){
  if(c.kind==='brainalot')return ['Brainalot',c.sourceLabel].filter(Boolean).join(' · ');
  if(c.kind==='calendar')return t("Календарь");if(c.kind==='weekly_review')return t("Недельный разбор");
  const course=/^(alpha|beta)$/i.test(c.course||'')?'':c.course;
  return c.taskTitle||course||c.sourceLabel||(c.kind==='prep'?t("Подготовка к уроку"):typeLabel);
}
const TYPE_ART={practice:'crystal',slides:'peak',script:'compass',research:'order',prep:'relic',brainalot:'obelisk',calendar:'sun',weekly_review:'book',task:'enamel'};
const CATEGORY_COLORS={research:'#76accf',practice:'#70bdb4',slides:'#ad9cd6',script:'#cfb187',prep:'#94c6b5',brainalot:'#b6b8d0',calendar:'#c4b18c',weekly_review:'#aabcc7',task:'#76accf'};
export const compactCardVariant=c=>c.award_variant||TYPE_ART[c.type||c.color||c.kind]||'enamel';
export const compactCardMinutes=(c,finished=Number(c.progress_percent??c.progress)>=100)=>finished?(c.actual_minutes??c.actual??c.minutes??c.planned??0):(c.minutes??c.planned??0);
export function readableCardContents(c,{typeLabel=t("Задача"),duration=null,finished=Number(c.progress_percent??c.progress)>=100}={}){
  const percent=finished?100:Math.max(0,Math.min(100,Number(c.progress_percent??c.progress)||0)),type=c.type||c.color||c.kind||'task',variant=compactCardVariant(c),color=CATEGORY_COLORS[type]||CATEGORY_COLORS.task,showCheck=finished&&c.status!=='cancelled'&&!c.cancelled;
  return '<span class="token small-award-token" data-color="'+esc(type)+'" data-award-variant="'+esc(variant)+'"'+(!c.award_variant?' data-art-preview="true"':'')+'><span class="rc-progress" style="width:'+percent+'%" aria-hidden="true"></span><span class="compact-award-top">'+renderAwardArt(variant,{compact:true,categoryColor:color})+'<span class="tiny-title">'+esc(c.title||typeLabel)+'</span></span><span class="token-meta"><span>'+esc(duration??t("{String} мин", {String: String(compactCardMinutes(c,finished))}))+'</span>'+(showCheck?'<svg class="rc-done-check" viewBox="0 0 16 16" aria-hidden="true"><path d="m2.5 8 3.5 3.5 7.5-8"/></svg>':'')+'</span></span>';
}
const cubeKey=c=>c.canonical_id||c.blueprint_ref||c.id;
export const sourceImportanceKey=c=>String(cubeKey(c)||'');
const validImportance=validImportanceScore;
export function sortSourceImportance(cubes,importance=()=>0){
  return cubes.map((cube,index)=>{const value=importance(cube);return {cube,index,value:validImportance(value)?value:0};}).sort((a,b)=>b.value-a.value||a.index-b.index).map(row=>row.cube);
}
export function prioritizeSourcePools(pools,importance){return {...pools,chains:sortSourceImportance(pools.chains,group=>importance({task_id:group.task_id})),external:sortSourceImportance(pools.external,importance),future:sortSourceImportance(pools.future,importance)};}
export function sourceImportanceControl(c,value=0,{editable=true,saving=false}={}){
  const importance=validImportance(value)?value:0,key=sourceImportanceKey(c);
  if(!editable)return '<span class="rc-source-importance" aria-label="'+esc(t('Важность: {value} из 5. Нет связанной задачи для изменения',{value:importance}))+'"><span>'+esc(t('Важн.'))+'</span><span>'+importance+'</span></span>';
  return '<label class="rc-source-importance"><span>'+esc(t('Важн.'))+'</span><select data-field="source-importance" data-id="'+esc(c.id)+'" data-importance-key="'+esc(key)+'" data-focus-key="source-importance-'+esc(encodeURIComponent(key))+'" aria-label="'+esc(t('Важность: {title}',{title:c.title||t('Кубик')}))+'" '+(saving?'disabled aria-busy="true"':'')+'>'+Array.from({length:6},(_,i)=>'<option value="'+i+'" '+(importance===i?'selected':'')+'>'+i+'</option>').join('')+'</select></label>';
}
export function fullCubeInfo(c,{importance=0,typeLabel=t("Задача")}={}){
  const rawPercent=Number(c.progress_percent??c.progress)||0,percent=Math.max(0,Math.min(100,rawPercent)),plan=c.minutes??c.planned??c.planned_minutes??0,actual=c.actual_minutes??c.actual??0;
  const lines=[String(c.title||typeLabel),readableSource(c,typeLabel),t('План: {plan} мин · Готовность: {percent}% · Факт: {actual} мин · Важность: {importance}/5',{plan,percent,actual,importance:validImportance(importance)?importance:0})];
  const output=String(c.output||'').trim(),comment=String(c.comment||'').trim();if(output&&output.length<=200)lines.push(t('Результат: {output}',{output}));if(comment&&comment.length<=200)lines.push(t('Комментарий: {comment}',{comment}));
  const steps=Array.isArray(c.steps)?c.steps:[],resources=c.resource_drafts||c,groups=Array.isArray(resources.groups)?resources.groups:[],links=[...(Array.isArray(resources.links)?resources.links:[]),...groups.flatMap(group=>Array.isArray(group.links)?group.links:[])].filter(link=>link&&(link.title||link.url));
  if(steps.length||links.length)lines.push([steps.length?t('Шагов: {count}',{count:steps.length}):'',links.length?t('Материалов: {count}',{count:links.length}):''].filter(Boolean).join(' · '));if(c.allocation_stale)lines.push(t('Предложение устарело. Пересчитайте план перед выбором.'));return lines.filter(Boolean).join('\n');
}
const numberOrder=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
export function orderSourceCubes(cubes,segments=[]){
  const segmentById=new Map(segments.map((segment,index)=>[segment.id,{segment,index}]));
  const key=(cube,index)=>{const match=segmentById.get(cube.segment_id),segment=match?.segment||{};return [numberOrder(cube.phase_order)??numberOrder(segment.phase_order)??numberOrder(segment.order)??match?.index??Number.MAX_SAFE_INTEGER,numberOrder(cube.sequence)??numberOrder(cube.order)??numberOrder(segment.sequence)??0,index];};
  return cubes.map((cube,index)=>({cube,key:key(cube,index)})).sort((a,b)=>{for(let i=0;i<a.key.length;i++)if(a.key[i]!==b.key[i])return a.key[i]-b.key[i];return String(cubeKey(a.cube)).localeCompare(String(cubeKey(b.cube)));}).map(item=>item.cube);
}
export const validPlanMonth=value=>typeof value==='string'&&/^\d{4}-\d{2}$/.test(value)&&Number(value.slice(0,4))>0&&Number(value.slice(5))>=1&&Number(value.slice(5))<=12;
export function approvedMonthlyPlan(planning={}){
  const plan=planning.monthly_plan,fields=['id','month','status','approved_at','task_ids'];
  if(!plan||typeof plan!=='object'||Object.keys(plan).length!==fields.length||fields.some(field=>!Object.hasOwn(plan,field)))return null;
  const tasks=new Set((planning.tasks||[]).map(task=>task.id));
  return typeof plan.id==='string'&&/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(plan.id)&&validPlanMonth(plan.month)&&plan.status==='approved'&&typeof plan.approved_at==='string'&&/T.*(?:Z|\+00:00)$/.test(plan.approved_at)&&Number.isFinite(Date.parse(plan.approved_at))&&Array.isArray(plan.task_ids)&&new Set(plan.task_ids).size===plan.task_ids.length&&plan.task_ids.every(id=>tasks.has(id))?plan:null;
}
export function approvedMonthlyFuture(cube,planning,date){
  const plan=approvedMonthlyPlan(planning),day=cube.planned_date;
  return !!(plan&&cube.task_id&&plan.task_ids.includes(cube.task_id)&&typeof day==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(day)&&day>date&&day.startsWith(plan.month)&&Number.isFinite(Date.parse(day+'T12:00:00Z'))&&new Date(day+'T12:00:00Z').toISOString().slice(0,10)===day);
}
export function sourcePools({planning={},date,taskIds=[],cubes=[],selectedIds=[],hiddenIds=[],restoredIds=[]}){
  const selected=new Set(selectedIds);for(const cube of cubes)if(selected.has(cube.id)||selected.has(cubeKey(cube)))selected.add(cubeKey(cube));const isSelected=c=>selected.has(c.id)||selected.has(cubeKey(c));const hidden=new Set(hiddenIds),restored=new Set(restoredIds),map=new Map();
  for(const cube of cubes){const key=cubeKey(cube);if(!key||isDeletedCube(planning,cube)||hidden.has(cube.id)||hidden.has(key))continue;const prior=map.get(key);if(prior&&prior.id===key&&cube.id!==key)continue;map.set(key,{...prior,...cube});}
  const all=[...map.values()],chosen=[...new Set(taskIds)],chosenSet=new Set(chosen),kept=all.filter(c=>c.task_id&&(!c.kind||c.kind==='task')&&!chosenSet.has(c.task_id)&&!approvedMonthlyFuture(c,planning,date)&&(isSelected(c)||restored.has(c.id)||((Number(c.progress_percent)>0||Number(c.actual_minutes)>0)&&(!c.report_date||c.report_date===date))));
  const groupIds=[...chosen,...new Set(kept.map(c=>c.task_id))],keptIds=new Set(kept.map(cubeKey));
  const chains=groupIds.map(taskId=>({task_id:taskId,title:planning.tasks?.find(t=>t.id===taskId)?.title||all.find(c=>c.task_id===taskId)?.taskTitle||t("Задача"),retained:!chosenSet.has(taskId),cubes:orderSourceCubes(all.filter(c=>c.task_id===taskId&&(!c.kind||c.kind==='task')&&(chosenSet.has(taskId)||keptIds.has(cubeKey(c)))),planning.task_segments||[])}));
  const assigned=new Set(chains.flatMap(g=>g.cubes.map(cubeKey))),externalKinds=new Set(['brainalot','calendar','prep','weekly_review']);
  const external=all.filter(c=>!assigned.has(cubeKey(c))&&externalKinds.has(c.kind)&&(!c.planned_date||c.planned_date===date||isSelected(c))&&(!c.report_date||c.report_date===date||isSelected(c)));
  const future=orderSourceCubes(all.filter(c=>!assigned.has(cubeKey(c))&&!externalKinds.has(c.kind)&&(Number(c.progress_percent)||0)<100&&approvedMonthlyFuture(c,planning,date)),planning.task_segments||[]);
  return {chains,external,future};
}
export function sourceCardContents(c,{typeLabel=t("Задача"),selected=false,done=Number(c.progress_percent)>=100,source=readableSource(c,typeLabel)}={}){
  const type=c.type||c.color||c.kind||'task',color=CATEGORY_COLORS[type]||CATEGORY_COLORS.task,percent=Math.max(0,Math.min(100,Number(c.progress_percent)||0));
  return '<span class="rc-source-progress" style="width:'+percent+'%" aria-hidden="true"></span><span class="rc-source-top">'+renderAwardArt(compactCardVariant(c),{compact:true,categoryColor:color})+'<strong>'+esc(c.title||typeLabel)+'</strong></span><span class="rc-source-duration">'+esc(t("{String} мин",{String:String(compactCardMinutes(c,done))}))+(done&&c.status!=='cancelled'&&!c.cancelled?'<svg class="rc-done-check" viewBox="0 0 16 16" aria-hidden="true"><path d="m2.5 8 3.5 3.5 7.5-8"/></svg>':'')+'</span>'+(selected||done?'<span class="rc-source-state">'+(done?t("Завершён"): t("В дне"))+'</span>':'');
}
export function hoverCubeContents(c,{typeLabel=t("Задача"),done=Number(c.progress_percent??c.progress)>=100}={}){
  const type=c.type||c.color||c.kind||'task',variant=compactCardVariant(c),color=CATEGORY_COLORS[type]||CATEGORY_COLORS.task;
  const source=readableSource(c,typeLabel),output=String(c.output||'').trim(),normalize=value=>String(value||'').trim().replace(/\s+/g,' ').toLocaleLowerCase(),showSource=source&&normalize(source)!==normalize(c.title||typeLabel);
  return '<span class="rc-hover-art">'+renderAwardArt(variant,{compact:true,categoryColor:color})+'</span><span class="rc-hover-copy"><strong>'+esc(c.title||typeLabel)+'</strong>'+(showSource?'<small>'+esc(source)+'</small>':'')+(output?'<span class="rc-hover-output">'+esc(output)+'</span>':'')+'<span class="rc-hover-meta">'+esc(t("{String} мин",{String:String(compactCardMinutes(c,done))}))+(done&&c.status!=='cancelled'&&!c.cancelled?'<svg class="rc-done-check" viewBox="0 0 16 16" aria-hidden="true"><path d="m2.5 8 3.5 3.5 7.5-8"/></svg>':'')+'</span></span>';
}
export function hoverCubePosition(rect,viewport,preview){
  const width=Math.min(236,Math.max(0,viewport.width-16)),height=preview.height;
  return {width,left:Math.max(8,Math.min(rect.left-(width-rect.width)/2,viewport.width-width-8)),top:Math.max(8,Math.min(rect.top-(height-rect.height)/2,viewport.height-height-8))};
}
