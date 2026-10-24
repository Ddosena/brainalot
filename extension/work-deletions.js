import {t} from './i18n.js';

export function cubeDeletion(planning={},cube={}){
  const ids=new Set([cube.id,cube.canonical_id,cube.blueprint_ref].filter(Boolean));
  for(const row of Object.values(planning.cube_deletions||{})){
    if(ids.has(row.cube_id)||row.blueprint_ref&&ids.has(row.blueprint_ref))return row;
    const historical=planning.constructor?.cubes?.[row.cube_id];
    if(cube.kind==='brainalot'&&cube.source_ref&&historical?.kind==='brainalot'&&historical.source_ref===cube.source_ref)return row;
  }
  return null;
}
export const isDeletedCube=(planning,cube)=>!!cubeDeletion(planning,cube);
export const normalizeDeletionState=value=>({pending:Array.isArray(value?.pending)?structuredClone(value.pending):[],conflicts:Array.isArray(value?.conflicts)?structuredClone(value.conflicts):[]});

export async function confirmedCubeDelete({cubeId,noteVersion,saveDraft,stopAndMeasure,readCurrent,commit}){
  await saveDraft();
  const cube=readCurrent();
  if(!cube)throw new Error(t('Кубик уже удалён. Ваш черновик сохранён для просмотра.'));
  await stopAndMeasure(cube);
  if(!readCurrent())throw new Error(t('Кубик уже удалён. Ваш черновик сохранён для просмотра.'));
  const response=await commit('cube_delete',{cube_id:cubeId,...(noteVersion?{expected_note_version:noteVersion}:{})});
  if(response?.delete_result?.status!=='complete'||!response?.state?.planning||!Number.isInteger(response.revision)){
    const error=new Error(t('Удаление ещё не подтверждено сервером. Проверьте состояние.'));
    error.delete_result=response?.delete_result;throw error;
  }
  return response;
}
