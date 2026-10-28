import {t} from '../i18n.js';
import {actionAttrs,fieldAttrs,modalClose} from './dialogs.js';

export const REPEATABLE_PREFIX='repeatable:';
export const repeatableGestureId=id=>REPEATABLE_PREFIX+id;
export function repeatableCube(template) {
  if(!template)return null;
  const universal=template.id==='prep-universal';
  const title=universal&&template.title==='П. к уроку'?t('П. к уроку'):template.title;
  const activity=template.activity_kind||(universal?'work':'personal');
  const sources=template.sources||[];
  return {
    id:repeatableGestureId(template.id),repeatableId:template.id,isRepeatableTemplate:true,
    title,activity_kind:activity,min:template.minutes,pct:0,goal:null,day:null,at:null,
    pinned:!!template.pinned,links:sources,sources,
  };
}

export function installRepeatables(ctx) {
  const {S,esc}=ctx;
  const repeatable=id=>(S.repeatables||[]).find(item=>item.id===id);
  const returnedInstances=()=>S.cubes.filter(k=>k.repeatable_instance&&k.day==null&&k.pct<100);
  const templateKeys=items=>items.map(item=>ctx.keycap(repeatableCube(item))).join('');

  function repeatablesPanel() {
    const templates=(S.repeatables||[]).filter(item=>!item.pinned);
    const returned=returnedInstances().filter(k=>!k.pinned);
    if(!templates.length&&!returned.length)return '';
    const instances=returned.length?
      `<button class="link" data-act="repeatable-instances">${t('В работе')} · ${returned.length}</button>`:'';
    const day=ctx.U?.screen==='day';
    const keys=templates.length?`<div class="cubes"${day?' data-scroll-strip tabindex="0"':''}>${templateKeys(templates)}</div>${day&&templates.length>3?'<small class="strip-more" aria-hidden="true">↔ · '+templates.length+'</small>':''}`:'';
    return '<section class="panel trayp repeatables" data-drop="shelf">'+
      `<h3>${t('Повторяемые')}${instances}</h3>${keys}</section>`;
  }
  function pinnedPanel() {
    const possible=ctx.possibleOn?ctx.possibleOn(ctx.U.day):S.cubes.filter(k=>k.day==null&&k.pct<100);
    const keys=[...(S.repeatables||[]).filter(item=>item.pinned).map(repeatableCube),...possible.filter(k=>k.pinned)];
    keys.sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
    if(!keys.length)return '';
    const cubes=keys.map(k=>ctx.keycap(k)).join('');
    return '<section class="panel trayp pinned-pool" data-drop="shelf">'+
      `<h3>${t('Закреплено')}</h3><div class="cubes">${cubes}</div></section>`;
  }
  function repeatableFields(item,id) {
    const title=esc(repeatableCube(item).title);
    const activity=ctx.activityOptions?ctx.activityOptions(item.activity_kind||'work'):'';
    const titleField=fieldAttrs('repeatable.title',id,esc);
    const activityField=fieldAttrs('repeatable.activity_kind',id,esc);
    const minutesField=fieldAttrs('repeatable.minutes',id,esc);
    return `<input class="gc-title" ${titleField} value="${title}" aria-label="${t('Название кубика')}">\n      `+
      `<div class="fields one"><label>${t('Тип')}<select ${activityField}>${activity}</select></label>`+
      `<label>${t('Длительность')} · <span class="muted">${t('Для новых экземпляров')}</span>`+
      `<input type="number" min="5" max="240" step="5" ${minutesField} value="${item.minutes}"></label></div>`;
  }
  function repeatableEditor(id) {
    const item=repeatable(id);
    if(!item)return '';
    const fields=repeatableFields(item,id);
    const remove=actionAttrs('repeatable-delete',id,esc);
    return '<form class="modal" data-form="none"><div class="mh">'+
      `<span class="type">∞ · ${t('Повторяемый кубик')}</span>${modalClose()}</div>\n      `+
      fields+'\n      <div class="gc-actions">'+
      `<button type="button" class="btn danger" ${remove}>${t('Удалить повторяемый кубик')}</button>`+
      `<span class="sp"></span>${modalClose('btn',t('Закрыть'))}</div></form>`;
  }
  function instanceKey(k) {
    const date=k.report_date?`<small class="muted">${esc(k.report_date)}</small>`:'';
    return `<div>${ctx.keycap(k)}${date}</div>`;
  }
  function repeatableInstancesEditor() {
    const list=returnedInstances();
    const keys=list.map(instanceKey).join('');
    return '<div class="modal wide"><div class="mh">'+
      `<h2>${t('Повторяемые')} · ${t('В работе')} · ${list.length}</h2>${modalClose()}</div>`+
      `<div class="cubes">${keys}</div></div>`;
  }
  function repeatableNewEditor(id) {
    const k=ctx.cube(id);
    if(!k)return '';
    return '<form class="modal" data-form="repeatable-new"><div class="mh">'+
      `<h2>${t('Повторяемый кубик')}</h2>${modalClose()}</div>`+
      `<input type="hidden" name="cube_id" value="${esc(id)}"><div class="fields one">`+
      `<label>${t('Название')}<input name="title" required value="${esc(k.title)}"></label>`+
      `<label>${t('Длительность')}<input name="minutes" type="number" min="5" max="240" step="5"`+
      ' required placeholder="30"></label></div><div class="mact">'+
      modalClose('btn',t('Отмена'))+`<button class="btn primary">${t('Создать')}</button></div></form>`;
  }
  function selectCreatedTemplate(before) {
    const created=(S.repeatables||[]).filter(item=>!before.has(item.id));
    ctx.U.modal=null;
    if(created.length===1)ctx.U.modal={type:'repeatable',id:created[0].id};
  }
  function saveCubeRepeatable(id,options={}) {
    const k=ctx.cube(id);
    if(!k||k.isRepeatableTemplate)return;
    if(k.repeatable_id&&repeatable(k.repeatable_id))return ctx.openModal({type:'repeatable',id:k.repeatable_id});
    if((k.duration_unknown||!(k.min>0))&&!options.minutes)return ctx.openModal({type:'repeatable-new',cubeId:id});
    const before=new Set((S.repeatables||[]).map(item=>item.id));
    return ctx.send('repeatable.save',{cube_id:id,...options},t('Кубик стал повторяемым'),()=>selectCreatedTemplate(before));
  }
  Object.assign(ctx,{
    repeatable,repeatablesPanel,pinnedPanel,repeatableEditor,repeatableNewEditor,repeatableInstancesEditor,saveCubeRepeatable,
  });
}
