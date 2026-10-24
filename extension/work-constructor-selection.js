// Local UI composition preferences only. Cube facts and history remain canonical.
export function uniqueTaskCubes(cubes){
  const seen=new Set();return cubes.filter(c=>{const id=c.canonical_id||c.blueprint_ref||c.id;if(!id||seen.has(id))return false;seen.add(id);return true;});
}
export function isProtectedTaskCube(cube,{timerIds=new Set(),historyIds=new Set()}={}){
  return Number(cube.progress_percent)>0||Number(cube.actual_minutes)>0||timerIds.has(cube.id)||historyIds.has(cube.id)||!!(cube.started_at||cube.completed_at||cube.report_date)||cube.status==='completed';
}
export function taskPoolChange(cubes,taskIds,options={}){
  const chosen=new Set(taskIds),unique=uniqueTaskCubes(cubes),removed=[],retained=[],included=[];
  for(const cube of unique){if(!cube.task_id||chosen.has(cube.task_id))included.push(cube);else if(isProtectedTaskCube(cube,options)){included.push(cube);retained.push(cube);}else removed.push(cube);}
  return {included,removed,retained};
}
export function readTaskChoice(storage,key,fallback=[]){
  try{const value=JSON.parse(storage.getItem(key));if(value?.version===1&&Array.isArray(value.taskIds)&&value.taskIds.every(id=>typeof id==='string'))return {taskIds:[...new Set(value.taskIds)],collapsed:!!value.collapsed,...(Array.isArray(value.restoredCubeIds)?{restoredCubeIds:[...new Set(value.restoredCubeIds.filter(id=>typeof id==='string'))]}:{}),...(Array.isArray(value.protectedCubeIds)?{protectedCubeIds:[...new Set(value.protectedCubeIds.filter(id=>typeof id==='string'))]}:{})};}catch{}
  return {taskIds:[...new Set(fallback.filter(Boolean))],collapsed:false};
}
export function saveTaskChoice(storage,key,choice){storage.setItem(key,JSON.stringify({version:1,taskIds:[...new Set(choice.taskIds)],collapsed:!!choice.collapsed,...(choice.restoredCubeIds?.length?{restoredCubeIds:[...new Set(choice.restoredCubeIds)]}:{}),...(choice.protectedCubeIds?.length?{protectedCubeIds:[...new Set(choice.protectedCubeIds)]}:{})}));}
