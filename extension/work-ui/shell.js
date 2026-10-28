import {t} from '../i18n.js';

export function installShell(ctx) {
  const {S,U}=ctx;
  function hintItem([title,description],index,step) {
    const number=index+1;
    const current=number===step?' now':'';
    const complete=number<step?' ok':'';
    const marker=number<step?'✓':number;
    return `<span class="hs${current}${complete}"><em>${marker}</em>`+
      `<b>${title}</b><small>${description}</small></span>`;
  }
  function hints() {
    if(S.hasStandalone||S.planning_initialized===false||(!S.started&&!U.started))return '';
    if(S.cubes.some(k=>k.goal&&k.day!=null))return '';
    const hasP=S.projects.length>0,hasG=S.goals.length>0,hasC=S.cubes.some(k=>k.goal);
    const step=!hasP?1:!hasG?2:!hasC?3:4;
    const items=[
      [t('+ Проект'),t('например «3D-печать»')],
      [t('+ Цель'),t('дни и объём в часах')],
      [t('+ Кубик'),t('кусок работы: 15 мин – 4 ч')],
      [t('Перетащи кубик'),t('в день, на шкалу или часы')],
    ];
    const steps=items.map((item,index)=>hintItem(item,index,step)).join('<i>→</i>');
    return `<div class="hints">${steps}</div>`;
  }
  function choiceButton(action,value,label,current,className='') {
    const css=className?` class="${className}"`:'';
    const selected=value===current;
    return `<button${css} data-act="${action}" data-arg="${value}" aria-pressed="${selected}">${label}</button>`;
  }
  function themeChoices(theme) {
    const choices=[['graphite',t('Графит')],['midnight',t('Полночь')],['contrast',t('Контраст')],['glamour',t('Гламур')]];
    return choices.map(([value,label])=>choiceButton('theme',value,label,theme)).join('');
  }
  function appbar() {
    const {theme,mode}=ctx.currentTheme();
    const day=choiceButton('tab','day',t('День'),U.screen,'tab');
    const week=choiceButton('tab','week',t('Неделя'),U.screen,'tab');
    const themes=themeChoices(theme);
    const dark=choiceButton('mode','dark',t('Тёмная'),mode);
    const light=choiceButton('mode','light',t('Светлая'),mode);
    const timer=ctx.timerBar?ctx.timerBar():'';
    return '<div class="appbar"><span class="brand">Brainalot · Work</span>\n     '+
      day+week+'\n     <span class="sp"></span>\n     '+timer+'\n     '+
      `<button class="btn small" data-act="today">${t('Сегодня')}</button>\n     `+
      `<div class="themes" role="group" aria-label="${t('Тема')}">${themes}</div>\n     `+
      `<div class="themes" role="group" aria-label="${t('Режим')}">${dark}${light}</div>\n    </div>`;
  }
  function startScreen() {
    const description=t(
      'Соберите план вручную, без агента: сначала проект, потом цели на несколько дней, потом кубики — куски работы, которые раскладываются по дням.'
    );
    return `<section class="panel start"><h1>${t('План Work пуст')}</h1><p>${description}</p>`+
      `<button class="btn primary" data-act="start">${t('Начать план вручную')}</button></section>`;
  }
  Object.assign(ctx,{hints,appbar,startScreen});
}
