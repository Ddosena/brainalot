import { esc, ic } from "./work-icons.js";
import { t, localeTag } from "./i18n.js";
import { timeClient, sharedApi,formatTime } from "./time-client.js";
import { startCubeTimer, pauseCubeTimer, stopCubeTimer, cubeTimerState, recordedCubeMinutes } from "./work-cube-timer.js";
import { START,END,clamp,formatReadiness,cubeDimensions,dayLoadReference,dayLoadBreakdown,dayTimelineEntries,nearestPlanningSlot,timelineResizeMinutes,clock,minutes,shiftDate,monday,nextMonday,dateLabel,todayIn,validUrl,resourcesForSave,resourcesForEdit,launchGroups,typeOf,TYPES,COLORS,constructorModel } from "./work-constructor-model.js?v=constructor-20261023-a";
import { createTraySlots,shouldUnstickPanel } from "./work-constructor-slots.js";
import { historyCubes, pedestalShelf, awardsPanel,awardArt,SHELF_STYLES,monthHtml,statusLabel,goalEarned } from "./work-constructor-history.js?v=constructor-20261023-a";
import {createKeyedSaveQueue,createCompletionClaims,transientOrbitModel,goalPreviewLabel} from "./work-constructor-motion.js";
import { uniqueTaskCubes,taskPoolChange,readTaskChoice,saveTaskChoice } from "./work-constructor-selection.js";
import { renderDayOrbit,currentDayPosition,fitOrbitLabels,mountOrbitCountdown } from "./work-day-orbit.js";
import {validPlanMonth,approvedMonthlyPlan,prioritizeSourcePools,sourceImportanceControl,sourcePools,sourceCardContents,setReadableLayout,readableLayout,readableCardContents,compactCardMinutes,readableSource,hoverCubeContents,hoverCubePosition,timelineCubeInfo} from "./work-readable-cubes.js";
import {orbitDropMinute} from './work-orbit-drop.js';


import {sourceImportance as canonicalSourceImportance,sourceImportanceTarget,saveSourceImportance,sourceNote} from "./work-source-importance.js";
import {isDeletedCube,normalizeDeletionState,confirmedCubeDelete} from './work-deletions.js';

