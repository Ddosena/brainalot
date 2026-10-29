import {t} from '../i18n.js';

// Shared binding attributes keep identity escaping identical across editors.
export const actionAttrs=(action,id,esc)=>`data-act="${action}" data-arg="${esc(id)}"`;
export const fieldAttrs=(field,id,esc)=>`data-field="${field}" data-id="${esc(id)}"`;
export function modalClose(className='x',label) {
  const aria=label==null?` aria-label="${t('Закрыть')}"`:'';
  const text=label==null?'✕':label;
  return `<button type="button" class="${className}" data-act="modal-close"${aria}>${text}</button>`;
}

export function installDialogs(ctx) {
  const {clk,ddmm,dow,esc,hm,iso,wd,S,U,DURS,CLIP,DOWF}=ctx;
  const gap5='\n     ',gap6='\n      ',gap7='\n       ';
  function option(value,title,selected=false) {
    const checked=selected?' selected':'';
    return `<option value="${value}"${checked}>${title}</option>`;
  }
  function durOptions(cur) {
    const list=DURS.includes(cur)?DURS:[...DURS,cur].sort((a,b)=>a-b);
    return list.map(m=>option(m,hm(m),m===cur)).join('');
  }
  function projectOptions(cur) {
    return S.projects.map(p=>option(esc(p.id),esc(p.name),p.id===cur)).join('');
  }
  function goalGroup(project,cur) {
    const goals=S.goals.filter(g=>g.p===project.id&&(!ctx.isDone(g)||g.id===cur));
    if(!goals.length)return '';
    const options=goals.map(g=>option(esc(g.id),esc(g.title),g.id===cur)).join('');
    return `<optgroup label="${esc(project.name)}">${options}</optgroup>`;
  }
  function goalOptions(cur) {
    return S.projects.map(p=>goalGroup(p,cur)).join('');
  }
  function paletteOptions() {
    return ctx.swatchChoices().map(c=>option(c.color||c.hex,esc(ctx.paletteName(c)))).join('');
  }
  function swatch(entry,name,current,field,id) {
    const color=entry.color||entry.hex;
    const owner=S.projects.filter(p=>p.id!==id&&p.c?.toLowerCase()===color.toLowerCase()).map(p=>p.name).join(', ');
    const nameText=ctx.paletteName(entry),count=entry.count||0;
    const occupied=owner?t(' · занят проектом ')+owner:'';
    const label=esc(`${nameText} · ${count}${occupied}`);
    const checked=color.toLowerCase()===current?' checked':'';
    const binding=field?' '+fieldAttrs(field,id,esc):'';
    const owners=owner?`<em>${esc(owner)}</em>`:'';
    return `<label style="--c:${color}" title="${label}"><input type="radio" name="${name}" value="${color}"`+
      ` required aria-label="${label}"${checked}${binding}><span></span>`+
      `<small>${esc(nameText)} · ${count}${owners}</small></label>`;
  }
  function swatches(name,cur,field,id) {
    const choices=ctx.swatchChoices(),current=String(cur||'').toLowerCase();
    const custom=cur&&!choices.some(c=>(c.color||c.hex).toLowerCase()===current);
    const warning=custom?`<p class="muted">${t('Свой цвет — выберите цвет календаря, чтобы события попадали на стрелу.')}</p>`:'';
    const colors=choices.map(entry=>swatch(entry,name,current,field,id)).join('');
    return `${warning}<div class="sw">${colors}</div>`;
  }
  function formHeader(form,title) {
    return `<form class="modal" data-form="${form}"><div class="mh"><h2>${title}</h2>${modalClose()}</div>`;
  }
  function submitActions(label) {
    return `<div class="mact">${modalClose('btn',t('Отмена'))}<button class="btn primary">${label}</button></div></form>`;
  }
  function actionButton(action,id,label,className='btn') {
    const binding=actionAttrs(action,id,esc);
    return `<button type="button" class="${className}" ${binding}>${label}</button>`;
  }
  function newProject() {
    const palette=ctx.paletteColors();
    const color=palette.find(c=>!S.projects.some(p=>p.c?.toLowerCase()===c.toLowerCase()))||palette[0];
    return formHeader('project-new',t('Новый проект'))+
      `<p class="muted">${t('Проект — большое направление. В нём живут цели.')}</p>`+gap5+
      `<div class="fields one"><label>${t('Название')}<input name="name" required`+
      ` placeholder="${t('Например, «3D-печать»')}" autocomplete="off"></label>`+
      `<label>${t('Цвет')}${swatches('c',color)}</label></div>`+gap5+submitActions(t('Создать проект'));
  }
  function projectEditor(m) {
    const project=ctx.proj(m.id);
    if(!project)return '';
    const count=S.goals.filter(g=>g.p===project.id).length;
    const cascade=count?` ${t('и')} ${count} ${t('целей')}`:'';
    const remove=actionButton('project-delete',project.id,t('Удалить проект')+cascade,'btn danger');
    const name=fieldAttrs('project.name',project.id,esc);
    return formHeader('none',t('Проект'))+gap5+
      `<div class="fields one"><label>${t('Название')}<input ${name} value="${esc(project.name)}"></label>`+
      `<label>${t('Цвет')}${swatches('c',project.c,'project.c',project.id)}</label></div>`+gap5+
      `<div class="gc-actions">${remove}<span class="sp"></span>${modalClose('btn',t('Закрыть'))}</div></form>`;
  }
  function newProjectFields(hasProjects) {
    const label=hasProjects?t('или новый проект'):t('Проект');
    const required=hasProjects?'':' required';
    return `<label>${label}<input name="newp" placeholder="${t('Название проекта')}"${required} autocomplete="off"></label>`+
      `<label>${t('Цвет нового проекта')}<select name="newc">`+
      `<option value="">${t('Выберите цвет календаря')}</option>${paletteOptions()}</select></label>`;
  }
  function newGoal(m) {
    const hasProjects=S.projects.length>0;
    const project=hasProjects?`<label>${t('Проект')}<select name="p">${projectOptions(m.p)}</select></label>`:'';
    return formHeader('goal-new',t('Новая цель'))+
      `<p class="muted">${t('Цель — результат на несколько дней. Её делят на кубики.')}</p>`+gap5+
      `<div class="fields one"><label>${t('Название')}<input name="title" required`+
      ` placeholder="${t('Например, «Подставка для катушки»')}" autocomplete="off"></label></div>`+gap5+
      `<div class="fields">${project}`+gap6+newProjectFields(hasProjects)+gap6+
      `<label>${t('С')}<input type="date" name="s" value="${iso(m.s)}" required></label>`+
      `<label>${t('По')}<input type="date" name="e" value="${iso(m.e)}" required></label>`+gap6+
      `<label>${t('Объём, ч')}<input type="number" name="h" min="0.5" step="0.5" value="3" required></label>`+
      `<label>${t('Срок (необязательно)')}<input type="date" name="due"></label></div>`+gap5+submitActions(t('Создать цель'));
  }
  function defaultGoal(m) {
    if(m.goal&&ctx.goal(m.goal))return m.goal;
    if(m.where==='day')return '';
    const selected=U.poolGoal&&ctx.goal(U.poolGoal);
    return selected&&!ctx.isDone(selected)?U.poolGoal:'';
  }
  function placementChoices(toDay) {
    const shelf=toDay?'':' checked',day=toDay?' checked':'';
    return `<div class="radio"><label><input type="radio" name="where" value="shelf"${shelf}>${t('Без дня')}</label>`+
      `<label><input type="radio" name="where" value="day"${day}>${t('Сразу в день')}</label></div>`;
  }
  function newCube(m) {
    const goal=defaultGoal(m),toDay=m.where==='day';
    const blank=option('',t('Без цели'),!goal);
    const activity=ctx.activityOptions(goal?'work':'personal');
    return formHeader('cube-new',t('Новый кубик'))+gap6+
      `<div class="fields one"><label>${t('Что сделать')}<input name="title" required`+
      ` placeholder="${t('Например, «Смоделировать основание»')}" autocomplete="off"></label></div>`+gap6+
      `<div class="fields"><label>${t('Цель')}<select name="goal">${blank}${goalOptions(goal)}</select></label>`+
      `<label>${t('или новый проект')}<input name="newp" autocomplete="off" placeholder="${t('Название проекта')}"></label>`+
      `<label>${t('Цвет нового проекта')}<select name="newc"><option value="">${t('Выберите цвет календаря')}</option>${paletteOptions()}</select></label>`+
      `<label>${t('Новая цель')}<input name="newgoal" autocomplete="off" placeholder="${t('Название')}"></label>`+
      `<label>${t('Длительность')}<select name="min">${durOptions(60)}</select></label>`+gap7+
      `<label>${t('Тип')}<select name="activity_kind">${activity}</select></label>`+gap7+placementChoices(toDay)+gap7+
      `<label>${t('День')}<input type="date" name="day" value="${iso(U.day)}"></label>`+
      `<label>${t('Время (необязательно)')}<input type="time" name="at" step="900"></label></div>`+gap6+
      submitActions(t('Добавить кубик'));
  }
  function cubeFields(k,g) {
    const goalBinding=fieldAttrs('cube.goal',k.id,esc);
    const goal=g?`<label>${t('Цель')}<select ${goalBinding}>${goalOptions(k.goal)}</select></label>`:'';
    const activity=ctx.activityOptions(k.activity_kind||(k.goal?'work':'personal'));
    const activityBinding=fieldAttrs('cube.activity_kind',k.id,esc);
    const durationBinding=fieldAttrs('cube.min',k.id,esc);
    const dayBinding=fieldAttrs('cube.day',k.id,esc),timeBinding=fieldAttrs('cube.at',k.id,esc);
    const day=k.day!=null?iso(k.day):'',time=k.at!=null?clk(k.at):'',disabled=k.day==null?' disabled':'';
    return `<div class="fields">${goal}`+gap6+
      `<label>${t('Тип')}<select ${activityBinding}>${activity}</select></label>`+
      `<label>${t('Длительность')}<select ${durationBinding}>${durOptions(k.min)}</select></label>`+gap6+
      `<label>${t('День')}<input type="date" ${dayBinding} value="${day}"></label>`+gap6+
      `<label>${t('Время')}<input type="time" step="900" ${timeBinding} value="${time}"${disabled}></label></div>`;
  }
  function cubePlacement(k,g) {
    if(k.day==null)return g?t('На полке цели. Перетащи в день или выбери дату.'):
      t('В «Других». Перетащи в день или выбери дату.');
    const end=clk(k.at+k.min);
    const time=k.at!=null?`, ${clk(k.at)}–${end}`:t(', без времени');
    return `${t('В дне')} ${dow(k.day)} ${ddmm(k.day)}${time}.`;
  }
  function sourceContent(source) {
    if(!/^https?:\/\//i.test(source.url||''))return `<span title="${esc(source.text)}">✎ ${esc(source.text)}</span>`;
    return `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer" title="${esc(source.url)}">`+
      `${CLIP}${esc(ctx.shortUrl(source.url))}</a>`;
  }
  function sourceItem(k,source) {
    const remove=actionAttrs('src-del',k.id,esc);
    return `<span class="srcitem">${sourceContent(source)}<button type="button" class="x" ${remove}`+
      ` data-source-id="${esc(source.id)}" aria-label="${t('Убрать источник')}">✕</button></span>`;
  }
  function cubeSources(k) {
    const links=k.links||[],count=links.length||'';
    const chips=links.map(source=>sourceItem(k,source)).join('');
    const list=links.length?`<div class="srclist">${chips}</div>`:'';
    const add=actionAttrs('src-add',k.id,esc);
    return `<div class="srcs"><h4>${t('Источники')} <span>${count}</span></h4>`+gap6+list+gap6+
      `<div class="srcadd"><input data-src-input="${esc(k.id)}" placeholder="${t('Ссылка или короткая заметка — Enter')}"`+
      ` autocomplete="off"><button type="button" class="btn small" ${add}>${t('Добавить')}</button></div></div>`;
  }
  function cubeActions(k) {
    const unplace=k.day!=null&&k.pct<100?actionButton('cube-unplace',k.id,t('Убрать из дня')):'';
    const reopen=k.pct>=100?actionButton('cube-reopen',k.id,t('Вернуть в работу')):'';
    const label=k.repeatable_id?t('Открыть повторяемый кубик'):t('Сделать повторяемым');
    const repeatable=actionButton('cube-repeatable',k.id,label);
    const remove=actionButton('cube-delete',k.id,t('Удалить кубик'),'btn danger');
    return `<div class="gc-actions">${unplace}${reopen}${repeatable}<span class="sp"></span>`+
      remove+modalClose('btn',t('Закрыть'))+'</div></form>';
  }
  function cubeEditor(m) {
    const k=ctx.cube(m.id);
    if(!k)return '';
    const g=k.goal&&ctx.goal(k.goal);
    const source=esc(k.src||'');
    const origin=g?`${t('Кубик ·')} ${esc(g.title)}`:`${t('Кубик · из «')}${source}»`;
    const titleBinding=fieldAttrs('cube.title',k.id,esc);
    const timer=(k.day!=null||k.time_retained)&&ctx.timerControls?ctx.timerControls(k):'';
    const ready=ctx.seg('cube',k.id,k.pct,5);
    return `<form class="modal" data-form="none" style="--c:${ctx.colorOf(k)}"><div class="mh">`+
      `<span class="type">${origin}</span>${modalClose()}</div>`+gap5+
      `<input class="gc-title" ${titleBinding} value="${esc(k.title)}" aria-label="${t('Название кубика')}">`+gap5+
      `<div class="ready"><label>${t('Готовность')}</label>${ready}</div>`+gap5+timer+gap5+
      cubeFields(k,g)+gap5+`<p class="muted">${cubePlacement(k,g)}</p>`+gap5+cubeSources(k)+gap5+cubeActions(k);
  }
  function goalEditor(m) {
    const goal=ctx.goal(m.id);
    return goal?`<div class="modal wide" style="--c:${ctx.goalColor(goal)}">${ctx.goalCard(goal)}</div>`:'';
  }
  const modalViews={
    'repeatable':m=>ctx.repeatableEditor(m.id),'repeatable-instances':()=>ctx.repeatableInstancesEditor(),
    'repeatable-new':m=>ctx.repeatableNewEditor(m.cubeId),'timer':()=>ctx.timerModal(),
    'project-new':newProject,'project-edit':projectEditor,'goal-new':newGoal,'cube-new':newCube,
    'cube':cubeEditor,'goal':goalEditor,
  };
  function modalHtml() {
    const modal=U.modal;
    if(!modal)return '';
    const body=modalViews[modal.type]?.(modal);
    return body?`<div class="overlay" data-overlay>${body}</div>`:'';
  }
  function goalMenu(g) {
    const manual=!ctx.readinessCubes(g).length;
    const completion=ctx.isDone(g)?['goal-reopen',t('Вернуть в работу')]:['goal-done',t('Готово — на полку')];
    return [
      ['open-goal',t('Открыть')],manual?completion:null,['new-cube',t('+ Кубик в цель')],
      ctx.isDone(g)?null:['goal-hide',g.hidden?t('Показать на неделе'):t('Скрыть с недели')],
      ['goal-delete',t('Удалить цель'),'danger'],
    ];
  }
  function cubeMenu(k) {
    const completion=k.pct>=100?['cube-reopen',t('Вернуть в работу')]:['cube-done',t('Готово')];
    const unplace=k.day!=null&&k.pct<100?['cube-unplace',t('Убрать из дня')]:null;
    const label=k.repeatable_id?t('Открыть повторяемый кубик'):t('Сделать повторяемым');
    // Owner 2026-10-28: «⧉» adds another part of the same cube (its minutes add up); Ctrl+drag does the same at a chosen time.
    const part=k.goal&&k.pct<100?['cube-part','⧉ '+t('Ещё часть')]:null;
    return [['open-cube',t('Открыть')],completion,unplace,part,['cube-repeatable',label],['cube-delete',t('Удалить кубик'),'danger']];
  }
  function menuContent(m) {
    if(m.kind==='day') {
      const day=+m.id;
      return {title:`${DOWF[wd(day)]} · ${ddmm(day)}`,items:[
        ['go-day',t('Перейти в день')],['new-cube-day',t('+ Кубик в этот день')],['new-goal',t('+ Цель с этого дня')],
      ]};
    }
    if(m.kind==='goalblock') {
      const [id,s,e]=m.id.split('|'),g=ctx.goal(id);
      return g?{title:`${g.title} · ${ddmm(+s)}${s===e?'':'–'+ddmm(+e)}`,items:[['goal-block-remove',t('Убрать блок'),'danger']]}:null;
    }
    if(m.kind==='repeatable') {
      const item=ctx.repeatable(m.id);
      return item?{title:item.title,items:[
        ['repeatable-edit',t('Открыть')],['repeatable-delete',t('Удалить повторяемый кубик'),'danger'],
      ]}:null;
    }
    const item=m.kind==='goal'?ctx.goal(m.id):ctx.cube(m.id);
    if(!item)return null;
    const items=m.kind==='goal'?goalMenu(item):cubeMenu(item);
    return {title:item.title,items};
  }
  function menuItem([action,label,className],id) {
    const css=className||'',binding=actionAttrs(action,id,esc);
    return `<button role="menuitem" class="${css}" ${binding}>${label}</button>`;
  }
  function menuHtml() {
    const menu=U.menu;
    if(!menu)return '';
    const content=menuContent(menu);
    if(!content)return '';
    const items=content.items.filter(Boolean);
    const x=Math.min(menu.x,innerWidth-215),y=Math.min(menu.y,innerHeight-items.length*34-46);
    const buttons=items.map(item=>menuItem(item,menu.id)).join('');
    return `<div class="menu" role="menu" style="left:${x}px;top:${y}px"><div class="mt">${esc(content.title)}</div>`+
      buttons+'</div>';
  }
  function quickProject(cur) {
    if(S.projects.length)return `<select name="p" aria-label="${t('Проект')}">${projectOptions(cur)}</select>`;
    return `<input name="newp" required placeholder="${t('Проект, например «Ремонт»')}" autocomplete="off">`+
      `<select name="newc" required aria-label="${t('Цвет проекта')}"><option value="">${t('Выберите цвет календаря')}</option>`+
      paletteOptions()+'</select>';
  }
  function quickHtml() {
    const q=U.quick;
    if(!q)return '';
    const project=[U.lastP,U.sel&&ctx.goal(U.sel)?.p,S.projects[0]?.id].find(p=>p&&ctx.proj(p))||'';
    const x=Math.max(8,Math.min(q.x-290,innerWidth-290)),y=Math.max(8,Math.min(q.y-20,innerHeight-200));
    const title=q.s===q.e?`${t('Цель на')} ${dow(q.s)} ${ddmm(q.s)}`:
      `${t('Цель на неделю')} ${ddmm(q.s)}–${ddmm(q.e)}`;
    return `<form class="quick" data-form="quick-goal" style="left:${x}px;top:${y}px"><div class="qh"><b>${title}</b>`+
      `<button type="button" class="x" data-act="quick-close" aria-label="${t('Закрыть')}">✕</button></div>\n   `+
      `<input name="title" required placeholder="${t('Название цели')}" autocomplete="off">\n   `+
      quickProject(project)+'\n   <div class="qa">'+
      `<small>${t('Enter — на стрелу. Кубики и детали — потом, на Неделе.')}</small>`+
      `<button class="btn primary small">${t('Создать')}</button></div></form>`;
  }
  function openModal(modal) {
    U.modal=modal;
    U.menu=null;
    ctx.render();
    setTimeout(()=>ctx.$('#layer [name=title],#layer [name=name]')?.focus(),0);
  }
  Object.assign(ctx,{durOptions,goalOptions,swatches,modalHtml,menuHtml,quickHtml,openModal});
}
