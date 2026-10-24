// Preserve the stored award ID and render its approved family for the goal scope.
import { AWARD_STUDIES, renderAward } from './work-goal-award-studies.js';
const ORIGINAL_IDS=['enamel','crystal','order','cup','relic','crown','shield','compass','laurel','sun','peak','book','phoenix','orbit','obelisk'];
export function renderGoalAward(variant,scope){
  const index=Math.max(0,ORIGINAL_IDS.indexOf(variant));
  return renderAward(AWARD_STUDIES[index].id,scope==='week'?'cup':'medal');
}
