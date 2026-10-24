import { t } from "./i18n.js";
export const shouldCelebrateCompletion=(before,next)=>Number(before?.progress_percent||0)<100&&Number(next?.progress_percent)>=100&&!before?.award_variant;
export function createKeyedSaveQueue(){
  const tails=new Map();
  return (id,save)=>{const operation=(tails.get(id)||Promise.resolve()).catch(()=>{}).then(save);tails.set(id,operation);const release=()=>{if(tails.get(id)===operation)tails.delete(id);};operation.then(release,release);return operation;};
}
export function createCompletionClaims(){
  const claims=new Map();return {
    reserve(id,before,next){if(claims.has(id)||!shouldCelebrateCompletion(before,next))return null;const token={};claims.set(id,token);return token;},
    release(id,token){if(token&&claims.get(id)===token){claims.delete(id);return true;}return false;},
  };
}
export function transientOrbitModel(model,cube,start){
  if(!Number.isFinite(start))return model;
  const selected=(model.selected||[]).filter(c=>c.id!==cube.id);
  const plannedStart=String(Math.floor(start/60)).padStart(2,'0')+':'+String(start%60).padStart(2,'0');
  return {...model,selected:[...selected,{...cube,start:plannedStart,progress_percent:Number(cube.progress_percent)||0}]};
}
export function goalPreviewLabel(goal,scope){return goal?.award_variant?(scope==='day'?t("Медаль за цель"):t("Кубок за цель")):(scope==='day'?t("Пример медали"):t("Пример кубка"));}
