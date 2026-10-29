import {t} from '../i18n.js';
const PLANNING = new Set(['manual_start','manual_project','manual_project_delete','manual_task','manual_task_delete','manual_cube','cube_progress',
'cube_delete','constructor_cube','constructor_goal_status','day_outcome']);
const PERIOD = new Set(['period_shift','period_set','period_clear','project_visibility','task_rename','cube_rename','cube_select','cube_time',
'goal_hide','cube_move','cube_drop','cube_resize','cube_return','cube_pin','cube_activity_set','repeatable_save','repeatable_delete','repeatable_drop',
'standalone_create','standalone_update','standalone_delete','standalone_undo']);
const pad = n => String(n).padStart(2,'0');
const hhmm = n => n == null ? null : pad(Math.floor(n / 60))+':'+pad(n % 60);
const localDate=()=>{const d=new Date();return [d.getFullYear(),pad(d.getMonth()+1),pad(d.getDate())].join('-');};
const addDays = (value, days) => {const d = new Date(value + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate()+days); return d.toISOString().slice(0,10);};
export function createCommands(transport) {
  // Each entry undoes one dispatched intent; Ctrl+Z walks back through them.
  let queue = Promise.resolve(), history = [];
  const send = (action, data) => transport.command(action, data, {route:PLANNING.has(action) ? '/api/work/planning' : '/api/work/period/command'});
  async function dispatch({type, payload:p = {}}) {
    let result,sourceUndo=String(type).startsWith('standalone_');
    const steps = [];
    const externalDelete = (type === 'cube.delete' || type === 'cube_delete') && transport.model?.cubes?.some(c => c.id === (p.id || p.cube_id) &&
 c.kind === 'brainalot');
    const run = async (action, data) => { steps.push({action,data,route:PLANNING.has(action) ? 'planning' : 'period'}); };
    const model = () => transport.model;
    const get = (key,id) => {const row = model()?.[key]?.find(x=>x.id===id);
 if (!row) throw new Error(t('Объект изменился или удалён; обновите план.')); return row;};
    const linked=c=>c?.standalone||c?.kind==='brainalot'&&c.library_source&&c.library_note_version!=null;
    const noteUpdate=async(cube,patch)=>{sourceUndo=true;await run('standalone_update',{cube_id:cube.id,expected_note_version:
cube.library_note_version,patch});};
    if(['project.create','goal.create'].includes(type)&&model()?.planning_initialized===false)await run('manual_start',{timezone:
Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'});
    if (PLANNING.has(type) || PERIOD.has(type)) { await run(type,p); } else switch(type) {
      case 'plan.start': await run('manual_start',p.timezone?{timezone:p.timezone}:{}); break;
      case 'repeatable.save': await run('repeatable_save',p); break;
      case 'repeatable.delete': await run('repeatable_delete',{repeatable_id:p.repeatable_id}); break;
      case 'repeatable.drop': await run('repeatable_drop',{repeatable_id:p.repeatable_id,date:p.selected_date,start:hhmm(p.start_minute)}); break;
      case 'cube.pin': await run('cube_pin',{cube_id:p.id,pinned:p.pinned}); break;
      case 'project.create': await run('manual_project',{name:p.name,color:p.color}); break;
      case 'project.update': {
        const x=get('projects',p.id);
        if (p.name !== undefined || p.color !== undefined) await run('manual_project',{project_id:p.id,name:p.name ?? x.title,color:p.color ?? x.color});
        if (p.hidden !== undefined) await run('project_visibility',{project_id:p.id,visible:!p.hidden}); break;
      }
      case 'project.delete': await run('manual_project_delete',{project_id:p.id}); break;
      case 'goal.create': await run('manual_task',{project_id:p.project_id,title:p.title,minutes:Math.max(5,p.planned_minutes || 5),start:
p.start_date ?? null,end:p.end_date ?? null,deadline:p.deadline_date ?? null,volume_by_cubes:!!p.auto_volume}); break;
      case 'goal.update': {
        let x=get('tasks',p.id);
        const dates = p.start_date !== undefined || p.end_date !== undefined;
        if (dates) await run('period_set',{task_id:p.id,start:p.start_date ?? x.start,end:p.end_date ?? x.end});
        x=get('tasks',p.id);
        if (['title','project_id','planned_minutes','deadline_date','auto_volume'].some(k=>p[k] !== undefined)) {
          await run('manual_task',{task_id:p.id,project_id:p.project_id ?? x.project_id,title:p.title ?? x.title,minutes:
p.planned_minutes ?? x.minutes,start:p.start_date ?? x.start,end:p.end_date ?? x.end,deadline:p.deadline_date === undefined ? x.deadline :
 p.deadline_date,volume_by_cubes:p.planned_minutes !== undefined ? false : (p.auto_volume ?? !!x.volume_by_cubes)});
        } else if (p.title !== undefined) await run('task_rename',{task_id:p.id,title:p.title});
        break;
      }
      case 'goal.move': {const x=get('tasks',p.id); await run('period_shift',{task_id:p.id,start:addDays(x.start,p.delta_days),end:addDays(x.end,
p.delta_days)}); break;}
      case 'goal.done': {const x=get('tasks',p.id); await run('day_outcome',{task_id:p.id,date:p.done ? model().today : x.completed_on,done:!!p.done}); break;}
      case 'goal.delete': await run('manual_task_delete',{task_id:p.id}); break;
      case 'goal.hide': await run('goal_hide',{task_id:p.id,hidden:!!p.hidden}); break;
      case 'cube.create': {
        if(p.new_project){
          await run('manual_project',{name:p.new_project.name,color:p.new_project.color});
          await run('manual_task',{project_id:'$created_project',title:p.new_project.goal,minutes:p.duration_minutes,start:p.selected_date,end:p.selected_date,volume_by_cubes:true});
          await run('manual_cube',{task_id:'$created_task',title:p.title,minutes:p.duration_minutes,...(p.selected_date?{date:p.selected_date}:{})});
          if(p.selected_date)await run('cube_drop',{cube_id:'$created_cube',date:p.selected_date,start:hhmm(p.start_minute)});
          if(p.activity_kind&&p.activity_kind!=='work')await run('cube_activity_set',{cube_id:'$created_cube',activity_kind:p.activity_kind});
          break;
        }
        if(!p.goal_id){sourceUndo=true;await run('standalone_create',{title:p.title,date:p.selected_date||null,minutes:p.duration_minutes,
activity_kind:p.activity_kind||'personal',start:hhmm(p.start_minute)});break;}
        await run('manual_cube',{task_id:p.goal_id,title:p.title,minutes:p.duration_minutes,...(p.selected_date ? {date:p.selected_date} : {})});
        if (p.selected_date) await run('cube_drop',{cube_id:'$created_cube',date:p.selected_date,start:hhmm(p.start_minute)});
        if(p.activity_kind&&p.activity_kind!=='work')await run('cube_activity_set',{cube_id:'$created_cube',activity_kind:p.activity_kind});
        break;
      }
      case 'cube.place': { const x=get('cubes',p.id);if(linked(x)){await noteUpdate(x,{date:p.selected_date,start:hhmm(p.start_minute)});break;
} const data={cube_id:p.id,date:p.selected_date,start:hhmm(p.start_minute)}; if (x.kind === 'calendar_optional' &&
 x.duration_unknown) data.minutes=p.duration_minutes ?? 30; await run('cube_drop',data); break; }
      case 'cube.return': {const x=get('cubes',p.id);if(linked(x))await noteUpdate(x,{date:null,start:null});else await run('cube_return',{cube_id:
p.id});break;}
      case 'cube.delete': {const x=get('cubes',p.id);if(x.standalone){sourceUndo=true;await run('standalone_delete',{cube_id:p.id,
expected_note_version:x.library_note_version});}else await run('cube_delete',{cube_id:p.id});break;}
      case 'cube.update': {
        let x=get('cubes',p.id);
        if(linked(x)){
          const patch={};for(const [from,to]of [['title','title'],['duration_minutes','minutes'],['readiness_pct','percent'],['activity_kind',
'activity_kind'],['sources','sources']])if(p[from]!==undefined)patch[to]=p[from];
          if(p.sources!==undefined)patch.sources=p.sources.map(s=>({id:s.id,url:s.url||'',text:s.text||''}));
          if(p.selected_date!==undefined)patch.date=p.selected_date;
          if(p.start_minute!==undefined)patch.start=hhmm(p.start_minute);
          if(p.readiness_pct>=100&&!(p.selected_date===undefined?x.selected_date:p.selected_date)){patch.date=model().planning_initialized===false?
localDate():model().today;patch.start=null;}
          await noteUpdate(x,patch);break;
        }
        if (p.goal_id !== undefined && p.goal_id !== x.task_id) await run('cube_reassign',{cube_id:p.id,task_id:p.goal_id});
        const resizeTogether = p.duration_minutes !== undefined && !!x.selected_date && (p.start_minute !== undefined || p.selected_date !== undefined);
        const patch={cube_id:p.id};
        if (p.title !== undefined) patch.title=p.title;
        if (p.duration_minutes !== undefined && x.kind === 'calendar_optional' && x.duration_unknown) await run('calendar_offer_estimate',{cube_id:
p.id,minutes:p.duration_minutes});
        else if (p.duration_minutes !== undefined && !resizeTogether) patch.minutes=p.duration_minutes;
        if (p.sources !== undefined) patch.sources=p.sources.map(s=>({id:s.id,url:s.url || '',text:s.text || ''}));
        if (Object.keys(patch).length>1) await run('constructor_cube',patch);
        if(p.activity_kind!==undefined)await run('cube_activity_set',{cube_id:p.id,activity_kind:p.activity_kind});
        x=get('cubes',p.id);
        if (resizeTogether && (p.selected_date === undefined || p.selected_date !== null)) {
          await run('cube_resize',{cube_id:p.id,date:p.selected_date ?? x.selected_date,start:p.start_minute === undefined ? x.start :
 hhmm(p.start_minute),minutes:p.duration_minutes});
        } else if (p.selected_date !== undefined || p.start_minute !== undefined) {
          const date=p.selected_date === undefined ? x.selected_date : p.selected_date;
          if (date) await run('cube_drop',{cube_id:p.id,date,start:p.start_minute === undefined ? x.start : hhmm(p.start_minute)});
          else if (x.selected_date) await run('cube_return',{cube_id:p.id});
        }
        x=get('cubes',p.id);
        if (p.readiness_pct !== undefined) {
          if (p.readiness_pct >= 100 && !(p.selected_date === undefined ? x.selected_date : p.selected_date)) await run('cube_drop',{cube_id:p.id,
date:model().today,start:null});
          await run('cube_progress',{cube_id:p.id,percent:p.readiness_pct});
        }
        break;
      }
      default: throw new TypeError(`Unknown Work intent: ${type}`);
    }
    if (!steps.length) return {model:model(),result:null,warnings:[],undo:null};
    result = steps.length === 1 ? await send(steps[0].action,steps[0].data) : await send('ui_batch',{commands:steps});
    const revision = result.result?.revision ?? result.model?.revision;
    const operation=result.result?.source_result?.operation_id;
    const inverse = sourceUndo&&operation?[{operation_id:operation}]:!sourceUndo&&!externalDelete && type !== 'plan.start' && type !== 'manual_start' &&
 Number.isInteger(revision) ? [revision] : [];
    history = inverse.length ? [...history.slice(-19), inverse] : [];
    return {...result, undo:inverse.length ? true : null};
  }
  function execute(intent){const pending=queue.then(()=>dispatch(intent));queue=pending.catch(()=>{});return pending;}
  function undo(){ const pending=queue.then(async()=>{ let result=null; const inverse=history[history.length-1]||[]; while(inverse.length){const item=inverse[inverse.length-1];
result=typeof item==='number'?await send('undo',{revision:item}):await send('standalone_undo',item);inverse.pop(); } if(!inverse.length)history.pop(); return result; });
 queue=pending.catch(()=>{}); return pending; }
  return {execute, get canUndo(){return history.length > 0;}, undo,async flush(){await queue;await transport.flush?.();}};
}













