import {importanceScore,validImportanceScore} from './importance-score.js';
import { t } from "./i18n.js";

// Library notes own their score; a materialized cube must not freeze a copy.
export function sourceNote(cube,dashboard={}){
  if(cube?.kind!=='brainalot'||!cube.source_ref)return null;
  for(const rows of Object.values(dashboard))if(Array.isArray(rows)){
    const note=rows.find(row=>row?.id===cube.source_ref&&row.kind==='task');
    if(note)return note;
  }
  return null;
}

export function sourceImportanceTarget(cube,planning={},dashboard={}){
  if(cube?.kind==='brainalot'){
    const note=sourceNote(cube,dashboard);
    return {kind:'note',id:cube.source_ref,score:note?importanceScore(note,0):0,
      version:note?.version,editable:!!(note&&typeof note.version==='string'&&note.version)};
  }
  const task=cube?.task_id?(planning.tasks||[]).find(task=>task.id===cube.task_id):null;
  if(task)return {kind:'task',id:task.id,score:importanceScore(task,0),editable:true};
  return {kind:null,id:null,score:0,editable:false};
}

export const sourceImportance=(cube,planning={},dashboard={})=>sourceImportanceTarget(cube,planning,dashboard).score;

// The caller supplies the existing CAS transport and handles refresh/errors.
// A library conflict is never retried with a newly fetched version.
export async function saveSourceImportance(target,value,{action,api}){
  if(!validImportanceScore(value))throw new RangeError(t("Важность должна быть целым числом от 0 до 5"));
  if(!target?.editable)throw new Error(t("Источник важности недоступен. Обновите предложения."));
  if(target.kind==='task')return action('task_importance',{task_id:target.id,importance_score:value});
  if(target.kind==='note')return api('/api/notes/'+encodeURIComponent(target.id),'PATCH',{
    expected_version:target.version,patch:{importance_score:value},
  });
  throw new Error(t("У этого кубика нет задачи с редактируемой важностью."));
}