const safe=value=>esc(String(value??"")),copy=value=>structuredClone(value);
const factGuardReason=()=>t("У блока нет исходного кубика Work для фактов и таймера.");
export function compactTimelineDate(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return '';
  const date=new Date(value+'T12:00:00Z');if(Number.isNaN(date.getTime())||date.toISOString().slice(0,10)!==value)return '';
  const parts=new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC',weekday:'short',day:'2-digit',month:'2-digit'}).formatToParts(date),part=name=>parts.find(row=>row.type===name)?.value||'';
  return part('weekday').replace(/\.$/,'').toLocaleUpperCase('ru-RU')+' · '+part('day')+'.'+part('month');
}
const uid=()=>("cube-"+(crypto.randomUUID?.()||Date.now()+"-"+Math.random())).replace(/[^a-zA-Z0-9:-]/g,"");
const fields=["kind","task_id","segment_id","source_ref","minutes","title","output","steps","comment","planned_date","links","groups","resource_drafts","week_position","blueprint_ref"];
export function cubeCommand(cube){return Object.fromEntries(fields.filter(key=>cube[key]!==undefined).map(key=>[key,copy(cube[key])]));}
const stableValue=value=>Array.isArray(value)?value.map(stableValue):value&&typeof value==="object"?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stableValue(value[key])])):value;
const equalValue=(a,b)=>JSON.stringify(stableValue(a))===JSON.stringify(stableValue(b));
export function constructorPatch(base,draft,latest,dirty){
  const changed=[...dirty],value=(cube,key)=>key==="resources"?resourcesForEdit(cube):["progress_percent","actual_minutes"].includes(key)?Number(cube?.[key])||0:key==="planned_date"?cube?.[key]??null:cube?.[key]??(["steps"].includes(key)?[]:"");
  const conflicts=changed.filter(key=>!equalValue(value(base,key),value(latest,key))&&!equalValue(value(draft,key),value(latest,key)));
  if(conflicts.length){const error=new Error(t("Поле изменилось в другой вкладке. Черновик сохранён — выберите нужную версию."));error.code="constructor-edit-conflict";error.fields=conflicts;throw error;}
  const patch={cube_id:draft.id};for(const key of changed)if(fields.includes(key))patch[key]=copy(draft[key]);if(dirty.has("resources"))Object.assign(patch,resourcesForSave(draft));return patch;
}
export function weekProposals(planning,start){
  const end=shiftDate(start,6),exempt=new Set((planning.result?.tasks||[]).filter(t=>t.scope_exempt).map(t=>t.id)),saved=planning.result?.constructor?.cubes||Object.values(planning.constructor?.cubes||{}),blueprints=new Set(saved.flatMap(c=>[c.blueprint_ref,c.id?.startsWith("planned-")?c.id.slice(8):c.id]).filter(Boolean));
  return [...saved.filter(c=>c.planned_date>=start&&c.planned_date<=end&&(!exempt.has(c.task_id)||c.selected_date||c.progress_percent>0||c.actual_minutes>0)),...(planning.result?.cubes||[]).filter(c=>!exempt.has(c.task_id)&&!c.manual&&!c.locked&&c.date>=start&&c.date<=end&&!blueprints.has(c.id)).map(c=>({...c,id:"planned-"+c.id,blueprint_ref:c.id,kind:"task",planned_date:c.date,candidate:true,progress_percent:0,actual_minutes:0}))].filter(cube=>!isDeletedCube(planning,cube));
}
export function groupWeekCubes(cubes){const groups=new Map();for(const cube of cubes){const key=JSON.stringify([cube.planned_date,cube.task_id||cube.kind+":"+cube.source_ref]);if(!groups.has(key))groups.set(key,{key,date:cube.planned_date,task_id:cube.task_id,cubes:[]});groups.get(key).cubes.push(cube);}return [...groups.values()];}
export function createWorkConstructor(root,ctx){
  let workStyle='navy',orbitTimer=null,taskPanelOpen=false,monthlyDraft=null,monthlyDraftBaseline=null,monthlyEditorOpen=false,monthlySaving=false;const poolPins=new Map(),importanceSaving=new Set(),importanceWrites=createKeyedSaveQueue(),preferences=globalThis.localStorage;
  try{const options=JSON.parse(preferences.getItem('brainalot.work.readable.v1')||'{}');setReadableLayout(options.layout);}catch{}
  function sourceImportance(c){return canonicalSourceImportance(c,planning()||{},dashboard);}
  const importanceKey=target=>target.kind+':'+target.id;
  function importanceControl(c){const target=sourceImportanceTarget(c,planning()||{},dashboard);return sourceImportanceControl(c,target.score,{editable:target.editable&&(target.kind!=='note'||!dashboardError),saving:importanceSaving.has(importanceKey(target))});}
  function changeSourceImportance(c,value){
    const target=sourceImportanceTarget(c,planning()||{},dashboard),key=importanceKey(target);
    return trackOperation(importanceWrites(key,async()=>{
      if(disposed)return;
      importanceSaving.add(key);error='';notice=t('Сохраняем важность…');render();
      try{
        await saveSourceImportance(target,value,{action:(name,data)=>commit(name,data),api:ctx.api||sharedApi});
        if(target.kind==='note')await refreshDashboard();
        notice=t('Важность сохранена');
      }catch(cause){
        if(target.kind==='note'&&cause.status===409){await refreshDashboard();cause.message=t('Задача изменилась. Важность не перезаписана — проверьте новое значение.');}
        throw cause;
      }finally{importanceSaving.delete(key);if(!disposed)render();}
    }));
  }
  function poolPinned(){if(!poolPins.has(selectedDate)){let value=false;try{value=JSON.parse(preferences.getItem('brainalot.work.pool.v1:'+selectedDate)||'{}').pinned===true;}catch{}poolPins.set(selectedDate,value);}return poolPins.get(selectedDate);}
  function setPoolPinned(value){const previousTop=$('#cc-selected')?.getBoundingClientRect?.().top;poolPins.set(selectedDate,value);try{preferences.setItem('brainalot.work.pool.v1:'+selectedDate,JSON.stringify({pinned:value}));}catch{}render();const tray=$('#cc-selected'),bounds=tray?.getBoundingClientRect?.(),scroller=value?root:document.scrollingElement||document.documentElement;if(bounds&&scroller&&Number.isFinite(previousTop)){const safeTop=value?(root.getBoundingClientRect?.().top||0)+($('.cc-goal-strip')?.getBoundingClientRect?.().height||0)+($('.cc-timeline-panel')?.getBoundingClientRect?.().height||0)+16:0;scroller.scrollTop+=bounds.top-Math.max(previousTop,safeTop);}}
  function requirePoolEditing(){if(poolPinned())throw new Error(t('Пул закреплён. Нажмите «Изменить пул», чтобы изменить состав.'));}
  let selectedDate=/^\d{4}-\d{2}-\d{2}$/.test(ctx.initialDate||"")?ctx.initialDate:null,mode=ctx.initialMode==="month"?"month":"day",historyMonth=null,historyPath=[],weekStart=null,weekKey=null,stage=null,hours=2,search="",busy=0,error="",notice="",pending=null,dashboard={},dashboardError="",serial=Promise.resolve(),popup=null,popupObserver=null,popupFrame=0,saveTimer=null,drag=null,suppressClick=0,disposed=false,resizeTimer=null,lastUndo=null;
  let deleteIntent=null,deleteBusy=false;
  const pendingActions=new Set(),popupSaves=new Set();
  const trackOperation=operation=>{pendingActions.add(operation);operation.then(()=>pendingActions.delete(operation),()=>pendingActions.delete(operation));return operation;};
  const popupBases=new Map(),popupConflicts=new Map(),dirtyFields=new Map(),drafts=new Map(),geometries=new Map(),physics=new Map(),pendingBlocks=new Set(),returned=new Map(),scrolls={tasks:0,week:0,catalog:0},sourceScrolls=new Map();
  const planning=()=>ctx.state?.planning;
  const deletionState=()=>normalizeDeletionState(ctx.deletionState?.());
  const deletionFrozen=()=>deletionState().pending.length>0;
  const popupDeleted=()=>!!popup&&isDeletedCube(planning(),getPopupCube()||{id:popup.id});
  const deletedMessage=()=>t('Кубик уже удалён. Ваш черновик сохранён для просмотра.');
  function requireMutableCube(cube){
    if(deletionFrozen())throw new Error(t('Удаление ещё выполняется. Другие изменения Work временно приостановлены.'));
    if(cube&&isDeletedCube(planning(),cube))throw new Error(deletedMessage());
  }
  const taskChoices=new Map(),knownTimerIds=new Set(),activeEffects=new Set(),enqueuePopupSave=createKeyedSaveQueue(),completionClaims=createCompletionClaims();let taskChangeSerial=Promise.resolve(),goalEditor=null,shelfStyle='rail',orbitShowDate=false;
  try{const options=JSON.parse(preferences.getItem('brainalot.work.constructor.options.v1')||'{}');if(SHELF_STYLES.some(([id])=>id===options.shelfStyle))shelfStyle=options.shelfStyle;orbitShowDate=!!options.orbitShowDate;}catch{}
  function persistOptions(){try{preferences.setItem('brainalot.work.constructor.options.v1',JSON.stringify({shelfStyle,orbitShowDate}));}catch{notice=t('Настройки доступны до закрытия этой вкладки.');}}
  const taskChoiceKey=()=> 'brainalot.work.tasks.v1:'+selectedDate;
  function taskChoice(){if(!taskChoices.has(selectedDate)){const m=model(),fallback=[m.main,...m.selected.map(c=>c.task_id),...m.possible.map(c=>c.task_id)];taskChoices.set(selectedDate,readTaskChoice(preferences,taskChoiceKey(),fallback));}return taskChoices.get(selectedDate);}
  function persistTaskChoice(){try{saveTaskChoice(preferences,taskChoiceKey(),taskChoice());}catch{notice=t('Выбор задач сохранён только в этой вкладке.');}}
  function taskProtection(){const timerIds=new Set([...knownTimerIds,...(taskChoice().protectedCubeIds||[])]),historyIds=new Set(Object.keys(planning()?.completed_cubes||{}));for(const [id,dirty] of dirtyFields)if(dirty.size)historyIds.add(id);for(const c of [...model().all,...model().selected]){const timer=cubeTimerState(c);if(timer.status!=='idle'||timer.elapsed_ms>0)timerIds.add(c.id);}return {timerIds,historyIds};}
  async function toggleTask(taskId){
    requirePoolEditing();
    const choice=taskChoice(),remove=choice.taskIds.includes(taskId),next=remove?choice.taskIds.filter(id=>id!==taskId):[...choice.taskIds,taskId];
    if(remove){
      // A previous timer may no longer be the current timer. Read its durable totals
      // before deciding that a cube is untouched; failure keeps the cubes safe.
      const affected=[...model().selected,...model().possible].filter(c=>c.task_id===taskId);
      try{await timeClient.refresh();const totals=await sharedApi('/api/time/totals');for(const row of totals.rows||[])if(row.task?.source==='work'&&Number(row.total_ms)>0&&row.task.id?.startsWith('work-cube:'))knownTimerIds.add(row.task.id.slice(10));}
      catch{for(const c of affected)knownTimerIds.add(c.id);notice=t('Время не удалось проверить. Кубики этой задачи сохранены.');}
      const protections=taskProtection(),change=taskPoolChange(model().selected,next,protections);choice.protectedCubeIds=[...new Set([...(choice.protectedCubeIds||[]),...taskPoolChange(affected,next,protections).retained.map(c=>c.id)])];for(const cube of change.removed)if(cube.task_id===taskId)await returnCube(cube.id,{composition:true});
      choice.restoredCubeIds=(choice.restoredCubeIds||[]).filter(id=>!affected.some(c=>c.id===id));
    }
    choice.taskIds=next;choice.focusTask=remove?next[0]:taskId;stage=null;persistTaskChoice();render();
  }
  const $=selector=>root.querySelector(selector);
  const model=()=>constructorModel(planning()||{},selectedDate||todayIn(planning()?.settings?.timezone),dashboard);
  const canonical=id=>(planning()?.result?.constructor?.cubes||[]).find(c=>c.id===id)||planning()?.result?.constructor?.goal_cubes?.[id]||(planning()?.result?.cubes||[]).find(c=>c.id===id);
  const decorated=c=>{const task=planning()?.tasks?.find(x=>x.id===c.task_id),segment=planning()?.task_segments?.find(x=>x.id===c.segment_id);return {...c,importance_score:sourceImportance(c),title:c.title||task?.title||"",steps:c.steps||[],output:c.output||"",comment:c.comment||"",taskTitle:task?.title||"",course:task?.course||"",segmentTitle:segment?.title||"",type:typeOf(c,segment),links:c.links||[],groups:c.groups||[]};};
  const allCubes=()=>[...model().selected,...model().possible,...model().hidden,...weekProposals(planning()||{},weekStart||nextMonday(selectedDate)),...virtualTasks(),...futureCandidates()];
  const find=id=>{const raw=canonical(id)||allCubes().find(c=>c.id===id);return raw?decorated({...raw,...drafts.get(id)}):null;};
  const canRecordFacts=c=>{const latest=canonical(c?.id)||c;return latest?.period_can_record_progress!==false&&latest?.period_read_only!==true;};
  function requireRecordableCube(c){if(!canRecordFacts(c))throw new Error(factGuardReason());}
  const isSelected=id=>model().selected.some(c=>c.id===id);
  const writable=()=>selectedDate>=todayIn(planning()?.settings?.timezone);
  const toast=message=>ctx.toast?.(message);
  function activeTasks(){const p=planning()||{},rows=p.result?.tasks||[];return (p.tasks||[]).filter(task=>!task.scope_exempt&&!rows.find(r=>r.id===task.id)?.scope_exempt);}
  function futureCandidates(){
    const p=planning()||{},saved=p.result?.constructor?.cubes||[],used=new Set(saved.flatMap(c=>[c.id,c.blueprint_ref].filter(Boolean)));
    return [...saved.filter(c=>c.kind==='task'||c.task_id&&!c.kind),...(p.result?.cubes||[]).filter(c=>!used.has(c.id)&&!c.manual&&!c.locked&&c.task_id&&c.date>selectedDate).map(c=>({...c,id:'planned-'+c.id,blueprint_ref:c.id,kind:'task',planned_date:c.date,candidate:true,progress_percent:0,actual_minutes:0}))].filter(cube=>!isDeletedCube(p,cube)).map(decorated);
  }
  function virtualTasks(){
    const p=planning()||{},m=model(),existing=new Set([...m.selected,...m.possible,...m.hidden,...futureCandidates().filter(c=>!c.report_date&&(!c.planned_date||c.planned_date>=selectedDate))].map(c=>c.task_id+':'+c.segment_id)),list=[];
    for(const entry of p.result?.task_pool||[]){
      if(entry.scope_exempt||!activeTasks().some(task=>task.id===entry.task_id)||!taskChoice().taskIds.includes(entry.task_id))continue;
      const segments=entry.segments?.length?entry.segments:[null];
      for(const segment of segments){const available=segment?.available_for_manual_minutes??segment?.remaining_minutes??entry.available_for_manual_minutes??0;if(available<5||existing.has(entry.task_id+':'+segment?.id))continue;
        list.push(decorated({id:'pool:'+entry.task_id+':'+(segment?.id||'main'),kind:'task',task_id:entry.task_id,...(segment?{segment_id:segment.id}:{}),minutes:Math.min(60,Math.floor(available/5)*5),title:segment?.title||entry.title,output:segment?.output||'',steps:segment?.steps||[],comment:'',candidate:true,links:[],groups:[]}));
      }
    }
    return list.filter(cube=>!isDeletedCube(p,cube));
  }
  function possible(){
    const m=model(),priority=returned.get(selectedDate)||[],raw=[...m.possible,...virtualTasks()],overrides=new Set(taskChoice().restoredCubeIds||[]),list=uniqueTaskCubes([...taskPoolChange(raw,taskChoice().taskIds,taskProtection()).included,...raw.filter(c=>overrides.has(c.id))]),ids=new Set();
    return list.filter(c=>{if(Number(c.progress_percent)>=100||ids.has(c.id)||m.hidden.some(h=>h.id===c.id))return false;ids.add(c.id);return true;}).sort((a,b)=>priority.indexOf(a.id)>=0?-1:priority.indexOf(b.id)>=0?1:0);
  }
  function snapshotPhysics(){for(const instance of physics.values())instance.destroy();physics.clear();}
  let disposeOrbitCountdown=()=>{};
  function mountOrbitFeatures(host){disposeOrbitCountdown();fitOrbitLabels(host);disposeOrbitCountdown=mountOrbitCountdown(host);}
  function render(){
    disposeOrbitCountdown();
    if(disposed)return;const p=planning(),focused=document.activeElement,focusKey=root.contains(focused)?focused?.dataset?.focusKey:null,selection=focusKey&&focused.matches?.("input,textarea")?[focused.selectionStart,focused.selectionEnd]:null;
    for(const el of root.querySelectorAll('[data-source-scroll]'))sourceScrolls.set(el.dataset.sourceScroll,{x:el.scrollLeft,y:el.scrollTop});if($(".rc-catalog-list"))scrolls.catalog=$(".rc-catalog-list").scrollTop;if($(".cc-task-strip"))scrolls.tasks=$(".cc-task-strip").scrollLeft;if($(".cc-week-scroll"))scrolls.week=$(".cc-week-scroll").scrollLeft;snapshotPhysics();
    if(!p){root.innerHTML='<div class="work-constructor cc-empty"><h2>'+safe(t("План пока не загружен"))+'</h2><p>'+safe(t("Проекты, доска, заметки и изображения сохраняются."))+'</p></div>';return;}
    selectedDate??=todayIn(p.settings?.timezone);weekStart??=nextMonday(selectedDate);historyMonth??=selectedDate.slice(0,7);const m=model();
    root.innerHTML='<div class="work-constructor" data-testid="work-constructor" data-collecting="'+(mode==='day'&&!poolPinned())+'" data-work-style="'+safe(workStyle)+'" data-card-layout="'+readableLayout()+'">'+
      '<nav class="cc-tabs"'+(ctx.embeddedNavigation?' data-embedded-navigation="true"':'')+'>'+(!ctx.embeddedNavigation?'<button data-action="mode-day" aria-selected="'+(mode==='day')+'">'+safe(t('День'))+'</button><button data-action="mode-week" aria-selected="'+(mode==='week')+'">'+safe(t('Неделя'))+'</button><button data-action="mode-month" aria-selected="'+(mode==='month')+'">'+safe(t('История'))+'</button>':'')+'<span class="cc-save-state" role="status">'+safe(error||notice||(busy?t('Сохраняем…'):''))+'</span></nav>'+deletionBanner()+(pending?'<section class="cc-warning" role="alert">'+safe(t('Изменение не подтверждено.'))+' <button data-action="retry">'+safe(t('Повторить'))+'</button>'+(pending.action==='cube_delete'?'<button data-action="deletion-check">'+safe(t('Проверить состояние'))+'</button>':'<button data-action="discard">'+safe(t('Отменить запрос'))+'</button>')+'</section>':'')+
      (mode==="day"?goalsHtml()+'<div class="cc-results-row">'+pedestalShelf(planning(),selectedDate,pendingBlocks,shelfStyle)+awardsPanel(planning(),selectedDate)+'</div>'+timelineHtml(m)+(!m.initialized?'<section class="cc-warning"><strong>'+safe(t("Этот день ещё использует прежний план"))+'</strong><p>'+safe(t("Включите конструктор явно: существующие уроки, выбранные кубики и факты сохранятся. Подготовки станут предложениями до выбора."))+'</p><button class="cc-primary" data-action="initialize" data-testid="initialize-constructor">'+safe(t("Включить конструктор для этого дня"))+'</button></section>':"")+dayHtml(m):mode==="week"?goalsHtml()+weekHtml():goalsHtml()+monthHtml(planning(),historyMonth,historyPath))+'</div>';
    const host=$('.work-constructor'),goals=$('.cc-goal-strip');if(host&&goals)host.style.setProperty('--cc-goal-height',Math.ceil(goals.getBoundingClientRect().height)+'px');if(host&&$('.cc-timeline-panel'))host.style.setProperty('--cc-timeline-height',Math.ceil($('.cc-timeline-panel').getBoundingClientRect().height)+'px');
    for(const [selector,key] of [[".cc-task-strip","tasks"],[".cc-week-scroll","week"]])if($(selector)){const el=$(selector);el.style.scrollBehavior="auto";el.scrollLeft=scrolls[key];el.style.scrollBehavior="";}
    for(const el of root.querySelectorAll('[data-source-scroll]')){const saved=sourceScrolls.get(el.dataset.sourceScroll);if(saved){el.scrollLeft=saved.x;el.scrollTop=saved.y;}}
    if(focusKey){const target=[...root.querySelectorAll("[data-focus-key]")].find(el=>el.dataset.focusKey===focusKey);target?.focus({preventScroll:true});try{if(selection&&target?.matches?.("input,textarea")&&target?.setSelectionRange)target.setSelectionRange(...selection);}catch{}}
    mountPhysics();mountOrbitFeatures($('.cc-orbit-host'));bindWheel();if(popup)renderPopup();updateTimers();scheduleOrbitClock();
    if(deletionFrozen()||deleteBusy)for(const element of root.querySelectorAll('button,input,textarea,select'))if(!readAction(element.dataset?.action)&&!['date','history-month','search'].includes(element.dataset?.field))element.disabled=true;
  }
  function deletionBanner(){
    const state=deletionState();
    const detail=rows=>rows.filter(row=>typeof row.error==='string'&&row.error).map(row=>'<p>'+safe(row.error)+'</p>').join('');
    if(state.pending.length)return '<section class="cc-warning" role="status">'+safe(t('Удаление ещё выполняется. Другие изменения Work временно приостановлены.'))+detail(state.pending)+' <button data-action="deletion-check">'+safe(t('Проверить состояние'))+'</button></section>';
    if(state.conflicts.length)return '<section class="cc-warning" role="alert">'+safe(t('Удаление не выполнено. Обновите данные перед новой попыткой.'))+detail(state.conflicts)+' <button data-action="deletion-check">'+safe(t('Обновить данные'))+'</button></section>';
    return '';
  }
  function readAction(name){return ['mode-day','mode-week','mode-month','previous','next','today','preview-date','week-prev','week-next','week-task','month-goal','month-day','month-root','month-path','inspect','history-inspect','trophy-inspect','popup-close','delete-cancel','deletion-check','retry','discard','refresh-dashboard','resources-open'].includes(name);}
  function goalsHtml(){
    const p=planning()||{},week=mode==='week'?weekStart:monday(selectedDate),goals=p.result?.constructor?.goals||[];
    return '<section class="cc-goal-strip cc-goals-readable" aria-label="'+safe(t('Цели дня и недели'))+'">'+[['day',selectedDate,t('Цель дня')],['week',week,t('Цель недели')]].map(([scope,date,label])=>{
      const goal=goals.find(g=>g.scope===scope&&g.date===date),meta=scope==='day'?p.constructor?.days?.[date]:p.constructor?.weeks?.[date],title=goal?.title??meta?.goal??'',percent=clamp(goal?.percent??0,0,100),earned=goalEarned(goal),editing=goalEditor===scope+':'+date;
      return '<article class="cc-goal-readable" data-testid="'+scope+'-goal"><div class="cc-goal-fill" style="width:'+percent+'%"></div><div class="cc-goal-copy"><div><b>'+safe(label)+'</b><small>'+safe(dateLabel(date))+'</small></div><h2>'+safe(title||t('Запишите ожидаемый результат'))+'</h2><div class="cc-goal-meta"><span>'+safe(statusLabel(goal))+'</span><button data-action="goal-editor" data-focus-key="goal-editor-'+scope+'" data-scope="'+scope+'" data-goal-date="'+date+'" aria-expanded="'+editing+'">'+safe(editing?t('Закрыть редактор'):t('Изменить'))+'</button></div></div><div class="cc-goal-preview '+(earned?'is-earned':'is-preview')+'"'+(!goal?.award_variant?' title="'+safe(t('Вид награды определяется при завершении'))+'"':'')+'>'+awardArt(goal||{scope},{preview:!earned})+'<small>'+safe(earned?t('Получена'):t(goalPreviewLabel(goal,scope)))+'</small></div>'+(editing?'<div class="cc-goal-editor"><label>'+safe(t('Ожидаемый результат'))+'<input data-goal-title="'+scope+'" data-goal-date="'+date+'" data-focus-key="goal-'+scope+'" value="'+safe(title)+'" maxlength="1000"></label><div>'+[['completed',t('Выполнена вручную')],['cancelled',t('Неактуальна')],['active',t('Вернуть в работу')]].map(([status,text])=>'<button data-action="goal-close" data-scope="'+scope+'" data-goal-date="'+date+'" data-status="'+status+'">'+safe(text)+'</button>').join('')+'</div></div>':'')+'</article>';
    }).join('')+'</section>';
  }
  function timelineNowHtml(position=currentDayPosition(selectedDate)){
    if(!position.today||position.nowMinutes<START||position.nowMinutes>END)return '';
    const percent=(position.nowMinutes-START)/(END-START)*100;
    return '<svg class="cc-timeline-now" data-testid="timeline-current-time" data-now="'+position.nowMinutes+'" role="img" aria-label="'+safe(t('Текущее время {time}',{time:position.label}))+'" viewBox="0 0 16 15" style="left:'+percent+'%"><path d="M 8 15 L 0 0 L 16 0 Z"/></svg>';
  }
  function timelineHtml(m){
    const entries=dayTimelineEntries(m),fixed=entries.events.map(e=>'<div class="cc-event" style="left:'+clamp((e.start-START)/(END-START)*100,0,100)+'%;width:'+Math.max(.5,e.minutes/(END-START)*100)+'%" data-ctooltip="'+safe(e.title+" · "+clock(e.start)+"–"+clock(e.start+e.minutes))+'"><b>'+safe(e.title)+'</b><small>'+safe(clock(e.start))+'</small></div>').join("");
    const chosen=entries.cubes.map(c=>{const detail=timelineCubeInfo(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,importance:sourceImportance(c)});return '<button class="cc-timeline-cube" data-ccube="'+safe(c.id)+'" data-tray="timeline" data-ctooltip="'+safe(c.title)+'" aria-label="'+safe(detail)+'" style="left:'+((c.start-START)/(END-START)*100)+'%;width:'+(c.minutes/(END-START)*100)+'%;background:'+(COLORS[c.type]||COLORS.task)+';'+(pendingBlocks.has(c.id)?"visibility:hidden":"")+'"><b>'+safe(c.title)+'</b><small>'+safe(clock(c.start))+" · "+safe(minutes(c.minutes))+'</small>'+(m.initialized&&writable()&&!['calendar','weekly_review'].includes(c.kind)&&!Number(c.progress_percent)&&!Number(c.actual_minutes)?'<span class="cc-time-resize" data-timeline-resize aria-hidden="true"></span>':'')+'</button>';}).join("");
    const fullDate=dateLabel(selectedDate,true);
    return '<section class="cc-timeline-panel" data-testid="constructor-day"><nav class="cc-date" aria-label="'+safe(t('Выбранная дата')+' · '+fullDate)+'"><button data-action="previous" aria-label="'+safe(t('Предыдущий день'))+'" title="'+safe(t('Предыдущий день'))+'">'+ic('chevL')+'</button><time datetime="'+selectedDate+'" title="'+safe(fullDate)+'" aria-label="'+safe(fullDate)+'">'+safe(compactTimelineDate(selectedDate))+'</time><button data-action="next" aria-label="'+safe(t('Следующий день'))+'" title="'+safe(t('Следующий день'))+'">'+ic('chevR')+'</button></nav><div class="cc-time-scroll"><div class="cc-timeline-info" data-testid="timeline-cube-info" aria-live="off"></div><div class="cc-timeline" data-testid="constructor-timeline">'+Array.from({length:15},(_,i)=>'<span class="cc-hour" style="left:'+i/14*100+'%">'+clock(START+i*60)+'</span><i class="cc-tick" style="left:'+i/14*100+'%"></i>').join("")+fixed+chosen+timelineNowHtml()+'</div></div></section>';
  }
  function cubeHtml(cube,tray,variant="day"){
    const c={...cube,...drafts.get(cube.id)},size=cubeDimensions(c.minutes,variant),type=c.type||typeOf(c),color=COLORS[type]||COLORS.task,selected=isSelected(c.id),full=[c.title,c.taskTitle,c.segmentTitle,TYPES[type],minutes(c.minutes),c.comment].filter(Boolean).join(" · ");
    return '<button class="cc-cube rc-card '+(selected&&tray==="possible"?"is-taken ":"")+(returned.get(selectedDate)?.[0]===c.id?"is-returned":"")+'" data-ccube="'+safe(c.id)+'" data-tray="'+tray+'" data-minutes="'+c.minutes+'" data-layout="'+readableLayout()+'" data-ctooltip="'+safe(full)+'" aria-label="'+safe(full)+'" data-testid="cube-'+safe(c.id)+'" style="width:'+size.width+'px;height:'+size.height+'px;'+(pendingBlocks.has(c.id)?'visibility:hidden':'')+'">'+readableCardContents(c,{typeLabel:TYPES[type]||TYPES.task,duration:minutes(compactCardMinutes(c))})+'</button>';
  }
  function completedForDay(){return historyCubes(planning()).filter(c=>Number(c.progress_percent)>=100&&(c.report_date||c.selected_date||c.planned_date)===selectedDate).map(decorated);}
  function sourceCardHtml(c,selectedIds){
    const done=Number(c.progress_percent)>=100,on=selectedIds.has(c.id),type=c.type||typeOf(c),source=readableSource(c,TYPES[type]||TYPES.task),tooltip=[c.title,source,t('План')+' '+minutes(c.minutes),c.planned_date?dateLabel(c.planned_date):''].filter(Boolean).join(' · ');
    return '<article class="rc-source-card '+(on?'is-taken ':'')+(done?'is-done':'')+'" data-row-id="'+safe(c.id)+'" '+(pendingBlocks.has(c.id)?'style="visibility:hidden"':'')+'><button class="rc-source-open" data-action="'+(done?'history-inspect':'inspect')+'" data-id="'+safe(c.id)+'" data-ccube="'+safe(c.id)+'" data-tray="'+(done?'history':on?'selected':'possible')+'" data-focus-key="source-'+safe(c.id)+'" data-ctooltip="'+safe(tooltip)+'" aria-label="'+safe(c.title+' · '+source+' · '+minutes(compactCardMinutes(c,done)))+'">'+sourceCardContents(c,{typeLabel:TYPES[type]||TYPES.task,selected:on,done,source})+'</button></article>';
  }
  function monthPlanDraft(){
    if(!monthlyDraft){const plan=approvedMonthlyPlan(planning());monthlyDraft={month:plan?.month||selectedDate.slice(0,7),taskIds:[...(plan?.task_ids||[])]};monthlyDraftBaseline=copy(monthlyDraft);}
    return monthlyDraft;
  }
  function monthPlanHtml(){
    const draft=monthPlanDraft(),tasks=planning()?.tasks||[];
    return '<details class="rc-month-plan" data-month-plan-editor '+(monthlyEditorOpen?'open':'')+'><summary>'+safe(t('План месяца'))+'</summary><div><label>'+safe(t('Месяц'))+'<input type="month" data-field="month-plan-month" data-focus-key="month-plan-month" value="'+safe(draft.month)+'" '+(monthlySaving?'disabled':'')+'></label><div class="rc-month-plan-tasks">'+tasks.map(task=>'<label><input type="checkbox" data-field="month-plan-task" data-task="'+safe(task.id)+'" data-focus-key="month-plan-task-'+safe(task.id)+'" '+(draft.taskIds.includes(task.id)?'checked':'')+' '+(monthlySaving?'disabled':'')+'><span>'+safe(task.title)+'</span></label>').join('')+'</div><button data-action="month-plan-save" '+(monthlySaving||!validPlanMonth(draft.month)?'disabled':'')+'>'+safe(monthlySaving?t('Сохраняем…'):t('Сохранить план месяца'))+'</button></div></details>';
  }
  async function saveMonthPlan(){
    if(monthlySaving)return;
    const draft=monthPlanDraft(),month=draft.month,known=new Set((planning()?.tasks||[]).map(task=>task.id)),task_ids=[...new Set(draft.taskIds)].filter(id=>known.has(id));
    if(!validPlanMonth(month))throw new Error(t('Выберите существующий месяц.'));
    monthlySaving=true;render();
    try{await commit('monthly_plan_approve',{month,task_ids});monthlyDraft=null;monthlyEditorOpen=false;}
    finally{monthlySaving=false;if(!disposed)render();}
  }
  function catalogHtml(m){
    const selectedIds=new Set(m.selected.map(c=>c.id)),required=new Set(m.events.filter(e=>e.required).map(e=>e.stable_id||e.occurrence_id||e.id));
    const canonicalSources=m.all.filter(c=>(!c.selected_date||c.selected_date===selectedDate)&&(!c.report_date||c.report_date===selectedDate)&&!(c.kind==='calendar'&&required.has(c.source_ref)&&!selectedIds.has(c.id)));
    const all=uniqueTaskCubes([...m.selected,...completedForDay(),...possible(),...canonicalSources,...futureCandidates().filter(c=>(!c.selected_date||c.selected_date===selectedDate)&&(!c.report_date||c.report_date===selectedDate))]);
    const pools=prioritizeSourcePools(sourcePools({planning:planning(),date:selectedDate,taskIds:taskChoice().taskIds,cubes:all,selectedIds:[...selectedIds],hiddenIds:m.hidden.map(c=>c.id),restoredIds:taskChoice().restoredCubeIds||[]}),sourceImportance);
    const tasks=activeTasks().map((task,index)=>({task,index})).sort((a,b)=>sourceImportance({task_id:b.task.id})-sourceImportance({task_id:a.task.id})||a.index-b.index).map(row=>row.task),choice=taskChoice(),selectedNames=tasks.filter(task=>choice.taskIds.includes(task.id)),retained=taskPoolChange([...m.selected,...m.possible],choice.taskIds,taskProtection()).retained;
    const choiceHtml='<details class="rc-task-choice" '+(taskPanelOpen?'open':'')+'><summary>'+safe(t('Выбрать задачи'))+'</summary><div>'+tasks.map(task=>'<label><input type="checkbox" data-field="source-task" data-task="'+safe(task.id)+'" data-focus-key="task-choice-'+safe(task.id)+'" '+(choice.taskIds.includes(task.id)?'checked':'')+' '+(poolPinned()?'disabled':'')+'><span>'+safe(task.title)+'</span></label>').join('')+'</div></details>';
    const chainHtml=pools.chains.map(group=>'<section class="rc-task-chain" data-task="'+safe(group.task_id)+'"><h3>'+safe(group.title)+(group.retained?'<small>'+safe(t('Сохранённые кубики'))+'</small>':'')+'</h3><div class="rc-chain-scroll" data-source-scroll="chain-'+safe(group.task_id)+'">'+(group.cubes.map(c=>sourceCardHtml(c,selectedIds)).join('')||'<p class="rc-source-empty">'+safe(t('Доступных кубиков пока нет'))+'</p>')+'</div></section>').join('');
    const monthly=approvedMonthlyPlan(planning()),monthLabel=monthly?.month||'';
    return '<section class="rc-sources" id="cc-possible" data-catalog-drop data-testid="source-pools">'+(dashboardError?'<p class="cc-warning" role="status">'+safe(dashboardError)+'</p>':'')+'<section class="rc-task-source" data-source-pool="tasks"><header><h2>'+safe(t('Кубики задач'))+'</h2>'+choiceHtml+'</header>'+(!selectedNames.length?'<p class="rc-source-empty">'+safe(t('Выберите задачи для общего пула'))+'</p>':'')+chainHtml+(retained.length?'<p class="cc-retained">'+safe(t('Сохранены начатые и исторические кубики'))+': '+retained.length+'</p>':'')+'</section><div class="rc-secondary-sources"><section class="rc-source-column" data-source-pool="external"><h2>'+safe(t('Brainalot и календарь'))+'</h2><div class="rc-source-scroll" data-source-scroll="external">'+(pools.external.map(c=>sourceCardHtml(c,selectedIds)).join('')||'<p class="rc-source-empty">'+safe(t('Предложений пока нет'))+'</p>')+'</div></section><section class="rc-source-column" data-source-pool="future"><h2>'+safe(t('Будущие задачи'))+'</h2>'+monthPlanHtml()+(pools.future.length?'<small class="rc-month-origin">'+safe(t('Согласованный план'))+' · '+safe(monthLabel)+'</small>':'')+'<div class="rc-source-scroll" data-source-scroll="future">'+(pools.future.map(c=>sourceCardHtml(c,selectedIds)).join('')||'<p class="rc-source-empty">'+safe(monthly?t('В согласованном плане нет будущих кубиков'):t('Нет согласованного месячного плана'))+'</p>')+'</div></section></div><details class="rc-hidden-list"><summary>'+safe(t('Не сегодня'))+' · '+m.hidden.length+'</summary>'+m.hidden.map(c=>'<div><span>'+safe(c.title)+'</span><button data-action="restore" data-id="'+safe(c.id)+'">'+safe(t('Вернуть'))+'</button></div>').join('')+'</details></section>';
  }
  function dayHtml(m){
    const selected=m.selected.filter(c=>c.start==null&&Number(c.progress_percent)<100),active=m.selected.filter(c=>Number(c.progress_percent)<100),pinned=poolPinned();
    return '<main class="cc-day-grid">'+dayResourceHtml(m)+'<div class="cc-work-area"><section class="cc-tray-panel rc-selected '+(pinned?'is-pinned':'is-following')+'" id="cc-selected" data-pool-pinned="'+pinned+'"><header><h2>'+safe(t('В дне'))+'</h2><span>'+active.length+' · '+safe(minutes(active.reduce((sum,c)=>sum+c.minutes,0)))+'</span><button data-action="pool-pin" data-focus-key="pool-pin" aria-label="'+safe(pinned?t('Изменить пул в этом окне'):t('Закрепить пул в этом окне'))+'" aria-pressed="'+pinned+'">'+safe(pinned?t('Изменить пул'):t('Закрепить пул'))+'</button></header><div class="cc-tray" data-physical="selected" data-testid="selected-tray">'+selected.map(c=>cubeHtml(c,'selected')).join('')+(selected.length?'':'<p class="cc-tray-empty">'+safe(t('Добавьте кубик из источников'))+'</p>')+'</div><div class="rc-tray-footer"><div class="cc-trash" data-trash="selected">'+ic('trash')+'<span>'+safe(t('Не сегодня'))+'</span></div><details class="rc-execution-returns"><summary>'+safe(t('Вернуть кубик'))+'</summary><div>'+active.map(c=>'<button data-action="return" data-id="'+safe(c.id)+'" '+(pinned?'disabled':'')+'>'+safe(c.title)+'</button>').join('')+'</div></details><button data-action="clear" '+(!m.initialized||!active.length||pinned?'disabled':'')+'>'+safe(t('Вернуть все'))+'</button></div></section>'+catalogHtml(m)+'</div></main>';
  }
  function orbitHtml(m,position=currentDayPosition(selectedDate)){const entries=dayTimelineEntries(m);return renderDayOrbit({date:selectedDate,start:START,end:END,events:entries.events.map(event=>({...event,color:"#e8b55b"})),cubes:entries.cubes.map(cube=>({...cube,color:COLORS[cube.type]||COLORS.task})),...position,labels:'curved',half:'afternoon',showDate:orbitShowDate});}
  function refreshClocks(){const position=currentDayPosition(selectedDate),host=$(".cc-orbit-host"),timeline=$('[data-testid="constructor-timeline"]');if(host){host.innerHTML=orbitHtml(model(),position);mountOrbitFeatures(host);}if(timeline){timeline.querySelector('.cc-timeline-now')?.remove();timeline.insertAdjacentHTML('beforeend',timelineNowHtml(position));}}
  function scheduleOrbitClock(){clearTimeout(orbitTimer);if(!disposed&&!document.hidden&&mode==="day")orbitTimer=setTimeout(()=>{refreshClocks();scheduleOrbitClock();},60000-Date.now()%60000);}
  function orbitVisibility(){clearTimeout(orbitTimer);if(document.hidden){leaveHover();return;}if(!document.hidden){void refreshDashboard();refreshClocks();scheduleOrbitClock();}}
  function dayResourceHtml(m){
    const s=m.summary,load=dayLoadReference(planning(),selectedDate,s),parts=dayLoadBreakdown(m);
    const rows=[[t("Календарь"),parts.calendar],[t("Незавершённые — резерв"),parts.unfinished],[t("Завершённые — факт"),parts.completedActual],...(parts.completedEstimate?[[t("Завершённые без факта — план"),parts.completedEstimate]]:[]),...(parts.returnedActual?[[t("Факт возвращённых кубиков"),parts.returnedActual]]:[]),...(parts.returnedEstimate?[[t("Прогресс без факта"),parts.returnedEstimate]]:[]),...(parts.other?[[t("Другие учтённые нагрузки"),parts.other]]:[])];
    return '<aside class="cc-resource"><div class="cc-orbit-host" data-testid="day-orbit-host">'+orbitHtml(m)+'</div><section class="cc-load-summary"><div class="cc-load-heading"><strong class="cc-load" data-load>'+safe(minutes(load.total))+'</strong><span>'+safe(t('из'))+' '+safe(minutes(load.budget))+'</span></div><div class="cc-energy-bar" role="meter" aria-label="'+safe(t('Нагрузка дня'))+'" aria-valuemin="0" aria-valuemax="'+load.budget+'" aria-valuenow="'+load.total+'"><span style="width:'+load.fill+'%"></span></div><details><summary>'+safe(t('Нагрузка и настройки'))+'</summary><dl>'+rows.map(([label,value])=>'<div><dt>'+safe(t(label))+'</dt><dd data-load-component="'+value+'">'+(value<0?'−':'')+safe(minutes(Math.abs(value)))+'</dd></div>').join('')+'</dl><p>'+safe(t('Первоначальный план'))+': '+safe(minutes(parts.plannedSelected))+'</p><p>'+safe(t('Факт'))+': <span data-day-actual>'+safe(minutes(parts.actual))+'</span></p><label>'+safe(t('Энергия дня'))+'<input type="number" min="0" max="24" step="0.5" data-field="energy" value="'+s.budget/60+'"></label>'+calendarHtml(m)+'<button data-action="recalculate">'+safe(t('Пересчитать будущие дни'))+'</button><button data-action="refresh-dashboard">'+safe(t('Обновить предложения'))+'</button></details></section></aside>';
  }
  function calendarHtml(m){return '<details class="cc-list"><summary>'+safe(t("Календарь · обязательность в Work"))+'</summary><p>'+safe(t("Локальный флаг. Google Calendar не изменяется."))+'</p>'+m.events.map(e=>'<label class="cc-event-option"><input type="checkbox" data-field="event-required" data-event="'+safe(e.stable_id||e.occurrence_id||e.id)+'" '+(e.required?"checked":"")+' '+(!m.initialized?"disabled":"")+'><span>'+safe(e.title||e.summary)+'<small>'+safe(String(e.start||"").slice(11,16))+'</small></span></label>').join("")+'</details>';}
  function weekGroups(){return groupWeekCubes(weekProposals(planning()||{},weekStart)).map(g=>({...g,title:planning()?.tasks?.find(task=>task.id===g.task_id)?.title||g.cubes[0]?.title||""}));}
  function weekHtml(){
    const groups=weekGroups(),chosen=groups.find(g=>g.key===weekKey);
    return '<section class="cc-week"><header><div><h2>'+safe(t("Подготовка недели"))+'</h2><p>'+safe(dateLabel(weekStart))+" — "+safe(dateLabel(shiftDate(weekStart,6),true))+'</p></div><div><button data-action="week-prev" aria-label="'+safe(t("Предыдущая неделя"))+'">'+ic("chevL")+'</button><button data-action="week-next" aria-label="'+safe(t("Следующая неделя"))+'">'+ic("chevR")+'</button></div></header><div class="cc-week-scroll"><div class="cc-week-days">'+Array.from({length:7},(_,i)=>{const date=shiftDate(weekStart,i),items=groups.filter(g=>g.date===date),total=items.flatMap(g=>g.cubes).reduce((sum,c)=>sum+c.minutes,0);return '<section class="cc-week-day"><header><span>'+safe(new Intl.DateTimeFormat(localeTag(),{weekday:"short",timeZone:"UTC"}).format(new Date(date+"T12:00:00Z")))+'</span><b>'+Number(date.slice(-2))+'</b><small>'+safe(minutes(total))+'</small></header>'+items.map(g=>'<button class="cc-week-task '+(g.key===weekKey?"is-on":"")+'" data-action="week-task" data-key="'+safe(g.key)+'"><strong>'+safe(g.title)+'</strong><span>'+g.cubes.length+" "+safe(t("куб."))+" · "+safe(minutes(g.cubes.reduce((sum,c)=>sum+c.minutes,0)))+'</span><small>'+safe(g.key===weekKey?t("Настраиваете"):t("Настроить кубики"))+'</small></button>').join("")+'</section>';}).join("")+'</div></div>'+(chosen?'<section class="cc-week-workspace" data-testid="weekly-workspace"><header><h2>'+safe(t("Кубики для"))+" "+safe(chosen.title)+'</h2><p>'+safe(dateLabel(chosen.date,true))+' · '+safe(t("Появятся в возможных этой даты"))+'</p><button data-action="preview-date" data-date="'+chosen.date+'">'+safe(t("Посмотреть в плане"))+" "+safe(dateLabel(chosen.date))+'</button></header><div class="cc-week-tools"><button data-action="week-add">'+safe(t("Добавить кубик"))+' · +60 '+safe(t("мин"))+'</button><button data-action="week-split">'+safe(t("Разделить кубик"))+' · '+safe(t("сумма прежняя"))+'</button></div><div class="cc-tray cc-week-tray" data-physical="week" data-testid="weekly-tray">'+chosen.cubes.map(c=>cubeHtml(decorated(c),"week","week")).join("")+'</div></section>':"")+'</section>';
  }
  let dashboardRead=0;
  async function refreshDashboard(){
    const read=++dashboardRead;
    try{const next=await ctx.readDashboard?.()||{};if(disposed||read!==dashboardRead)return;dashboard=next;dashboardError="";}
    catch(cause){if(disposed||read!==dashboardRead)return;dashboardError=t("Предложения Brainalot недоступны: {error}",{error:cause.message});}
    render();
  }
  async function refreshPending(){pending=await ctx.pendingDraft?.().catch(()=>null);render();}
  function commit(action,data,{quiet=false}={}){
    const operation=serial.catch(()=>{}).then(async()=>{
      if(disposed)return;if(await ctx.pendingDraft?.())throw new Error(t("Сначала разрешите неподтверждённый запрос Work."));
      const payload=typeof data==='function'?data():data;
      requireMutableCube(payload?.cube_id?(find(payload.cube_id)||{id:payload.cube_id}):null);
      busy++;error="";if(!quiet)notice=t("Сохраняем в Work…");
      try{const response=await ctx.action(action,payload);if(!quiet)notice=t("Изменение сохранено в Work");return response;}
      catch(cause){error=cause.status===409?t("Данные изменились. Запрос сохранён для безопасного повтора."):t("Не удалось сохранить: {error}",{error:cause.message});throw cause;}
      finally{busy--;pending=await ctx.pendingDraft?.().catch(()=>null);if(!quiet)render();}
    });serial=operation;return operation;
  }
  async function persistCube(cube,{quiet=false}={}){
    if(popup?.tray!=="week"&&!model().initialized)await ensureDay();
    cube={...cube,kind:cube.kind||(cube.task_id?"task":cube.kind)};
    if(!planning()?.constructor?.cubes?.[cube.id]&&!cube.planned_date)cube.planned_date=selectedDate;
    const data={cube_id:cube.id,...cubeCommand(cube)},resource=resourcesForSave(cube);
    Object.assign(data,resource);return commit("constructor_cube",data,{quiet});
  }
  function getPopupCube(){return popup?find(popup.id)||drafts.get(popup.id):null;}
  function openPopup(id,element,tray){
    if(Date.now()<suppressClick)return;hideTooltip();const c=find(id);if(!c)return;const existing=drafts.get(id);closePopup();const r=element.getBoundingClientRect();
    deleteIntent=null;popup={id,tray,anchor:{left:r.left,right:r.right,top:r.top,bottom:r.bottom}};if(!dirtyFields.get(id)?.size)popupBases.set(id,copy(c));drafts.set(id,existing||{...copy(c),...resourcesForEdit(c)});renderPopup();document.querySelector("[data-constructor-popup] h2")?.focus({preventScroll:true});
  }
  function closePopup(save=true){if(save&&popup&&!popupDeleted()&&!deletionFrozen()&&!deleteBusy&&drafts.has(popup.id))void savePopup().catch(()=>{});popupObserver?.disconnect();popupObserver=null;cancelAnimationFrame(popupFrame);popupFrame=null;document.querySelector("[data-constructor-popup]")?.remove();popup=null;deleteIntent=null;}
  function canDelete(c){return !['history','trophy'].includes(popup?.tray)&&['task','brainalot'].includes(c.kind||'task')&&!isDeletedCube(planning(),c);}
  function deleteControls(c){
    if(!canDelete(c))return '';
    const disabled=deleteBusy||poolPinned()||deletionFrozen();
    if(deleteIntent?.id!==c.id)return '<div class="cc-popup-actions"><button data-action="delete-ask" '+(disabled?'disabled':'')+'>'+safe(t('Удалить кубик'))+'</button></div>';
    const text=deleteIntent.noteId?t('Удалить задание Brainalot «{title}» и его кубик?',{title:deleteIntent.title}):t('Удалить кубик этапа «{title}»? Задача «{task}» и остальные этапы сохранятся.',{title:deleteIntent.title,task:c.taskTitle||t('Работа')});
    return '<section class="cc-warning" role="group" aria-label="'+safe(t('Подтверждение удаления'))+'"><p>'+safe(text)+'</p><div class="cc-popup-actions"><button data-action="delete-confirm" '+(disabled?'disabled':'')+'>'+safe(deleteBusy?t('Удаляем…'):t('Удалить'))+'</button><button data-action="delete-cancel" '+(deleteBusy?'disabled':'')+'>'+safe(t('Отмена'))+'</button></div></section>';
  }
  async function askDelete(){
    requirePoolEditing();const c=getPopupCube();requireMutableCube(c);if(!c||!canDelete(c)||deleteBusy)return;
    let note=null;if(c.kind==='brainalot'){await refreshDashboard();if(dashboardError)throw new Error(dashboardError);note=sourceNote(c,dashboard);if(!note?.version)throw new Error(t('Исходная задача недоступна. Обновите предложения Brainalot.'));}
    if(!popup||popup.id!==c.id)return;
    deleteIntent={id:c.id,noteId:note?.id||null,noteVersion:note?.version,title:note?.title||c.title};renderPopup();
  }
  async function deleteConfirmed(){
    requirePoolEditing();const intent=deleteIntent,c=getPopupCube();requireMutableCube(c);if(!intent||intent.id!==c?.id||deleteBusy)return;
    deleteBusy=true;render();
    try{
      await confirmedCubeDelete({cubeId:intent.id,noteVersion:intent.noteVersion,saveDraft:()=>savePopup(),readCurrent:()=>{
        const current=find(intent.id);return current&&!isDeletedCube(planning(),current)?current:null;
      },stopAndMeasure:async cube=>{
        if(canonical(cube.id)||cubeTimerState(cube).status!=='idle'||knownTimerIds.has(cube.id))await stopAndMeasure(cube);
      },commit:(name,data)=>commit(name,data,{quiet:true})});
      drafts.delete(intent.id);dirtyFields.delete(intent.id);popupBases.delete(intent.id);popupConflicts.delete(intent.id);
      if(popup?.id===intent.id)closePopup(false);notice=t('Кубик удалён. Сохранённые факты остались в истории.');error='';await refreshDashboard();
    }catch(cause){
      if(cause.status===409){deleteIntent=null;if(intent.noteId)await refreshDashboard();}
      throw cause;
    }finally{deleteBusy=false;if(!disposed)render();}
  }
  function popupHtml(c){
    if(popup.tray==="history")return historyPopupHtml(c);const selected=isSelected(c.id),week=popup.tray==="week",type=c.type||typeOf(c),locked=["calendar","weekly_review"].includes(c.kind),min=c.kind==="prep"?0:5,max=c.kind==="prep"?120:240,timer=cubeTimerState(c),factHint=canRecordFacts(c)?"":' title="'+safe(factGuardReason())+'"',factDisabled=canRecordFacts(c)?"":' disabled'+factHint;
    return '<header><span>'+safe(TYPES[type]||TYPES.task)+" · "+safe(week?t("Подготовка недели"):selected?t("Выбран в день"):t("Возможный кубик"))+'</span><button data-action="popup-close" aria-label="'+safe(t("Закрыть"))+'">'+ic("close")+'</button></header><h2 tabindex="-1">'+safe(c.title)+'</h2><p class="cc-popup-context">'+safe(c.taskTitle||c.sourceLabel||"")+(c.segmentTitle?" → "+safe(c.segmentTitle):"")+'</p>'+importanceControl(c)+'<label class="cc-range">'+safe(t("Длительность"))+'<b data-popup-duration>'+safe(minutes(c.minutes))+'</b><input type="range" data-popup-field="minutes" min="'+min+'" max="'+max+'" step="5" value="'+c.minutes+'" '+(locked?"disabled":"")+' aria-label="'+safe(t("Длительность кубика"))+'"></label><label class="cc-range">'+safe(t("Готовность"))+'<b data-popup-percent>'+formatReadiness(c.progress_percent)+'%</b><input type="range" data-popup-field="progress_percent" min="0" max="100" step="5" value="'+(c.progress_percent||0)+'" aria-label="'+safe(t("Готовность кубика"))+'"'+factDisabled+'></label><label class="cc-actual"'+factHint+'>'+safe(t("Факт, мин"))+'<input type="number" data-popup-field="actual_minutes" min="0" max="100000" value="'+Math.round(c.actual_minutes||0)+'"'+factDisabled+'></label>'+
      (week?'<label>'+safe(t("Дата подготовки"))+'<input type="date" data-popup-field="planned_date" value="'+safe(c.planned_date)+'"></label><p>'+safe(t("Появится в возможных"))+" "+safe(dateLabel(c.planned_date,true))+'</p>':"")+
      (selected?'<section class="cc-popup-timer"><div><b data-timer>'+formatTime(timer.remaining_ms)+'</b><span data-timer-status>'+safe(timer.status)+'</span></div><div><button data-action="timer-start"'+factDisabled+'>'+safe(t("Приступить"))+'</button><button data-action="timer-pause">'+safe(t("Пауза"))+'</button><button data-action="timer-stop">'+safe(t("Стоп"))+'</button><button data-action="done"'+factDisabled+'>'+safe(t("Готово"))+'</button></div><small>'+safe(t("Время не меняет готовность. Следующий кубик запускается вручную."))+'</small></section>':"")+
      '<div class="cc-popup-actions">'+(week?'<button class="cc-primary" data-action="preview-date" data-date="'+safe(c.planned_date)+'">'+safe(t("Посмотреть в плане"))+" "+safe(dateLabel(c.planned_date))+'</button>':selected?'<button data-action="return" data-id="'+safe(c.id)+'">'+safe(t("В возможные"))+'</button><button data-action="'+(c.start==null?"on-timeline":"float")+'" data-id="'+safe(c.id)+'">'+safe(c.start==null?t("На шкалу"):t("В россыпь"))+'</button>':'<button class="cc-primary" data-action="select" data-id="'+safe(c.id)+'" '+(c.allocation_stale?'disabled':'')+'>'+safe(t("В день"))+'</button>')+(!week?'<button data-action="hide" data-id="'+safe(c.id)+'">'+safe(t("Не сегодня"))+'</button>':"")+'</div>'+resourcesHtml(c)+
      '<details class="cc-popup-details" data-fold="details"><summary>'+safe(t("Подробности"))+'</summary><label>'+safe(t("Название"))+'<input data-popup-field="title" maxlength="300" value="'+safe(c.title)+'"></label><label>'+safe(t("Шаги"))+'<textarea data-popup-field="steps" rows="2">'+safe((c.steps||[]).join("\n"))+'</textarea></label><label>'+safe(t("Ожидаемый результат"))+'<textarea data-popup-field="output" rows="2">'+safe(c.output)+'</textarea></label><label>'+safe(t("Комментарий агенту"))+'<textarea data-popup-field="comment" rows="2">'+safe(c.comment)+'</textarea></label></details><p class="cc-popup-save" data-popup-save>'+safe(popupConflicts.has(c.id)?t("Поле изменилось в другой вкладке. Черновик сохранён — выберите нужную версию."):t("Изменения сохраняются в Work"))+'</p>'+(popupConflicts.has(c.id)?'<div class="cc-popup-actions"><button data-action="popup-latest">'+safe(t("Взять актуальную версию"))+'</button><button data-action="popup-overwrite">'+safe(t("Сохранить мой вариант"))+'</button></div>':'')+deleteControls(c);
  }
  function resourcesHtml(c){
    const list=c.links||[],groups=c.groups||[],count=list.length+groups.reduce((n,g)=>n+(g.links||[]).length,0);
    const linkRow=(link,group="")=>'<div class="cc-resource-link" data-link-row><input data-link-field="title" data-link="'+safe(link.id)+'" data-group="'+safe(group)+'" value="'+safe(link.title)+'" placeholder="'+safe(t("Название материала"))+'"><input data-link-field="url" data-link="'+safe(link.id)+'" data-group="'+safe(group)+'" value="'+safe(link.url)+'" placeholder="https://…"><div><a data-resource-anchor '+(validUrl(link.url)?'href="'+safe(validUrl(link.url))+'" target="_blank" rel="noopener noreferrer"':'aria-disabled="true"')+'>'+safe(t("Открыть"))+'</a><button data-action="link-remove" data-link="'+safe(link.id)+'" data-group="'+safe(group)+'">'+safe(t("Удалить ссылку"))+'</button></div><small data-link-error>'+(validUrl(link.url)?"":safe(t("Нужен полный http:// или https:// URL с адресом сайта.")))+'</small></div>';
    return '<details class="cc-popup-materials" data-fold="materials"><summary>'+safe(t("Материалы"))+" · "+count+'</summary><p>'+safe(t("Группы откроются в Chrome через подключённое расширение."))+'</p><div class="cc-resource-tools"><button data-action="link-add">'+safe(t("Добавить ссылку"))+'</button><button data-action="group-add">'+safe(t("Добавить группу"))+'</button><button data-action="resources-open">'+safe(t("Открыть все"))+" · "+count+'</button></div>'+list.map(l=>linkRow(l)).join("")+groups.map(g=>'<section class="cc-resource-group"><div><input data-group-title="'+safe(g.id)+'" value="'+safe(g.title)+'"><button data-action="group-remove" data-group="'+safe(g.id)+'">'+safe(t("Удалить группу"))+'</button></div><div><button data-action="link-add" data-group="'+safe(g.id)+'">'+safe(t("Добавить ссылку"))+'</button><button data-action="resources-open" data-group="'+safe(g.id)+'">'+safe(t("Открыть группу"))+" · "+(g.links||[]).length+'</button></div>'+(g.links||[]).map(l=>linkRow(l,g.id)).join("")+'</section>').join("")+'</details>';
  }
  function historyPopupHtml(c){
    const groups=launchGroups(c),done=Number(c.progress_percent)>=100;
    return '<header><span>'+safe(t("История кубика"))+'</span><button data-action="popup-close" aria-label="'+safe(t("Закрыть"))+'">'+ic("close")+'</button></header><h2 tabindex="-1">'+safe(c.title)+'</h2><p>'+safe(c.taskTitle||"")+'</p><div class="cc-history-facts"><b>'+formatReadiness(c.progress_percent)+'% · '+safe(done?t("Выполнен"):t("Не выполнен"))+'</b><span>'+safe(t("План"))+" "+safe(minutes(c.minutes))+' · '+safe(t("Факт"))+" "+safe(minutes(c.actual_minutes||0))+'</span></div><p>'+safe(c.output||"")+'</p>'+(c.steps?.length?'<ol>'+c.steps.map(step=>'<li>'+safe(step)+'</li>').join("")+'</ol>':"")+(c.comment?'<p>'+safe(c.comment)+'</p>':"")+'<details class="cc-popup-materials" data-fold="materials"><summary>'+safe(t("Материалы"))+'</summary>'+groups.map(g=>'<section class="cc-resource-group"><b>'+safe(g.title)+'</b><button data-action="resources-open" data-group="'+safe(g.id)+'">'+safe(t("Открыть группу"))+'</button>'+g.links.map(l=>'<a href="'+safe(l.url)+'" target="_blank" rel="noopener noreferrer">'+safe(l.title||l.url)+'</a>').join("")+'</section>').join("")+'</details>';
  }
  function positionPopup(){
    const el=document.querySelector("[data-constructor-popup]");if(!el||!popup)return;const width=Math.min(332,innerWidth-24);el.style.width=width+"px";el.style.maxHeight=Math.max(80,innerHeight-24)+"px";const a=popup.anchor,left=a.right+12+width<=innerWidth?a.right+12:a.left-width-12;
    el.style.left=clamp(left,12,innerWidth-width-12)+"px";el.style.top=clamp(a.top,12,innerHeight-el.offsetHeight-12)+"px";
  }
  function schedulePopupPosition(){if(popupFrame)return;popupFrame=requestAnimationFrame(()=>{popupFrame=null;positionPopup();});}
  function renderPopup(){
    const c=getPopupCube();if(!c){closePopup(false);return;}const old=document.querySelector("[data-constructor-popup]"),active=document.activeElement,focus=old?.contains(active)?{...active.dataset}:null,start=active?.selectionStart,end=active?.selectionEnd,scroll=old?.scrollTop||0,folds=Object.fromEntries([...(old?.querySelectorAll("[data-fold]")||[])].map(el=>[el.dataset.fold,el.open]));
    old?.remove();const el=document.createElement("aside");el.className="cc-popup work-constructor";el.dataset.constructorPopup="";el.dataset.testid="cube-popup";el.setAttribute("role","dialog");el.setAttribute("aria-label",c.title);el.innerHTML=(popupDeleted()?'<p class="cc-warning" role="status">'+safe(deletedMessage())+'</p>':'')+popupHtml(c);document.body.append(el);
    if(popupDeleted()||deletionFrozen()||deleteBusy)for(const control of el.querySelectorAll('input,textarea,select,button')){
      if(['popup-close','deletion-check','delete-cancel'].includes(control.dataset?.action)&&!deleteBusy)continue;
      if(control.matches?.('textarea,input[type=text],input:not([type])'))control.readOnly=true;else control.disabled=true;
    }
    for(const fold of el.querySelectorAll("[data-fold]"))fold.open=!!folds[fold.dataset.fold];
    if(focus){const target=[...el.querySelectorAll("input,textarea,select")].find(n=>Object.entries(focus).every(([key,value])=>n.dataset[key]===value));target?.focus({preventScroll:true});try{if(typeof start==="number")target?.setSelectionRange(start,end);}catch{}}
    el.scrollTop=scroll;popupObserver?.disconnect();if(typeof ResizeObserver!=="undefined"){popupObserver=new ResizeObserver(schedulePopupPosition);popupObserver.observe(el);}positionPopup();updateTimers();
  }
  function savePopup(){
    clearTimeout(saveTimer);if(!popup)return Promise.resolve();const {id,tray}=popup;
    const operation=enqueuePopupSave(id,()=>savePopupNow(id,tray));popupSaves.add(operation);operation.then(()=>popupSaves.delete(operation),()=>popupSaves.delete(operation));return operation;
  }
  async function savePopupNow(id,tray){
    if(disposed)return;const draft=drafts.get(id);if(!draft||!dirtyFields.get(id)?.size)return;
    const dirty=new Set(dirtyFields.get(id)||[]);
    // A refreshed Day projection can revoke factual identity after a draft was
    // opened. Drop only those stale fact fields; metadata remains editable.
    if(!canRecordFacts(canonical(id)||find(id)))for(const key of ["progress_percent","actual_minutes"]){dirty.delete(key);dirtyFields.get(id)?.delete(key);delete draft[key];}
    if(!dirty.size){drafts.delete(id);dirtyFields.delete(id);return;}
    const sent=JSON.stringify(draft),c={...find(id),...copy(draft)};let celebration=null;
    requireMutableCube(c);
    try{
      if(tray!=="week"&&!model().initialized)await ensureDay();
      const base=popupBases.get(id)||copy(c),metadataDirty=[...dirty].some(key=>fields.includes(key)||key==="resources");
      if(metadataDirty||!planning()?.constructor?.cubes?.[id])await commit("constructor_cube",()=>{
        const latest=canonical(id);
        if(planning()?.constructor?.cubes?.[id])return constructorPatch(base,c,latest,dirty);
        const create={cube_id:id,...cubeCommand({...c,kind:c.kind||(c.task_id?"task":c.kind),planned_date:c.planned_date||selectedDate}),...resourcesForSave(c)};return create;
      },{quiet:true});
      const report=canonical(id);
      if(dirty.has("progress_percent")||dirty.has("actual_minutes")){
        requireRecordableCube(c);
        const percent=dirty.has("progress_percent")?Math.round(clamp(c.progress_percent,0,100)):Number(report?.progress_percent)||0;
        let actual=dirty.has("actual_minutes")?Number(c.actual_minutes)||0:Number(report?.actual_minutes)||0;celebration=completionClaims.reserve(id,report,{progress_percent:percent});const celebrate=!!celebration,origin=celebrate?cubeElement(id)?.getBoundingClientRect():null;if(celebrate)pendingBlocks.add(id);
        if(percent>=100)actual=Math.max(actual,await measureStoppedCube(c));
        await commit("cube_progress",()=>{requireRecordableCube(c);const latest=canonical(id);constructorPatch(base,c,latest,new Set([...dirty].filter(key=>["progress_percent","actual_minutes"].includes(key))));const latestPercent=dirty.has("progress_percent")?percent:Number(latest?.progress_percent)||0,latestActual=dirty.has("actual_minutes")?Number(c.actual_minutes)||0:Number(latest?.actual_minutes)||0;return {cube_id:id,percent:latestPercent,actual_minutes:Math.round(latestPercent>=100?Math.max(actual,latestActual):latestActual)};},{quiet:true});if(celebrate)completionFlight(c,origin,celebration);
      }
      popupConflicts.delete(id);const updated=canonical(id);if(updated){const nextBase=copy(popupBases.get(id)||updated);for(const key of dirty){if(key==="resources")Object.assign(nextBase,{links:copy(updated.links||[]),groups:copy(updated.groups||[]),resource_drafts:copy(updated.resource_drafts||{links:updated.links||[],groups:updated.groups||[]})});else nextBase[key]=copy(updated[key]);}if(dirty.has("progress_percent")&&Number(c.progress_percent)>=100)nextBase.actual_minutes=updated.actual_minutes;popupBases.set(id,nextBase);}if(JSON.stringify(drafts.get(id))===sent){drafts.delete(id);dirtyFields.delete(id);}
      const label=popup?.id===id?document.querySelector("[data-popup-save]"):null;if(label)label.textContent=t("Сохранено в Work");geometries.delete(id);
    }catch(cause){if(celebration&&completionClaims.release(id,celebration))pendingBlocks.delete(id);if(cause.code==="constructor-edit-conflict"){popupConflicts.set(id,cause.fields||[]);if(popup?.id===id)renderPopup();throw cause;}const saved=canonical(id);if(saved&&drafts.has(id)){drafts.get(id).minutes=saved.minutes;drafts.get(id).planned_date=saved.planned_date;updateGeometry(find(id));}const label=document.querySelector("[data-popup-save]");if(label)label.textContent=error||cause.message;throw cause;}
  }
  function scheduleSave(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>void savePopup().catch(()=>{}),500);}
  function updateGeometry(c){
    for(const el of document.querySelectorAll("[data-ccube]")){
      if(el.dataset.ccube!==c.id)continue;
      if(el.dataset.tray==="timeline"){const selected=model().selected.find(x=>x.id===c.id),start=c.start??selected?.start;if(start!=null){el.style.left=(clock(start)-START)/(END-START)*100+"%";el.style.width=c.minutes/(END-START)*100+"%";const detail=el.querySelector("small");if(detail)detail.textContent=clock(clock(start))+" · "+minutes(c.minutes);}continue;}
      if(el.matches('.rc-source-open')){el.innerHTML=sourceCardContents(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,selected:isSelected(c.id)});continue;}
      const size=cubeDimensions(c.minutes,el.dataset.tray==="week"?"week":"day");el.style.width=size.width+"px";el.style.height=size.height+"px";el.dataset.minutes=c.minutes;const art=el.matches(".cc-cube");if(art)el.innerHTML=readableCardContents(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,duration:minutes(compactCardMinutes(c))});const badge=el.querySelector("[data-duration]");if(badge)badge.textContent=c.minutes;physics.get(selectedDate+":"+el.dataset.tray)?.resize(c.id,size);physics.get("week:"+weekKey)?.resize(c.id,size);
    }
    const readout=document.querySelector("[data-popup-duration]");if(readout)readout.textContent=minutes(c.minutes);
    const orbit=$(".cc-orbit-host");if(orbit){const m=model();orbit.innerHTML=orbitHtml({...m,selected:m.selected.map(item=>item.id===c.id?{...item,...c}:item)});mountOrbitFeatures(orbit);}
  }
  async function measureStoppedCube(c){
    requireRecordableCube(c);await stopCubeTimer(c);const saved=Number(canonical(c.id)?.actual_minutes)||0,measured=await recordedCubeMinutes(c),actual=Math.max(saved,measured,Number(drafts.get(c.id)?.actual_minutes)||0);
    return actual;
  }
  async function stopAndMeasure(c){
    if(!canRecordFacts(c)){if(["running","paused"].includes(cubeTimerState(c).status))await stopCubeTimer(c);return;}
    const saved=Number(canonical(c.id)?.actual_minutes)||0,actual=await measureStoppedCube(c);
    if(actual>saved)await commit("cube_progress",{cube_id:c.id,percent:Number(c.progress_percent)||0,actual_minutes:actual},{quiet:true});
  }
  async function selectCube(id,start=null){
    requirePoolEditing();if(popup?.id===id)await savePopup();const c=find(id);if(!c||!writable())return;if(c.allocation_stale)throw new Error(t("Остаток изменился — настройте объём кубика."));if(model().summary.budget<=0)throw new Error(t("Сегодня 0 энергии. Измените энергию вручную, чтобы добавить работу."));
    const body={cube_id:id,date:selectedDate,...(start!=null?{start:clock(start)}:{}),...(!canonical(id)?{cube:{...cubeCommand(c),...resourcesForSave(c)}}:{})};
    await commit("constructor_select",body);return find(id);
  }
  async function returnCube(id,{composition=false}={}){
    if(!composition)requirePoolEditing();
    if(popup?.id===id)await savePopup();const c=find(id);if(!c)return;await stopAndMeasure(c);await commit("constructor_return",{cube_id:id});if(!composition){taskChoice().restoredCubeIds=[...new Set([...(taskChoice().restoredCubeIds||[]),id])];persistTaskChoice();}returned.set(selectedDate,[id,...(returned.get(selectedDate)||[]).filter(x=>x!==id)]);closePopup(false);render();
  }
  async function hideCube(id,target,origin){
    requirePoolEditing();
    if(popup?.id===id)await savePopup();const c=find(id);if(!c)return;lastUndo={date:selectedDate,id,selected:isSelected(id),start:c.start??null};
    if(isSelected(id))await stopAndMeasure(c);
    if(!canonical(id))await persistCube(c,{quiet:true});
    flyToBin(c,target,origin);await commit("constructor_hide",{cube_id:id,date:selectedDate,hidden:true});closePopup(false);render();
  }
  async function restoreCube(id){await commit("constructor_hide",{cube_id:id,date:selectedDate,hidden:false});taskChoice().restoredCubeIds=[...new Set([...(taskChoice().restoredCubeIds||[]),id])];persistTaskChoice();returned.set(selectedDate,[id,...(returned.get(selectedDate)||[]).filter(x=>x!==id)]);render();}
  async function addMain(taskId){
    const m=model(),task=planning()?.tasks?.find(x=>x.id===taskId),segment=planning()?.task_segments?.find(s=>s.id===stage),pool=m.pool.find(x=>x.task_id===taskId),required=hours*60;
    if(!task)return;if(m.summary.free<required)throw new Error(t("Не хватает энергии для выбранного объёма. Сначала освободите время или измените энергию."));
    const remaining=pool?.segments?.find(s=>s.id===stage)?.available_for_manual_minutes??pool?.available_for_manual_minutes??0;
    if(remaining<required)throw new Error(t("В выбранном этапе недостаточно свободного остатка."));
    for(let i=0;i<hours;i++)await commit("constructor_select",{cube_id:uid(),date:selectedDate,cube:{kind:"task",task_id:task.id,...(stage?{segment_id:stage}:{}),minutes:60,title:segment?.title||task.title,output:segment?.output||"",steps:segment?.steps||[],comment:""}});
  }
  async function ensureDay(){if(!model().initialized)await commit("constructor_day",{date:selectedDate});}
  function mountPhysics(){
    for(const el of root.querySelectorAll('[data-physical]')){
      if(el.clientWidth<=0)continue;
      const variant=el.dataset.physical==='week'?'week':'day',key=variant==='week'?'week:'+weekKey:selectedDate+':'+el.dataset.physical;
      const instance=createTraySlots(el,{variant});physics.set(key,instance);
      const panel=el.closest('.rc-selected');
      if(panel?.classList.contains('is-following')){
        const stickyTop=Number.parseFloat(getComputedStyle(panel).top);
        panel.classList.toggle('is-tall',shouldUnstickPanel(panel.getBoundingClientRect().height,window.innerHeight,stickyTop));
      }
    }
  }
  function bindWheel(){const strip=$(".cc-task-strip");strip?.addEventListener("wheel",event=>{if(Math.abs(event.deltaX)>Math.abs(event.deltaY)||strip.scrollWidth<=strip.clientWidth)return;const next=clamp(strip.scrollLeft+event.deltaY,0,strip.scrollWidth-strip.clientWidth);if(next!==strip.scrollLeft){event.preventDefault();strip.scrollLeft=next;}},{passive:false});}
  const snapSlot=(id,duration,desired)=>nearestPlanningSlot(planning(),occupied(id),duration,desired);
  function occupied(exclude){const m=model();return [...m.events.filter(e=>e.required).map(e=>({start:clock(e.start),minutes:clock(e.end)-clock(e.start)})),...m.selected.filter(c=>c.id!==exclude&&c.start!=null).map(c=>({start:clock(c.start),minutes:c.minutes}))];}
  function boundsHit(element,x,y,margin=0){if(!element)return false;const r=element.getBoundingClientRect();return x>=r.left-margin&&x<=r.right+margin&&y>=r.top-margin&&y<=r.bottom+margin;}
  function cubeElement(id){return [...document.querySelectorAll("[data-ccube]")].find(el=>el.dataset.ccube===id);}
  function playEffect(node,frames,options){
    if(disposed||matchMedia('(prefers-reduced-motion: reduce)').matches||!node.animate)return Promise.resolve();
    return new Promise(resolve=>{let done=false,motion,timer;const finish=()=>{if(done)return;done=true;clearTimeout(timer);activeEffects.delete(cancel);resolve();},cancel=()=>{motion?.cancel();finish();};activeEffects.add(cancel);try{motion=node.animate(frames,options);motion.finished.catch(()=>{}).finally(finish);timer=setTimeout(cancel,Number(options.duration||0)+250);}catch{finish();}});
  }
  function fly(c,origin,destination,{bin=false,suction=false,effect='flight',onFinish=()=>{}}={}){
    if(disposed||matchMedia('(prefers-reduced-motion: reduce)').matches||!destination){onFinish();return Promise.resolve();}
    const size=cubeDimensions(c.minutes),from=origin||cubeElement(c.id)?.getBoundingClientRect()||{left:destination.left,top:destination.top,width:size.width,height:size.height},node=document.createElement('div');node.className='cc-flight rc-card';node.dataset.layout=readableLayout();node.dataset.effect=effect;node.dataset.cubeId=c.id;node.innerHTML=readableCardContents(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,duration:minutes(compactCardMinutes(c))});document.body.append(node);
    if(effect==='tray-slot')return playEffect(node,[{transform:'translate('+from.left+'px,'+from.top+'px)',width:from.width+'px',height:from.height+'px',opacity:1},{transform:'translate('+destination.left+'px,'+destination.top+'px)',width:destination.width+'px',height:destination.height+'px',opacity:1}],{duration:320,easing:'cubic-bezier(.2,.75,.3,1)',fill:'forwards'}).finally(()=>{node.remove();onFinish();});
    const end=bin?{left:destination.left+destination.width/2,top:destination.top+destination.height/2,width:2,height:2}:suction?{left:destination.left+destination.width*.16,top:destination.top+destination.height*.16,width:destination.width*.68,height:destination.height*.68}:destination,dx=end.left-from.left,dy=end.top-from.top;
    return playEffect(node,[{transform:'translate('+from.left+'px,'+from.top+'px) rotate(0deg)',width:(from.width||size.width)+'px',height:(from.height||size.height)+'px',opacity:1},{transform:'translate('+(from.left+dx*.38-dy*.08)+'px,'+(from.top+dy*.38+dx*.08)+'px) rotate(-10deg)',width:(bin?size.width*.6:Math.max(90,from.width*.82))+'px',height:(bin?size.height*.6:Math.max(50,from.height*.82))+'px',offset:.55,opacity:1},{transform:'translate('+end.left+'px,'+end.top+'px) rotate('+(bin?23:0)+'deg)',width:end.width+'px',height:end.height+'px',opacity:bin?0:1}],{duration:suction?320:bin?440:540,easing:'cubic-bezier(.48,.03,.8,.68)',fill:'forwards'}).finally(()=>{node.remove();onFinish();});
  }
  function flyToBin(c,target,origin){const bin=target||$('[data-trash="'+(isSelected(c.id)?"selected":"possible")+'"]');fly(c,origin,bin?.getBoundingClientRect(),{bin:true});}
  async function placeOnTimeline(id,start,origin){
    const c=find(id);if(!c)return;pendingBlocks.add(id);closePopup();
    try{
      if(isSelected(id))await commit("constructor_timeline",{cube_id:id,date:selectedDate,start:clock(start)});
      else await selectCube(id,start);
      render();const final=[...root.querySelectorAll(".cc-timeline-cube")].find(el=>el.dataset.ccube===id);
      if(!final){pendingBlocks.delete(id);return;}
      fly(find(id)||c,origin,final.getBoundingClientRect(),{onFinish:()=>{pendingBlocks.delete(id);for(const node of root.querySelectorAll(".cc-timeline-cube"))if(node.dataset.ccube===id)node.style.visibility="";}});
    }catch(cause){pendingBlocks.delete(id);render();throw cause;}
  }
  function completionFlight(c,origin,celebration){
    const finished={...c,...canonical(c.id),progress_percent:100};render();const target=[...root.querySelectorAll('.cc-trophy')].find(el=>el.dataset.id===c.id),reveal=()=>{completionClaims.release(c.id,celebration);pendingBlocks.delete(c.id);for(const node of root.querySelectorAll('.cc-trophy'))if(node.dataset.id===c.id)node.style.visibility='';};
    if(!target||disposed||matchMedia('(prefers-reduced-motion: reduce)').matches){reveal();return;}
    const size=cubeDimensions(c.minutes),from=origin||{left:target.getBoundingClientRect().left,top:target.getBoundingClientRect().top,width:size.width,height:size.height},node=document.createElement('div'),color=COLORS[c.type||typeOf(c)]||COLORS.task;
    node.className='cc-completion-transform';node.dataset.cubeId=c.id;node.dataset.layout=readableLayout();Object.assign(node.style,{left:from.left+'px',top:from.top+'px',width:Math.max(38,from.width||size.width)+'px',height:Math.max(38,from.height||size.height)+'px'});
    node.innerHTML='<span class="cc-material-before rc-card">'+readableCardContents(c,{finished:false,typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,duration:minutes(compactCardMinutes(c))})+'</span><span class="cc-material-after rc-card">'+readableCardContents(finished,{finished:true,typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,duration:minutes(compactCardMinutes(finished,true))})+'</span>'+Array.from({length:5},(_,i)=>'<i class="cc-completion-spark" style="--spark-angle:'+(i*72-36)+'deg"></i>').join('');document.body.append(node);
    const before=node.querySelector('.cc-material-before'),after=node.querySelector('.cc-material-after'),sparks=[...node.querySelectorAll('.cc-completion-spark')];
    const jobs=[playEffect(node,[{transform:'scale(1)',filter:'brightness(1)'},{transform:'scale(1.14)',filter:'brightness(1.5)',offset:.48},{transform:'scale(1)',filter:'brightness(1)'}],{duration:280,easing:'ease-out',fill:'forwards'}),playEffect(before,[{opacity:1},{opacity:0}],{duration:220,fill:'forwards'}),playEffect(after,[{opacity:0},{opacity:1}],{duration:240,fill:'forwards'}),...sparks.map((spark,i)=>playEffect(spark,[{transform:'rotate('+(i*72-36)+'deg) translateY(-12px) scale(.5)',opacity:0},{opacity:1,offset:.2},{transform:'rotate('+(i*72-36)+'deg) translateY(-40px) scale(.1)',opacity:0}],{duration:280,easing:'ease-out',fill:'forwards'}))];
    void Promise.all(jobs).then(()=>{const transformed=node.getBoundingClientRect();node.remove();if(disposed){reveal();return;}const final=[...root.querySelectorAll('.cc-trophy')].find(el=>el.dataset.id===c.id);return fly(finished,transformed,final?.getBoundingClientRect(),{effect:'completion-flight',onFinish:reveal});}).catch(()=>{node.remove();reveal();});
  }
  async function weekAdd(){
    const group=weekGroups().find(g=>g.key===weekKey);if(!group?.task_id)throw new Error(t("Добавление доступно для задачи курса."));
    const task=planning().tasks.find(task=>task.id===group.task_id);await persistCube({id:uid(),kind:"task",task_id:task.id,title:task.title,minutes:60,planned_date:group.date,steps:[],output:"",comment:"",links:[],groups:[]});render();
  }
  async function weekSplit(){
    const group=weekGroups().find(g=>g.key===weekKey),c=group?.cubes.find(c=>c.kind==="task"&&c.minutes>=10&&!c.selected_date&&!c.progress_percent&&!c.actual_minutes);
    if(!c)throw new Error(t("Разделить можно только неначатый, не выбранный кубик от 10 минут."));
    if(!planning()?.constructor?.cubes?.[c.id])await persistCube(c,{quiet:true});await commit("constructor_split",{cube_id:c.id,minutes:Math.floor(c.minutes/10)*5,new_cube_id:uid()});
  }
  async function openResources(c,groupId,linkId){
    await savePopup();const saved=find(c.id)||c;let groups=launchGroups(saved,groupId);
    if(linkId)groups=groups.map(g=>({...g,links:g.links.filter(l=>l.id===linkId)})).filter(g=>g.links.length);
    if(!groups.length)throw new Error(t("Нет корректных ссылок. Нужен http:// или https:// с адресом сайта."));
    const item={id:"work-cube:"+c.id,title:c.title,groups:groups.map(g=>({...g,color:g.color||"cyan"}))},only=groupId||linkId?item.groups[0]:null,result=await (ctx.openCubeWorkspace||ctx.openWorkspace)?.(item,only);
    if(result?.ok===false)throw new Error(t("Браузер не открыл рабочее место."));
    if(result?.preview)toast(t("Браузер открыл только первую ссылку. Нативные группы доступны в расширении Chrome."));
  }
  async function action(button,event){
    const name=button.dataset.action,id=button.dataset.id||popup?.id,c=id?find(id):null;
    if((deleteBusy||deletionFrozen())&&!readAction(name))throw new Error(t('Удаление ещё выполняется. Другие изменения Work временно приостановлены.'));
    if(!readAction(name)&&c)requireMutableCube(c);
    if(name==='deletion-check'){await ctx.refreshWork?.();await refreshPending();await refreshDashboard();return;}
    if(name==='delete-ask')return askDelete();
    if(name==='delete-confirm')return deleteConfirmed();
    if(name==='delete-cancel'){deleteIntent=null;renderPopup();return;}
    if(["mode-day","mode-week","mode-month"].includes(name)){closePopup();mode=name==="mode-day"?"day":name==="mode-week"?"week":"month";render();return;}
    if(name==="previous"||name==="next"||name==="today"||name==="preview-date"){closePopup();selectedDate=name==="today"?todayIn(planning()?.settings?.timezone):name==="preview-date"?button.dataset.date:shiftDate(selectedDate,name==="previous"?-1:1);mode="day";error="";render();return;}
    if(name==="goal-close")return commit("constructor_goal_status",{scope:button.dataset.scope,date:button.dataset.goalDate,status:button.dataset.status});
    if(name==="initialize")return commit("constructor_day",{date:selectedDate});
    if(name==="goal-editor"){const key=button.dataset.scope+':'+button.dataset.goalDate;goalEditor=goalEditor===key?null:key;render();if(goalEditor)$('[data-goal-title="'+button.dataset.scope+'"]')?.focus({preventScroll:true});return;}
    if(name==="tasks-toggle"){taskChoice().collapsed=!taskChoice().collapsed;persistTaskChoice();render();return;}

    if(name==='month-plan-save')return saveMonthPlan();
    if(name==='pool-pin'){await taskChangeSerial;await serial;setPoolPinned(!poolPinned());return;}
    if(name==="main"){const operation=taskChangeSerial.catch(()=>{}).then(()=>toggleTask(button.dataset.task));taskChangeSerial=operation;return operation;}
    if(name==="stage"){stage=button.dataset.segment;render();return;}
    if(name==="hours"){hours=Number(button.dataset.hours);render();return;}
    if(name==="add-main")return addMain(button.dataset.task);
    if(name==="tasks-prev"||name==="tasks-next"){ $(".cc-task-strip")?.scrollBy({left:name==="tasks-prev"?-360:360,behavior:"smooth"});return;}
    if(name==="month-root"){historyPath=[];render();return;}if(name==="month-path"){historyPath=historyPath.slice(0,Number(button.dataset.index)+1);render();return;}if(name==="month-goal"){closePopup();mode="month";const goal=planning()?.result?.constructor?.goals?.find(goal=>goal.id===id);if(goal?.date)historyMonth=goal.date.slice(0,7);historyPath=[{goalId:id}];render();return;}if(name==="month-day"){historyPath.push({date:button.dataset.date,goalId:button.dataset.goalId});render();return;}if(name==="trophy-inspect"||name==="history-inspect"){openPopup(id,button,name==="history-inspect"?"history":"trophy");return;}
    if(name==="inspect"){openPopup(id,button,isSelected(id)?"selected":"possible");return;}
    if(name==="popup-latest"){drafts.delete(id);dirtyFields.delete(id);popupConflicts.delete(id);const current=canonical(id);popupBases.set(id,copy(current));drafts.set(id,{...copy(current),...resourcesForEdit(current)});error="";renderPopup();return;}
    if(name==="popup-overwrite"){popupBases.set(id,copy(canonical(id)));popupConflicts.delete(id);await savePopup();renderPopup();return;}
    if(name==="popup-close"){closePopup();return;}
    if(name==="select"){await selectCube(id);if(popup)popup.tray="selected";render();return;}
    if(name==="return"){await ensureDay();return returnCube(id);}
    if(name==="hide"){await ensureDay();return hideCube(id,null,cubeElement(id)?.getBoundingClientRect());}
    if(name==="restore")return restoreCube(id);
    if(name==="restore-latest"){const last=model().hidden.at(-1);if(last)return restoreCube(last.id);return;}
    if(name==="undo"&&lastUndo){const u=lastUndo;if(u.date!==selectedDate)return;lastUndo=null;await restoreCube(u.id);if(u.selected){await selectCube(u.id);if(u.start!=null)await commit("constructor_timeline",{cube_id:u.id,date:u.date,start:u.start});}render();return;}
    if(name==="clear"){await ensureDay();for(const item of [...model().selected])await returnCube(item.id);return;}
    if(name==="on-timeline"){const start=snapSlot(id,c.minutes,START);if(start===null)throw new Error(t("Нет свободного места нужной длительности."));return placeOnTimeline(id,start,cubeElement(id)?.getBoundingClientRect());}
    if(name==="float")return commit("constructor_timeline",{cube_id:id,date:selectedDate,start:null});
    if(name==="recalculate")return commit("recalculate",{from_date:selectedDate,refresh_calendar:false,reason:"constructor-user-request"});
    if(name==="refresh-dashboard")return refreshDashboard();
    if(name==="retry"){await ctx.retryDraft();await refreshPending();return;}
    if(name==="discard"){await ctx.discardDraft();await refreshPending();return;}
    if(name==="week-prev"||name==="week-next"){closePopup();weekStart=shiftDate(weekStart,name==="week-prev"?-7:7);weekKey=null;render();return;}
    if(name==="week-task"){weekKey=button.dataset.key;closePopup();render();$("[data-testid='weekly-workspace']")?.scrollIntoView({block:"nearest",behavior:"smooth"});return;}
    if(name==="week-add")return weekAdd();
    if(name==="week-split")return weekSplit();
    if(name.startsWith("timer-")||name==="done"){
      if(!c)return;if(name==="timer-start"||name==="done")requireRecordableCube(c);await savePopup();if(name==="timer-start"||name==="done")requireRecordableCube(c);
      if(name==="timer-start")await startCubeTimer(c);else if(name==="timer-pause")await pauseCubeTimer(c);else await stopAndMeasure(c);
      if(name==="done"&&Number(canonical(c.id)?.progress_percent)<100){const celebration=completionClaims.reserve(c.id,canonical(c.id),{progress_percent:100}),celebrate=!!celebration,origin=cubeElement(c.id)?.getBoundingClientRect();if(celebrate)pendingBlocks.add(c.id);try{await commit("cube_progress",{cube_id:c.id,percent:100,actual_minutes:Math.max(Number(c.actual_minutes)||0,await recordedCubeMinutes(c))});drafts.delete(c.id);if(celebrate)completionFlight(c,origin,celebration);}catch(cause){if(celebration&&completionClaims.release(c.id,celebration))pendingBlocks.delete(c.id);render();throw cause;}}updateTimers();if(popup)renderPopup();return;
    }
    if(["link-add","link-remove","group-add","group-remove"].includes(name)){
      const draft=drafts.get(popup.id)||{...copy(c),...resourcesForEdit(c)};draft.links??=[];draft.groups??=[];const group=draft.groups.find(g=>g.id===button.dataset.group),links=group?.links||draft.links;
      if(name==="link-add")links.push({id:uid(),title:"",url:""});if(name==="link-remove"){const index=links.findIndex(l=>l.id===button.dataset.link);if(index>=0)links.splice(index,1);}if(name==="group-add")draft.groups.push({id:uid(),title:t("Новая группа"),links:[]});if(name==="group-remove")draft.groups=draft.groups.filter(g=>g.id!==button.dataset.group);
      drafts.set(c.id,draft);const dirty=dirtyFields.get(c.id)||new Set();dirty.add("resources");dirtyFields.set(c.id,dirty);renderPopup();scheduleSave();return;
    }
    if(name==="resources-open")return openResources(c,button.dataset.group);
  }
  function handleClick(event){
    const host=event.target.closest("[data-constructor-popup]");if(!root.contains(event.target)&&!host)return;
    const anchor=event.target.closest("[data-resource-anchor]");
    if(anchor){event.preventDefault();const row=anchor.closest("[data-link-row]"),input=row?.querySelector('[data-link-field="url"]'),c=getPopupCube();if(!input||!validUrl(input.value)){toast(t("Нужен полный http:// или https:// URL с адресом сайта."));return;}const title=row.querySelector('[data-link-field="title"]')?.value||c.title;
      void (async()=>{await savePopup();const group={id:"single",title,links:[{title,url:validUrl(input.value)}],color:"cyan"};const result=await (ctx.openCubeWorkspace||ctx.openWorkspace)?.({id:"work-cube:"+c.id,title:c.title,groups:[group]},group);if(result?.ok===false)throw new Error(t("Браузер не открыл ссылку."));})().catch(reportError);return;
    }
    const button=event.target.closest("[data-action]");if(button){event.preventDefault();void trackOperation(Promise.resolve(action(button,event))).catch(reportError);return;}
    const cube=event.target.closest("[data-ccube]");if(cube&&Date.now()>=suppressClick)openPopup(cube.dataset.ccube,cube,cube.dataset.tray);
  }
  function reportError(cause){error=cause?.message||String(cause);toast(error);render();}
  function handleInput(event){
    const el=event.target;if(!root.contains(el)&&!el.closest("[data-constructor-popup]"))return;
    if(deletionFrozen()||deleteBusy||popupDeleted())return;
    if(el.dataset.field==="search"){search=el.value;render();return;}
    if(el.dataset.field==="energy"){const label=$("[data-energy]");if(label)label.textContent=el.value;return;}
    if(!popup)return;
    const c=getPopupCube();if(["progress_percent","actual_minutes"].includes(el.dataset.popupField)&&!canRecordFacts(c))return;const draft=drafts.get(c.id)||copy(c);drafts.set(c.id,draft);
    if(el.dataset.popupField){
      const field=el.dataset.popupField;const dirty=dirtyFields.get(c.id)||new Set();dirty.add(field);dirtyFields.set(c.id,dirty);
      draft[field]=["minutes","actual_minutes","progress_percent"].includes(field)?Math.max(0,Number(el.value)||0):field==="steps"?el.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean):el.value;
      if(field==="minutes"){geometries.set(c.id,draft.minutes);updateGeometry(find(c.id));const label=document.querySelector("[data-popup-duration]");if(label)label.textContent=minutes(draft.minutes);}
      if(field==="progress_percent"){const label=document.querySelector("[data-popup-percent]");if(label)label.textContent=formatReadiness(draft.progress_percent)+"%";}
      if(field!=="minutes"&&field!=="progress_percent"&&field!=="planned_date")scheduleSave();
    }else if(el.dataset.groupTitle){const dirty=dirtyFields.get(c.id)||new Set();dirty.add("resources");dirtyFields.set(c.id,dirty);const group=draft.groups?.find(g=>g.id===el.dataset.groupTitle);if(group)group.title=el.value;scheduleSave();}
    else if(el.dataset.linkField){const dirty=dirtyFields.get(c.id)||new Set();dirty.add("resources");dirtyFields.set(c.id,dirty);const group=draft.groups?.find(g=>g.id===el.dataset.group),list=group?.links||draft.links,link=list?.find(l=>l.id===el.dataset.link);if(link)link[el.dataset.linkField]=el.value;
      const row=el.closest("[data-link-row]"),valid=validUrl(link?.url),anchor=row?.querySelector("[data-resource-anchor]"),label=row?.querySelector("[data-link-error]");if(anchor){if(valid){anchor.href=valid;anchor.removeAttribute("aria-disabled");}else{anchor.removeAttribute("href");anchor.setAttribute("aria-disabled","true");}}if(label)label.textContent=valid?"":t("Нужен полный http:// или https:// URL с адресом сайта.");scheduleSave();
    }
  }
  function handleChange(event){
    const el=event.target;if(!root.contains(el)&&!el.closest("[data-constructor-popup]"))return;
    if((deletionFrozen()||deleteBusy||popupDeleted())&&!['date','history-month','search'].includes(el.dataset.field))return;
    if(el.dataset.field==='month-plan-month'){const draft=monthPlanDraft(),plan=approvedMonthlyPlan(planning());draft.month=el.value;draft.taskIds=plan?.month===el.value?[...plan.task_ids]:[];render();return;}
    if(el.dataset.field==='month-plan-task'){const draft=monthPlanDraft();draft.taskIds=el.checked?[...new Set([...draft.taskIds,el.dataset.task])]:draft.taskIds.filter(id=>id!==el.dataset.task);render();return;}
    if(el.dataset.field==='source-importance'){const c=find(el.dataset.id)||historyCubes(planning(),{includeArchived:true}).find(cube=>cube.id===el.dataset.id);if(c)void changeSourceImportance(c,Number(el.value)).catch(reportError);return;}
    if(el.dataset.field==='source-task'){const operation=taskChangeSerial.catch(()=>{}).then(()=>toggleTask(el.dataset.task));taskChangeSerial=operation;void operation.catch(reportError);return;}
    if(el.dataset.field==='task-focus'){taskChoice().focusTask=el.value;stage=null;render();return;}
    if(el.dataset.goalTitle){const scope=el.dataset.goalTitle;void commit("constructor_goal",{scope,date:el.dataset.goalDate,title:el.value}).catch(reportError);return;}
    if(el.dataset.field==="history-month"){historyMonth=el.value;historyPath=[];render();return;}if(el.dataset.field==="date"){closePopup();selectedDate=el.value;render();return;}
    if(el.dataset.field==="energy"){void (async()=>{await ensureDay();await commit("day_budget",{date:selectedDate,minutes:Math.round(Number(el.value)*60)});})().catch(reportError);return;}
    if(el.dataset.field==="goal"){void commit("constructor_day",{date:selectedDate,goal:el.value}).catch(reportError);return;}
    if(el.dataset.field==="event-required"){void commit("calendar_required",{event_id:el.dataset.event,required:el.checked}).catch(reportError);return;}
    if(el.dataset.popupField){void savePopup().then(()=>render()).catch(()=>{});}
  }
  function cleanupDrag(){const orbit=$(".cc-orbit-host");if(orbit&&!disposed){orbit.innerHTML=orbitHtml(model());mountOrbitFeatures(orbit);}if(drag?.source){drag.source.style.opacity="";drag.source.classList.remove("is-resizing");}drag?.ghost?.remove();drag=null;$(".cc-snap")?.remove();for(const el of root.querySelectorAll(".is-drop"))el.classList.remove("is-drop");}
  function pointerDown(event){
    if(deletionFrozen()||deleteBusy)return;
    if(event.button!==0||!root.contains(event.target))return;const element=event.target.closest("[data-ccube]");if(!element||element.dataset.tray==="week"||!writable())return;
    const c=find(element.dataset.ccube);if(!c||Number(c.progress_percent)>=100||poolPinned()&&!isSelected(c.id))return;
    if(event.target.closest("[data-timeline-resize]")){
      if(!model().initialized||c.start==null||Number(c.progress_percent)||Number(c.actual_minutes)||['calendar','weekly_review'].includes(c.kind))return;
      event.preventDefault();closePopup();hideTooltip();const timeline=$(".cc-timeline");if(!timeline)return;
      drag={type:"resize",id:c.id,c:copy(c),base:copy(canonical(c.id)||c),source:element,pointer:event.pointerId,x:event.clientX,y:event.clientY,active:false,minutes:c.minutes,pixelsPerMinute:timeline.getBoundingClientRect().width/(END-START)};return;
    }
    drag={id:c.id,c,source:element,tray:element.dataset.tray,x:event.clientX,y:event.clientY,active:false,pointer:event.pointerId,snap:null};
  }
  function orbitDropAt(x,y){
    const svg=$('.cc-orbit-host svg');
    return svg?orbitDropMinute(svg.getBoundingClientRect(),x,y):null;
  }
  function orbitDropHint(host,label,valid){
    const hint=document.createElement('div');hint.className='cc-orbit-drop-hint'+(valid?'':' is-invalid');hint.textContent=label;host.append(hint);
  }
  function pointerMove(event){
    lastHoverPoint={x:event.clientX,y:event.clientY};
    if(!drag||event.pointerId!==drag.pointer){hoverTooltip(event);return;}
    if(drag.type==="resize"){
      if(!drag.active&&Math.abs(event.clientX-drag.x)<2)return;event.preventDefault();drag.active=true;
      const value=timelineResizeMinutes(planning(),occupied(drag.id),clock(drag.c.start),drag.c.minutes+(event.clientX-drag.x)/drag.pixelsPerMinute,drag.c.kind==="prep"?120:240);
      if(value!==null){drag.minutes=value;drag.source.classList.add("is-resizing");updateGeometry({...drag.c,minutes:value});}return;
    }
    if(!drag.active&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<5)return;
    if(!drag.active){
      drag.active=true;closePopup();hideTooltip();clearTimelineInfo();for(const item of physics.values())item.pause();const ghost=document.createElement("div"),size=cubeDimensions(drag.c.minutes);ghost.className="cc-drag-ghost rc-card";ghost.dataset.layout=readableLayout();ghost.style.width=size.width+"px";ghost.style.height=size.height+"px";ghost.innerHTML=readableCardContents(drag.c,{typeLabel:TYPES[drag.c.type]||TYPES.task,duration:minutes(compactCardMinutes(drag.c))});document.body.append(ghost);drag.ghost=ghost;drag.source.style.opacity=".2";
    }
    event.preventDefault();const size=cubeDimensions(drag.c.minutes),timeline=$(".cc-timeline"),near=boundsHit(timeline,event.clientX,event.clientY,45),ringMinute=near?null:orbitDropAt(event.clientX,event.clientY);drag.ghost.style.left=event.clientX-size.width/2+"px";drag.ghost.style.top=event.clientY-size.height/2+"px";drag.ghost.style.transform="";drag.snap=null;$(".cc-snap")?.remove();
    if(near){const bounds=timeline.getBoundingClientRect(),desired=START+(event.clientX-bounds.left)/bounds.width*(END-START)-drag.c.minutes/2,start=snapSlot(drag.id,drag.c.minutes,desired),snap=document.createElement("div");snap.className="cc-snap";snap.textContent=start===null?t("Нет свободного места"):clock(start);snap.style.left=(start===null?40:(start-START)/(END-START)*100)+"%";snap.style.width=(start===null?20:drag.c.minutes/(END-START)*100)+"%";timeline.append(snap);drag.snap=start;
      if(start!==null){const orbit=$(".cc-orbit-host");if(orbit){orbit.innerHTML=orbitHtml(transientOrbitModel(model(),drag.c,start));mountOrbitFeatures(orbit);}const center=bounds.left+(start-START+drag.c.minutes/2)/(END-START)*bounds.width;drag.ghost.style.left=(event.clientX*.78+center*.22)-size.width/2+"px";drag.ghost.style.top=(event.clientY*.78+(bounds.top+46)*.22)-size.height/2+"px";drag.ghost.style.transform="scale(1.1,.78)";}
    }
    if(ringMinute!==null){
      const desired=ringMinute-drag.c.minutes/2,start=snapSlot(drag.id,drag.c.minutes,desired),orbit=$('.cc-orbit-host');drag.snap=start;
      if(orbit){orbit.innerHTML=orbitHtml(start===null?model():transientOrbitModel(model(),drag.c,start));mountOrbitFeatures(orbit);orbitDropHint(orbit,start===null?t('Нет свободного места нужной длительности.'):clock(start),start!==null);}
      if(start!==null)drag.ghost.style.transform='scale(1.1,.78)';
    }else if(!near||drag.snap===null){const orbit=$(".cc-orbit-host");if(orbit){orbit.innerHTML=orbitHtml(model());mountOrbitFeatures(orbit);}}
    for(const zone of root.querySelectorAll("[data-trash],[data-physical],[data-catalog-drop]"))zone.classList.toggle("is-drop",boundsHit(zone,event.clientX,event.clientY,10));
  }
  function pointerUp(event){
    if(drag?.type==="resize"&&event.pointerId===drag.pointer){
      const previous=drag;cleanupDrag();if(!previous.active){render();return;}suppressClick=Date.now()+450;
      if(previous.minutes===previous.c.minutes){render();return;}
      void commit("constructor_cube",()=>{
        const latest=canonical(previous.id);if(!latest||latest.start!==previous.base.start||latest.selected_date!==previous.base.selected_date)throw new Error(t("Данные изменились. Повторите действие."));
        const duration=timelineResizeMinutes(planning(),occupied(previous.id),clock(latest.start),previous.minutes,latest.kind==="prep"?120:240);
        if(duration!==previous.minutes)throw new Error(t("Нет свободного места нужной длительности."));
        return constructorPatch(previous.base,{...latest,minutes:previous.minutes},latest,new Set(["minutes"]));
      }).catch(reportError);return;
    }
    if(!drag||event.pointerId!==drag.pointer)return;const previous=drag,origin=drag.ghost?.getBoundingClientRect();if(!previous.active){drag=null;return;}suppressClick=Date.now()+450;const bin=[...root.querySelectorAll("[data-trash]")].find(el=>boundsHit(el,event.clientX,event.clientY)),left=$('[data-physical="selected"]'),right=$('[data-catalog-drop]'),timeline=$(".cc-timeline"),overTimeline=boundsHit(timeline,event.clientX,event.clientY,45),ringMinute=overTimeline?null:orbitDropAt(event.clientX,event.clientY);cleanupDrag();
    void (async()=>{
      if(bin){await ensureDay();await hideCube(previous.id,bin,origin);return;}
      if(overTimeline){if(previous.snap===null)throw new Error(t("Нет свободного места нужной длительности."));await placeOnTimeline(previous.id,previous.snap,origin);return;}
      if(ringMinute!==null){const start=snapSlot(previous.id,previous.c.minutes,ringMinute-previous.c.minutes/2);if(start===null)throw new Error(t("Нет свободного места нужной длительности."));await placeOnTimeline(previous.id,start,origin);return;}
      const target=boundsHit(left,event.clientX,event.clientY)?"selected":boundsHit(right,event.clientX,event.clientY)?"possible":null;
      if(!target){render();return;}
      pendingBlocks.add(previous.id);
      if(target==='possible'){
        try{if(isSelected(previous.id)){await ensureDay();await returnCube(previous.id);}render();snapshotPhysics();const row=[...root.querySelectorAll('.rc-source-card')].find(node=>node.dataset.rowId===previous.id),bounds=(row||$('[data-catalog-drop]'))?.getBoundingClientRect();await fly(find(previous.id)||previous.c,origin,bounds,{suction:true,effect:'catalog-return',onFinish:()=>{pendingBlocks.delete(previous.id);render();}});}catch(cause){pendingBlocks.delete(previous.id);render();throw cause;}return;
      }
      try{
        if(target==='selected'){if(!isSelected(previous.id))await selectCube(previous.id);else if(find(previous.id)?.start!=null)await commit('constructor_timeline',{cube_id:previous.id,date:selectedDate,start:null});}
        render();
        const node=[...root.querySelectorAll('[data-physical="selected"] [data-ccube]')].find(el=>el.dataset.ccube===previous.id),destination=node?.getBoundingClientRect();
        await fly(find(previous.id)||previous.c,origin,destination||null,{effect:'tray-slot',onFinish:()=>{pendingBlocks.delete(previous.id);render();}});
      }catch(cause){pendingBlocks.delete(previous.id);render();throw cause;}
    })().catch(reportError);
  }
  function pointerCancel(event){if(drag&&event.pointerId===drag.pointer){cleanupDrag();render();}}
  let tooltip=null,lastHoverPoint=null,hoverKey=null;
  function clearTimelineInfo(){const strip=$('.cc-timeline-info');if(strip){strip.textContent='';strip.removeAttribute('title');strip.removeAttribute('aria-label');strip.classList.remove('has-cube');}}
  function showTimelineInfo(c){const strip=$('.cc-timeline-info');if(!strip)return;const line=timelineCubeInfo(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task,importance:sourceImportance(c)});strip.textContent=line;strip.title=line;strip.setAttribute('aria-label',line);strip.classList.add('has-cube');}
  function hideTooltip(){tooltip?.remove();tooltip=null;hoverKey=null;}
  function leaveHover(){lastHoverPoint=null;hideTooltip();clearTimelineInfo();}
  function updateHoverTarget(element){
    if(disposed||drag?.active||popup||document.hidden||(globalThis.matchMedia?.('(hover: none)').matches&&element!==document.activeElement)){hideTooltip();clearTimelineInfo();return;}
    const target=element?.closest?.('[data-ccube],.rc-source-card,.cc-trophy,.cc-history-member,[data-ctooltip]');if(!target||!root.contains(target)){hideTooltip();clearTimelineInfo();return;}
    const carrier=target.closest('[data-ccube],[data-row-id],[data-id]'),id=target.dataset.ccube||target.dataset.rowId||target.dataset.id||carrier?.dataset.ccube||carrier?.dataset.rowId||carrier?.dataset.id;
    const c=id?(find(id)||historyCubes(planning(),{includeArchived:true}).find(cube=>cube.id===id)):null;
    if(!c){hideTooltip();clearTimelineInfo();return;}
    if(target.dataset.tray==='timeline'||carrier?.dataset.tray==='timeline'){hideTooltip();showTimelineInfo(c);return;}
    clearTimelineInfo();
    const anchor=target.closest('.rc-source-card,.cc-cube,.cc-trophy,.cc-history-member')||target;
    const key=id+':'+(anchor.dataset.rowId?'source':anchor.className);
    if(hoverKey!==key)hideTooltip();
    if(!tooltip){tooltip=document.createElement('div');tooltip.className='cc-hover-tooltip is-positioning';tooltip.dataset.testid='cube-hover-info';tooltip.setAttribute('role','tooltip');tooltip.innerHTML=hoverCubeContents(c,{typeLabel:TYPES[c.type||typeOf(c)]||TYPES.task});document.body.append(tooltip);hoverKey=key;}
    const rect=anchor.getBoundingClientRect();tooltip.style.width=Math.min(118,window.innerWidth-16)+'px';
    const position=hoverCubePosition(rect,{width:window.innerWidth,height:window.innerHeight},{height:tooltip.getBoundingClientRect().height});
    tooltip.style.left=position.left+'px';tooltip.style.top=position.top+'px';
    tooltip.style.setProperty('--rc-start-x',String(Math.max(.1,Math.min(1,rect.width/position.width))));
    tooltip.style.setProperty('--rc-start-y',String(Math.max(.1,Math.min(1,rect.height/tooltip.getBoundingClientRect().height))));
    tooltip.style.transformOrigin=Math.max(0,Math.min(position.width,rect.left+rect.width/2-position.left))+'px '+Math.max(0,Math.min(tooltip.getBoundingClientRect().height,rect.top+rect.height/2-position.top))+'px';
    tooltip.classList.remove('is-positioning');
  }
  function hoverTooltip(event){lastHoverPoint={x:event.clientX,y:event.clientY};updateHoverTarget(event.target);}
  function refreshHoverFromPoint(){if(lastHoverPoint)updateHoverTarget(document.elementFromPoint(lastHoverPoint.x,lastHoverPoint.y));}
  function focusHover(event){if(event.target.closest?.('[data-ccube],.cc-trophy,.cc-history-member'))updateHoverTarget(event.target);}
  function blurHover(event){if(!event.relatedTarget?.closest?.('[data-ccube],.cc-trophy,.cc-history-member')){hideTooltip();clearTimelineInfo();}}
  function keyDown(event){
    if(event.key==="Escape"){closePopup();cleanupDrag();leaveHover();render();return;}
    const task=event.target.closest?.(".cc-task");if(task&&["ArrowLeft","ArrowRight"].includes(event.key)){const tasks=[...root.querySelectorAll(".cc-task")],index=tasks.indexOf(task),next=tasks[clamp(index+(event.key==="ArrowLeft"?-1:1),0,tasks.length-1)];event.preventDefault();next?.focus();next?.scrollIntoView({block:"nearest",inline:"nearest"});}
    const sector=event.target.closest?.(".cc-day-orbit-sector[role=button]");if(sector&&root.contains(sector)&&["Enter"," "].includes(event.key)){event.preventDefault();openPopup(sector.dataset.id,sector,isSelected(sector.dataset.id)?"selected":"possible");return;}
    const cube=event.target.closest?.("[data-ccube]");if(cube&&["Enter"," "].includes(event.key)){event.preventDefault();openPopup(cube.dataset.ccube,cube,cube.dataset.tray);}
  }
  function updateTimers(){const c=getPopupCube(),el=document.querySelector("[data-constructor-popup]");if(!c||!el)return;const state=cubeTimerState(c),clockEl=el.querySelector("[data-timer]"),status=el.querySelector("[data-timer-status]");if(clockEl)clockEl.textContent=formatTime(state.remaining_ms);if(status)status.textContent=(state.status==="running"?t("Идёт работа"):state.status==="paused"?t("Пауза"):state.status==="finished"?t("Время вышло"):t("Не запущен"));}
  function remoteChanged(){
    void Promise.resolve(ctx.pendingDraft?.()).then(next=>{if(!disposed&&JSON.stringify(next||null)!==JSON.stringify(pending)){pending=next||null;render();}}).catch(()=>{});
    if(!monthlyEditorOpen)monthlyDraft=null;
    if(popupDeleted()){clearTimeout(saveTimer);deleteIntent=null;notice=deletedMessage();render();return;}
    if(deletionFrozen()||deletionState().conflicts.length){clearTimeout(saveTimer);render();return;}
    if(document.activeElement?.closest?.("[data-constructor-popup]")){notice=t("Work обновился. Ваш ввод сохранён в редакторе.");return;}render();
  }
  function outerDown(event){if(popup&&!event.target.closest("[data-constructor-popup]")&&!event.target.closest("[data-ccube]"))closePopup();}
  function toggle(event){if(event.target.matches?.("[data-month-plan-editor]"))monthlyEditorOpen=event.target.open;if(event.target.matches?.(".rc-task-choice"))taskPanelOpen=event.target.open;if(event.target.closest?.("[data-constructor-popup]"))schedulePopupPosition();}
  function resize(){clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{snapshotPhysics();render();positionPopup();},140);}
  document.addEventListener("visibilitychange",orbitVisibility);document.addEventListener("click",handleClick);document.addEventListener("input",handleInput);document.addEventListener("change",handleChange);document.addEventListener("pointermove",pointerMove,{passive:false});document.addEventListener("pointerup",pointerUp);document.addEventListener("pointercancel",pointerCancel);document.addEventListener("pointerdown",outerDown);document.addEventListener("keydown",keyDown);document.addEventListener("toggle",toggle,true);document.addEventListener("scroll",refreshHoverFromPoint,true);document.addEventListener("pointerleave",leaveHover);root.addEventListener("pointerleave",leaveHover);root.addEventListener("pointerdown",pointerDown);root.addEventListener("focusin",focusHover);root.addEventListener("focusout",blurHover);window.addEventListener("resize",resize);
  const unsubscribe=timeClient.subscribe(updateTimers);
  render();void refreshPending().catch(reportError);void refreshDashboard();
  async function prepareLeave(){
    if(drag?.active)throw new Error(t('Завершите перемещение кубика перед переходом.'));
    await taskChangeSerial;
    while(pendingActions.size)await Promise.all([...pendingActions]);
    await savePopup();
    while(popupSaves.size)await Promise.all([...popupSaves]);
    await serial;
    if(await ctx.pendingDraft?.())throw new Error(t('Сначала разрешите неподтверждённый запрос Work.'));
    if([...dirtyFields.values()].some(fields=>fields.size))throw new Error(t('Сохраните изменения кубика перед переходом.'));
    if(monthlyDraft){const baseline=monthlyDraftBaseline,tasks=[...baseline.taskIds].sort();if(monthlyDraft.month!==baseline.month||JSON.stringify([...monthlyDraft.taskIds].sort())!==JSON.stringify(tasks)){monthlyEditorOpen=true;render();throw new Error(t('Сохраните месячный план перед переходом.'));}}
    return {date:selectedDate,mode:mode==='month'?'history':'day'};
  }
  return {render,remoteChanged,prepareLeave,destroy(){disposed=true;disposeOrbitCountdown();for(const cancel of [...activeEffects])cancel();clearTimeout(orbitTimer);document.removeEventListener("visibilitychange",orbitVisibility);clearTimeout(saveTimer);clearTimeout(resizeTimer);snapshotPhysics();cleanupDrag();hideTooltip();closePopup(false);if(typeof unsubscribe==="function")unsubscribe();document.removeEventListener("click",handleClick);document.removeEventListener("input",handleInput);document.removeEventListener("change",handleChange);document.removeEventListener("pointermove",pointerMove);document.removeEventListener("pointerup",pointerUp);document.removeEventListener("pointercancel",pointerCancel);document.removeEventListener("pointerdown",outerDown);document.removeEventListener("keydown",keyDown);document.removeEventListener("toggle",toggle,true);document.removeEventListener("scroll",refreshHoverFromPoint,true);document.removeEventListener("pointerleave",leaveHover);root.removeEventListener("pointerleave",leaveHover);root.removeEventListener("pointerdown",pointerDown);root.removeEventListener("focusin",focusHover);root.removeEventListener("focusout",blurHover);window.removeEventListener("resize",resize);}};
}
