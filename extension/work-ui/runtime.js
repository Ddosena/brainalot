import {t,localeTag,translateServerMessage} from '../i18n.js';
import {patchMarkup} from './dom.js';
import {adaptModel} from './model-adapter.js';
import {installCubes} from './cubes.js';
import {installWeek} from './week.js';
import {installArrow} from './arrow.js';
import {installDay} from './day.js';
import {installClock} from './clock.js';
import {installDialogs} from './dialogs.js';
import {installDrag} from './drag.js';
import {installShell} from './shell.js';
import {installRepeatables,repeatableCube,REPEATABLE_PREFIX} from './repeatables.js';
import {installPool} from './pool.js';
import {installTimer} from './timer.js';

export function createWorkRuntime({commands,screen='week',date=null,baseDate,document:doc=globalThis.document,window:win=globalThis.window,
onNavigate=()=>{},onTheme=()=>{},onIdle=()=>{}}={}) {
  const DAY_MS=86400000, now=new Date(), todayUTC=Date.UTC(now.getFullYear(),now.getMonth(),now.getDate());
  const BASE=baseDate??todayUTC-((new Date(todayUTC).getUTCDay()+6)%7)*DAY_MS,TODAY=Math.round((todayUTC-BASE)/DAY_MS);
  const S={started:false,projects:[],goals:[],cubes:[],events:[],repeatables:[],calendarPalette:[]};
  const U={screen,day:TODAY,weekStart:Math.floor(TODAY/7)*7,sel:null,poolGoal:null,ppd:22,modal:null,menu:null,started:false};
  const D={ARROW_FROM:0,SCALER_H:560,scaling:null,bdrag:null,gdrag:null,pdrag:null,rdrag:null,ghost:null,ghostEl:null,overEl:null,suppressClick:false};
  let pending=0,ready=false,theme='midnight',mode='dark',vscrollMemo=null,lastError='',lastRenderMs=0,acceptedFingerprint=null;
  const renderTimes=[];let renderCount=0,maxRenderMs=0;
  const listeners=[],timers=new Set(),dirtyFields=new Set(),inflight=new Set(),readinessPending=new Map();
  const pad=n=>String(n).padStart(2,'0'),dayDate=i=>new Date(BASE+i*DAY_MS),dd=i=>pad(dayDate(i).getUTCDate());
  const ddmm=i=>dd(i)+'.'+pad(dayDate(i).getUTCMonth()+1),wd=i=>((i%7)+7)%7,weekOf=i=>Math.floor(i/7)*7;
  const DOW=[t('пн'),t('вт'),t('ср'),t('чт'),t('пт'),t('сб'),t('вс')],DOWF=[t('Понедельник'),t('Вторник'),
t('Среда'),t('Четверг'),t('Пятница'),t('Суббота'),t('Воскресенье')];
  const dow=i=>DOW[wd(i)],monthName=i=>dayDate(i).toLocaleDateString(localeTag(),{month:'long',timeZone:'UTC'}),iso=i=>i==null?null:
dayDate(i).toISOString().slice(0,10);
  const fromIso=s=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(s||''))return NaN;const n=Math.round((Date.parse(s+'T00:00:00Z')-BASE)/DAY_MS);return iso(n)===s?n:NaN;};
  const clk=m=>pad(Math.floor(m/60)%24)+':'+pad(m%60);
  function hm(m){
    if(m<60)return m+' '+t('мин');
    const remainder=m%60?' '+m%60+' '+t('мин'):'';
    return Math.floor(m/60)+' '+t('ч')+remainder;
  }
  function dur(m){
    if(m<60)return m+' '+t('мин');
    const hours=new Intl.NumberFormat(localeTag(),{maximumFractionDigits:1}).format(Math.round(m/6)/10);
    return hours+' '+t('ч');
  }
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cssId=value=>globalThis.CSS?.escape?globalThis.CSS.escape(String(value)):String(value).replace(/["\\]/g,'\\$&');
  const uid=prefix=>prefix+globalThis.crypto.randomUUID(),clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),nowMinute=()=>{const d=new Date();
return d.getHours()*60+d.getMinutes();},$=s=>doc.querySelector(s);
  const proj=id=>S.projects.find(p=>p.id===id),goal=id=>S.goals.find(g=>g.id===id),cube=id=>S.cubes.find(k=>k.id===id)||(typeof id==='string'&&
id.startsWith(REPEATABLE_PREFIX)?repeatableCube(S.repeatables.find(item=>item.id===id.slice(REPEATABLE_PREFIX.length))):null);
  const goalColor=g=>proj(g?.p)?.c||'#888',colorOf=k=>k.isRepeatableTemplate||k.repeatable_instance?'var(--accent)':k.goal?goalColor(goal(k.goal)):
'var(--possible)',isDone=g=>g.pct>=100,visible=g=>!proj(g.p)?.hidden;
  const eventsFor=i=>S.events.filter(e=>e.day===i&&e.at!=null&&e.min>0).map(e=>({...e,fixed:true}));
  const dayCubes=i=>S.cubes.filter(k=>k.day===i),dayLoad=i=>dayCubes(i).reduce((a,k)=>a+k.min,0),shelf=gid=>S.cubes.filter(k=>k.goal===gid&&
k.day==null&&k.pct<100),activeOn=i=>S.goals.filter(g=>!isDone(g)&&g.s<=i&&i<=g.e);
  const timedOn=i=>[...eventsFor(i),...S.cubes.filter(k=>k.day===i&&k.at!=null)];
  const conflict=(i,at,min,except)=>{const ex=new Set([].concat(except||[]));return timedOn(i).find(x=>!ex.has(x.id)&&at<x.at+x.min&&x.at<at+min);};
  function listen(target,type,callback,options){target.addEventListener(type,callback,options);listeners.push(()=>target.removeEventListener(type,
callback,options));}
  function timer(fn,ms){const id=setTimeout(()=>{timers.delete(id);fn();},ms);timers.add(id);return id;}
  function saveUI(){try{win.sessionStorage.setItem('work:view',JSON.stringify({ppd:U.ppd,started:U.started}));}catch{}}
  try{const saved=JSON.parse(win.sessionStorage.getItem('work:view'));U.ppd=clamp(saved?.ppd||22,5,40);U.started=!!saved?.started;}catch{}
  if(date){const initial=fromIso(date);if(Number.isFinite(initial)){U.day=initial;U.weekStart=weekOf(initial);}}
  function activityOptions(value){
    return [['work',t('Работа')],['personal',t('Дело')]].map(([key,label])=>{
      const selected=value===key?' selected':'';
      return `<option value="${key}"${selected}>${label}</option>`;
    }).join('');
  }
  const ctx={S,U,D,BASE,TODAY,DAY_MS,DAY_FROM:480,DAY_SPAN:960,CUBE_MAX:240,VPX:30,HALF:720,RING:112,RW:58,FW:76,NUMR:168,DURS:[15,30,45,60,75,90,120,
150,180,210,240],PPD_MIN:5,PPD_MAX:40,pad,dayDate,dd,ddmm,DOW,DOWF,wd,dow,monthName,iso,fromIso,weekOf,clk,hm,dur,esc,cssId,uid,clamp,nowMinute,$,
proj,goal,cube,goalColor,colorOf,isDone,visible,eventsFor,dayCubes,dayLoad,shelf,activeOn,timedOn,conflict,saveUI,listen,timer,activityOptions};
  const isDragging=()=>!!(D.pdrag||D.rdrag||D.bdrag||D.gdrag||D.scaling);
  function paletteColors(){return S.calendarPalette.map(c=>c.color||c.hex).filter(Boolean);}
  function calendarPoints(){return S.projects.filter(p=>!p.hidden).flatMap(p=>(p.calendarPoints||[]).map(e=>({...e,project:p.id,color:p.c})));}
  function swatchChoices(){return S.calendarPalette;}
  const paletteNames={'#d50000':t('Томат'),'#e67c73':t('Фламинго'),'#ad1457':t('Радиккио'),'#d81b60':
t('Цветок вишни'),'#f4511e':t('Мандарин'),'#ef6c00':t('Тыква'),'#f09300':t('Манго'),'#f6bf26':t('Банан'),'#e4c441':
t('Цитрон'),'#c0ca33':t('Авокадо'),'#7cb342':t('Фисташка'),'#33b679':t('Шалфей'),'#0b8043':t('Базилик'),'#009688':
t('Эвкалипт'),'#039be5':t('Павлин'),'#4285f4':t('Кобальт'),'#3f51b5':t('Черника'),'#7986cb':t('Лаванда'),'#b39ddb':
t('Глициния'),'#9e69af':t('Аметист'),'#8e24aa':t('Виноград'),'#795548':t('Какао'),'#a79b8e':t('Берёза'),'#616161':
t('Графит')};
  const paletteName=entry=>entry.standard&&paletteNames[entry.color?.toLowerCase()]||entry.name||entry.color||entry.hex;
  function toast(text,undo=false){const host=$('#toast');if(!host)return;$('#toastText').textContent=text;$('#toastUndo').hidden=!undo;
host.hidden=false;clearTimeout(toast.timer);toast.timer=timer(()=>{host.hidden=true;},undo?6500:3500);}
  function renderLayer(){patchMarkup($('#layer'),ctx.modalHtml()+ctx.menuHtml()+ctx.quickHtml());
for(const field of dirtyFields)if(!field.isConnected)dirtyFields.delete(field);}
  function renderContent(startedPlan){
    if(!ready){
      const message=esc(lastError||t('Загрузка плана…'));
      return `<section class="panel start"><p>${message}</p></section>`;
    }
    if(!startedPlan)return ctx.startScreen();
    return U.screen==='week'?ctx.weekScreen()+ctx.projectsPanel():ctx.dayScreen();
  }
  function restoreScroll(arrowTop){
    const nav=$('#av');
    if(nav)nav.scrollTop=arrowTop??Math.max(0,(TODAY-D.ARROW_FROM)*U.ppd-nav.clientHeight/3);
    const nvs=$('#vscroll');
    if(nvs)nvs.scrollTop=vscrollMemo&&vscrollMemo.day===U.day?vscrollMemo.top:U.day===TODAY?Math.max(0,(nowMinute()-480)/60*30-120):60;
  }
  function render(){
    const started=performance.now(),av=$('#av'),arrowTop=av?.scrollTop,vs=$('#vscroll');
    if(vs)vscrollMemo={day:+$('#vtrack')?.dataset.day,top:vs.scrollTop};
    doc.body.classList.remove('graphite','midnight','contrast','glamour','dark','light');doc.body.classList.add(theme,mode);
    const startedPlan=S.hasStandalone||S.planning_initialized!==false&&(S.started||U.started);
    const dayClass=U.screen==='day'||!startedPlan?' dayapp':'';
    patchMarkup($('#stage'),`<div class="app${dayClass}">${ctx.appbar()}${renderContent(startedPlan)}</div>`);
    ctx.placeThumb();restoreScroll(arrowTop);renderLayer();ctx.paintTimer?.();
    lastRenderMs=performance.now()-started;renderTimes.push({sequence:++renderCount,ms:lastRenderMs});
    if(renderTimes.length>100)renderTimes.shift();maxRenderMs=Math.max(maxRenderMs,lastRenderMs);
  }
  function paintDrop(){if($('#clockhost'))patchMarkup($('#clockhost'),ctx.clockSvg(U.day));for(const [id,html]of [['htrack',ctx.hTrack],['vtrack',
ctx.vTrack]]){const old=$('#'+id);if(old){const wrap=doc.createElement('div');wrap.innerHTML=html(U.day);const next=wrap.firstElementChild;
patchMarkup(old,next.innerHTML);old.setAttribute('style',next.getAttribute('style')||'');}}}
  function fly(fromEl,toSel,label){const to=$(toSel);if(!fromEl||!to)return;const a=fromEl.getBoundingClientRect(),b=to.getBoundingClientRect(),
f=doc.createElement('div');f.className='fly';f.textContent='✓ '+label;f.style.left=a.left+'px';f.style.top=a.top+'px';doc.body.append(f);
requestAnimationFrame(()=>requestAnimationFrame(()=>{const x=b.left+16-a.left,y=b.top+8-a.top;f.style.transform=`translate(${x}px,${y}px) scale(.7)`;
f.style.opacity='.15';}));timer(()=>f.remove(),700);}
  async function send(type,payload,message='',success){
    pending++;
    const operation=commands.execute({type,payload});inflight.add(operation);
    try{const result=await operation;if(result?.model)ctx.acceptModel(adaptModel(result.model,{baseDate:BASE}));lastError='';await success?.(result);
const warnings=(result?.warnings||[]).map(w=>translateServerMessage(typeof w==='string'?w:w.message||w.code||''));toast([message||
t('Сохранено'),...warnings].filter(Boolean).join('\n'),!!result?.undo);return result;}
    catch(error){lastError=error.message||String(error);toast(lastError);return false;}
    finally{inflight.delete(operation);pending--;render();onIdle();}
  }
  function openGoal(id,toggle){if(!goal(id))return;U.poolGoal=id;if(U.screen==='day')U.modal={type:'goal',id};else U.sel=toggle&&U.sel===id?null:id;}
  function pickGoal(id){openGoal(id,false);}
  function goalDone(id){const g=goal(id);if(!g)return;fly($(`.gc[data-goal="${cssId(id)}"]`)||$(`.gband[data-goal="${cssId(id)}"]`),'#goalShelf',
g.title);return send('goal.done',{id,done:true},t('Цель готова'),()=>{if(U.sel===id)U.sel=null;if(U.modal?.id===id)U.modal=null;});}
  const trophyTarget=k=>U.screen==='day'?'#doneShelf':'.day[data-day="'+(k.day??TODAY)+'"]';
  async function cubeDone(id){const k=cube(id);if(!k)return;if(!(await ctx.timerBeforeComplete(id)))return false;
    fly($(`[data-cube="${cssId(id)}"]`),trophyTarget(k),k.title);
    return send('cube.update',{id,readiness_pct:100},t('Кубик готов'));
  }
  async function addSource(id,raw){const k=cube(id),text=String(raw||'').trim();if(!k||!text)return;const url=/^https?:\/\/\S+$/i.test(text)?text:
/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(text)?'https://'+text:null;const result=await send('cube.update',{id,sources:[...(k.links||[]),{id:uid('s'),url,
text:url?null:text}]},t('Источник добавлен'));if(result){const input=$(`[data-src-input="${cssId(id)}"]`);if(input){input.value='';
delete input.dataset.workDirty;input.focus();}}}
  function onSeg(btn){
    const meter=btn.parentElement,n=+meter.dataset.n,id=meter.dataset.segId,item=cube(id);if(!item)return;
    const previous=readinessPending.get(id),displayed=previous?.pct??item.pct;
    const k=[...meter.querySelectorAll('button')].indexOf(btn),was=Math.round(displayed/(100/n)),next=k+1===was?k:k+1,pct=Math.round(next*100/n);
    meter.querySelectorAll('button').forEach((b,j)=>{
      b.style.setProperty('--d',Math.abs(j-Math.min(was,next))*55+'ms');b.classList.toggle('on',j<next);
    });
    meter.querySelector('output').textContent=pct+'%';meter.classList.toggle('full',pct>=100);
    if(previous){clearTimeout(previous.timer);timers.delete(previous.timer);inflight.delete(previous.promise);previous.resolve();pending--;}
    let resolve;const promise=new Promise(done=>{resolve=done;});pending++;inflight.add(promise);
    const entry={pct,promise,resolve,timer:null};readinessPending.set(id,entry);
    entry.timer=timer(async()=>{
      readinessPending.delete(id);pending--;
      if(pct>=100)fly(meter.closest('.modal')||meter,trophyTarget(item),item.title);
      try{if(pct>=100&&!(await ctx.timerBeforeComplete(id))){render();return;}await send('cube.update',{id,readiness_pct:pct},pct>=100?
t('Кубик готов'):t('Готовность сохранена'),()=>{if(pct>=100&&U.modal?.id===id)U.modal=null;});}
      finally{inflight.delete(promise);resolve();}
    },650);
  }
  Object.assign(ctx,{paletteColors,calendarPoints,swatchChoices,paletteName,toast,renderLayer,render,paintDrop,fly,send,openGoal,pickGoal,goalDone,
cubeDone,addSource,onSeg,idle:onIdle,currentTheme:()=>({theme,mode})});
  installShell(ctx);installCubes(ctx);installPool(ctx);installRepeatables(ctx);installTimer(ctx);installWeek(ctx);installArrow(ctx);installDay(ctx);
installClock(ctx);installDialogs(ctx);
  // Chrome/native routes and both views share exactly these actions and editors.
  installActions(ctx,{commands,onNavigate,onTheme,isDragging,dirtyFields});
  installDrag(ctx);
  ctx.acceptModel=value=>{const key=JSON.stringify(value);if(key===acceptedFingerprint)return;acceptedFingerprint=key;Object.assign(S,value);
ready=true;lastError='';if(!isDragging()&&!pending)render();};
  ctx.acceptTheme=value=>{theme=value.theme;mode=value.mode;if(!isDragging())render();};
  ctx.reportError=error=>{lastError=error.message||String(error);if(ready)toast(lastError);else render();};
  ctx.isPaused=()=>pending>0||inflight.size>0||isDragging()||dirtyFields.size>0;
  ctx.isDragging=isDragging;
  ctx.ready=()=>ready;
  ctx.flush=async()=>{if(isDragging())ctx.cancelDrags();await Promise.allSettled([...inflight]);await commands.flush?.();await ctx.timerFlush?.();
for(const field of dirtyFields)if(!field.isConnected)dirtyFields.delete(field);if(pending||
dirtyFields.size)throw new Error(t('Дождитесь сохранения изменений.'));};
  ctx.stats=()=>({lastRenderMs,maxRenderMs,renderCount,renderTimes:[...renderTimes],pending,dirtyFields:dirtyFields.size});
  const hand=setInterval(()=>{if(!isDragging()&&!doc.hidden)paintDrop();},20000);listeners.push(()=>clearInterval(hand));
  let resizeTimer;listen(win,'resize',()=>{clearTimeout(resizeTimer);resizeTimer=timer(()=>{if(!isDragging())render();},150);});
  ctx.destroy=()=>{ctx.timerDestroy?.();listeners.forEach(fn=>fn());timers.forEach(clearTimeout);clearTimeout(toast.timer);ctx.cancelDrags();};
  render();return ctx;
}

function installActions(ctx,{commands,onNavigate,onTheme,isDragging,dirtyFields}) {
  const {S,U,D,TODAY,$,goal,cube,proj,weekOf,fromIso,iso,clamp,esc,cssId,send,render,renderLayer,toast,listen}=ctx;
  const close=()=>{U.modal=null;};
  const explicitActivity=new WeakSet();
  let starting=false;
  const navigate=screen=>{if(onNavigate(screen,iso(U.day))!==false)U.screen=screen;};
  const ACTS={
    tab:navigate,today:()=>{U.day=TODAY;U.weekStart=weekOf(TODAY);},
    'week-prev':()=>{U.weekStart-=7;U.day-=7;},'week-next':()=>{U.weekStart+=7;U.day+=7;},'week-today':()=>{U.weekStart=weekOf(TODAY);U.day=TODAY;},
    'pick-day':a=>{U.day=+a;U.weekStart=weekOf(U.day);},'open-day':()=>navigate('day'),
    'go-day':a=>{U.day=+a;U.weekStart=weekOf(U.day);navigate('day');scrollTo(0,0);},
    'new-cube-day':a=>{U.day=+a;ctx.openModal({type:'cube-new',where:'day'});},
    'day-prev':()=>{U.day--;U.weekStart=weekOf(U.day);},'day-next':()=>{U.day++;U.weekStart=weekOf(U.day);},
    'new-project':()=>ctx.openModal({type:'project-new'}),
    'new-goal':a=>{const s=a!=null&&a!==''?+a:Math.max(U.day,U.screen==='week'?U.weekStart:U.day);ctx.openModal({type:'goal-new',s,e:s+2,p:U.sel&&
goal(U.sel)?.p});},
    'new-cube':a=>ctx.openModal({type:'cube-new',goal:a||null,where:!a&&U.screen==='day'?'day':'shelf'}),
    'open-goal':a=>ctx.openGoal(a,false),'pick-goal':a=>ctx.pickGoal(a),'close-goal':()=>{if(U.modal?.type==='goal')close();else U.sel=null;},
    'goal-done':a=>ctx.goalDone(a),'goal-reopen':a=>send('goal.done',{id:a,done:false}),
    'goal-delete':a=>send('goal.delete',{id:a},t('Цель удалена вместе с кубиками'),()=>{if(U.sel===a)U.sel=null;
if(U.modal?.id===a)close();}),
    'open-cube':a=>ctx.openModal({type:'cube',id:a}),'cube-done':a=>ctx.cubeDone(a),'cube-reopen':a=>send('cube.update',{id:a,readiness_pct:80},
t('Кубик снова в работе')),
    'cube-unplace':a=>send('cube.return',{id:a},t('Кубик возвращён на полку')),
    'cube-delete':a=>send('cube.delete',{id:a},t('Кубик удалён'),()=>{if(U.modal?.id===a)close();}),
    'cube-pin':a=>send('cube.pin',{id:a,pinned:!cube(a)?.pinned},'',()=>ctx.timer(()=>$(`[data-act="cube-pin"][data-arg="${cssId(a)}"]`)?.focus(),0)),
    'cube-repeatable':a=>ctx.saveCubeRepeatable(a),'repeatable-edit':a=>ctx.openModal({type:'repeatable',id:a}),
    'repeatable-instances':()=>ctx.openModal({type:'repeatable-instances'}),
    'repeatable-delete':a=>send('repeatable.delete',{repeatable_id:a},t('Повторяемый кубик удалён'),close),
    'timer-open':()=>ctx.timerOpen(),'timer-toggle':a=>ctx.timerToggle(a),'timer-play':a=>ctx.timerPlay(a),'timer-pause':()=>ctx.timerPause(),
'timer-stop':()=>ctx.timerStop(),'timer-rest':a=>ctx.timerRest(a),'timer-preset':()=>ctx.timerSavePreset(),'timer-retry':()=>ctx.timerRetry(),
    'modal-close':close,'quick-close':()=>{U.quick=null;},'src-add':a=>ctx.addSource(a,$(`[data-src-input="${cssId(a)}"]`)?.value||''),
    'src-del':(id,el)=>send('cube.update',{id,sources:(cube(id)?.links||[]).filter(l=>l.id!==el.dataset.sourceId)},t('Источник удалён')),
    start:async()=>{if(starting)return;starting=true;try{if(S.planning_initialized===false){await send('plan.start',{timezone:
Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'},'',()=>{U.started=true;ctx.saveUI();});}else{U.started=true;ctx.saveUI();render();
}}finally{starting=false;}},
    'zoom-month':()=>{const av=$('#av'),center=(av.scrollTop+av.clientHeight/2)/U.ppd;U.ppd=clamp((av.clientHeight-40)/31,5,40);ctx.saveUI();render();
const n=$('#av');n.scrollTop=center*U.ppd-n.clientHeight/2;return 'done';},
    'project-edit':a=>ctx.openModal({type:'project-edit',id:a}),
    'project-delete':a=>send('project.delete',{id:a},t('Проект удалён'),()=>{close();if(!goal(U.sel))U.sel=null;}),
    'arrow-jump':(a,el,e)=>{const r=el.getBoundingClientRect(),i=Math.floor((e.clientY-r.top-28)/U.ppd)+D.ARROW_FROM;U.weekStart=weekOf(i);U.day=i;},
    theme:a=>onTheme({theme:a}),mode:a=>onTheme({mode:a}),
  };
  listen(document,'click',e=>{
    if(D.suppressClick){D.suppressClick=false;e.preventDefault();return;}
    if(e.target.id==='toastUndo'){void commands.undo().then(()=>{toast(t('Отменено'));render();}).catch(error=>toast(error.message));return;}
    if(e.target.matches('[data-overlay]')){close();renderLayer();return;}
    const segBtn=e.target.closest('.seg button');if(segBtn)return ctx.onSeg(segBtn);
    const button=e.target.closest('[data-act]'),hadMenu=!!U.menu;U.menu=null;
    if(!button||!ACTS[button.dataset.act]){if(hadMenu)renderLayer();return;}
    const r=ACTS[button.dataset.act](button.dataset.arg,button,e);if(r!=='done'){ctx.saveUI();render();}
  });
  listen(document,'dblclick',e=>{const day=e.target.closest('.day[data-day]');if(day){U.day=+day.dataset.day;navigate('day');render();return;
}const wk=e.target.closest('.wk');if(wk&&!e.target.closest('.gband')){const r=wk.getBoundingClientRect(),
col=clamp(Math.floor((e.clientX-r.left)/r.width*7),0,6);ACTS['new-goal'](String(U.weekStart+col));}});
  listen(document,'contextmenu',e=>{
    if(e.target.closest('input,select,textarea'))return;
    const arrow=e.target.closest('.arrow');if(arrow&&!e.target.closest('[data-goal]')){e.preventDefault();const r=arrow.getBoundingClientRect(),
day=Math.floor((e.clientY-r.top-28)/U.ppd)+D.ARROW_FROM,byDay=U.ppd>=12;U.quick={s:byDay?day:weekOf(day),e:byDay?day:weekOf(day)+6,x:e.clientX,y:
e.clientY};U.menu=null;render();ctx.timer(()=>$('.quick [name=title]')?.focus(),0);return;}
    const cell=e.target.closest('.day[data-day]'),wk=!e.target.closest('[data-goal]')&&e.target.closest('.wk');if(cell||wk){e.preventDefault();
let day=cell?+cell.dataset.day:null;if(wk){const r=wk.getBoundingClientRect();day=U.weekStart+clamp(Math.floor((e.clientX-r.left)/r.width*7),0,6);
}U.menu={kind:'day',id:String(day),x:e.clientX,y:e.clientY};renderLayer();return;}
    const c=e.target.closest('[data-cube]'),g=e.target.closest('[data-goal]');if(!c&&!g)return;e.preventDefault();const template=c&&
cube(c.dataset.cube);U.menu={kind:template?.isRepeatableTemplate?'repeatable':c?'cube':'goal',id:template?.isRepeatableTemplate?template.repeatableId:
c?c.dataset.cube:g.dataset.goal,x:e.clientX,y:e.clientY};renderLayer();
  });
  listen(document,'keydown',e=>{
    if(e.target.matches('[data-scaler]')&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();const av=$('#av'),day=(av.scrollTop+av.clientHeight/2)/U.ppd;
      U.ppd=e.key==='Home'?5:e.key==='End'?40:clamp(U.ppd+(['ArrowUp','ArrowRight'].includes(e.key)?1:-1),5,40);
      ctx.saveUI();render();const next=$('#av');next.scrollTop=day*U.ppd-next.clientHeight/2;return;
    }
    if((e.key==='Enter'||e.key===' ')&&e.target.matches('[role=button][tabindex],[role=group][data-cube][tabindex]')){e.preventDefault();
if(e.target.matches('.gband')){ctx.openGoal(e.target.dataset.goal,true);render();}else e.target.click();return;}
    if(e.key==='Enter'&&e.target.matches('[data-src-input]')){e.preventDefault();void ctx.addSource(e.target.dataset.srcInput,e.target.value);return;}
    if(e.key!=='Escape')return;if(U.quick){U.quick=null;render();return;}if(isDragging()){ctx.cancelDrags();return;}if(U.menu||U.modal){U.menu=null;
close();renderLayer();}
  });
  listen(document,'input',e=>{if(e.target.name==='activity_kind')explicitActivity.add(e.target);
if(e.target.matches('[data-field],[name],[data-src-input],[data-time-preset]')){e.target.dataset.workDirty='1';
if(e.target.dataset.field)dirtyFields.add(e.target);}});
  listen(document,'change',async e=>{
    if(e.target.matches('[data-pool-goal]')){U.poolGoal=e.target.value;render();return;}
    if(e.target.name==='goal'){const activity=e.target.form?.querySelector('[name=activity_kind]');if(activity&&
!explicitActivity.has(activity)){activity.value=e.target.value?'work':'personal';activity.dataset.workDirty='1';}return;}
    const el=e.target,f=el.dataset.field,id=el.dataset.id;if(!f)return;const v=el.value;let type,p={id};
    if(f.startsWith('project.')){type='project.update';if(f==='project.show')p.hidden=!el.checked;else if(f==='project.name')p.name=v.trim()||
proj(id).name;else p.color=v;}
    if(f.startsWith('repeatable.')){type='repeatable.save';p={repeatable_id:id};if(f==='repeatable.title')p.title=v.trim()||ctx.repeatable(id)?.title;
if(f==='repeatable.minutes')p.minutes=+v;if(f==='repeatable.activity_kind')p.activity_kind=v;}
    if(f.startsWith('goal.')){type='goal.update';const g=goal(id);if(f==='goal.title')p.title=v.trim()||g.title;if(f==='goal.p')p.project_id=v;if(f===
'goal.h'){p.planned_minutes=Math.max(0,Math.round((+v||0)*2)/2)*60;p.auto_volume=false;}if(f==='goal.due')p.deadline_date=v||null;if(f==='goal.s'||
f==='goal.e'){let s=f==='goal.s'?fromIso(v):g.s,en=f==='goal.e'?fromIso(v):g.e;if(!Number.isFinite(s)||!Number.isFinite(en))return;if(s>en){if(f===
'goal.s')en=s;else s=en;}p.start_date=iso(s);p.end_date=iso(en);}}
    if(f.startsWith('cube.')){type='cube.update';const k=cube(id);if(f==='cube.title')p.title=v.trim()||k.title;if(f==='cube.goal')p.goal_id=v;if(f===
'cube.min')p.duration_minutes=+v;if(f==='cube.activity_kind')p.activity_kind=v;if(f==='cube.day'){p.selected_date=v||null;if(v&&k.at!=null&&
ctx.conflict(fromIso(v),k.at,k.min,id))p.start_minute=null;}if(f==='cube.at'){p.start_minute=v?Number(v.slice(0,2))*60+Number(v.slice(3)):null;}}
    if(!type)return;const result=await send(type,p);if(result&&f==='cube.activity_kind')await ctx.timerCategoryChanged?.(id);
if(result){delete el.dataset.workDirty;dirtyFields.delete(el);}else{el.dataset.workDirty='1';dirtyFields.add(el);}render();
  });
  async function ensureProject(fd){const name=String(fd.get('newp')||'').trim();if(!name)return String(fd.get('p')||'');
const color=String(fd.get('newc')||'');if(!color){toast(t('Выберите цвет календаря для нового проекта.'));
return null;}const before=new Set(S.projects.map(p=>p.id)),r=await send('project.create',{name,color});if(!r)return null;
const added=S.projects.filter(p=>!before.has(p.id));return added.length===1?added[0].id:null;}
  listen(document,'submit',async e=>{
    e.preventDefault();const form=e.target,type=form.dataset.form,fd=new FormData(form);if(type==='none')return;
const button=form.querySelector('button:not([type="button"])');if(button)button.disabled=true;
    try{
      if(type==='repeatable-new'){const id=String(fd.get('cube_id')||''),title=String(fd.get('title')||'').trim(),minutes=+fd.get('minutes');
if(!title||!minutes)return;await ctx.saveCubeRepeatable(id,{title,minutes});}
      if(type==='project-new'){const name=String(fd.get('name')||'').trim(),color=String(fd.get('c')||'');if(!name||!color)return;
await send('project.create',{name,color},t('Проект создан. Дальше — «+ Цель».'),close);}
      if(type==='goal-new'||type==='quick-goal'){const title=String(fd.get('title')||'').trim(),q=U.quick;let s=type==='quick-goal'?q?.s:
fromIso(fd.get('s')),en=type==='quick-goal'?q?.e:fromIso(fd.get('e'));if(!title||!Number.isFinite(s)||!Number.isFinite(en))return;if(en<s)[s,en]=[en,
s];const project_id=await ensureProject(fd);if(!project_id)return;const before=new Set(S.goals.map(g=>g.id));await send('goal.create',{project_id,
title,start_date:iso(s),end_date:iso(en),planned_minutes:type==='quick-goal'?0:Math.max(.5,+fd.get('h')||3)*60,deadline_date:fd.get('due')||null,
auto_volume:type==='quick-goal'},t('Цель создана. Добавьте ей кубики.'),()=>{close();U.quick=null;
const added=S.goals.filter(g=>!before.has(g.id));if(added.length===1){U.sel=added[0].id;U.flash=added[0].id;ctx.timer(()=>{U.flash=null;},2500);
}U.lastP=project_id;if(U.screen==='week'&&(s>U.weekStart+6||en<U.weekStart)){U.weekStart=weekOf(s);U.day=s;}});}
      if(type==='cube-new'){const title=String(fd.get('title')||'').trim(),goal_id=String(fd.get('goal')||''),duration_minutes=+fd.get('min');
const newName=String(fd.get('newp')||'').trim(),newColor=String(fd.get('newc')||''),newGoal=String(fd.get('newgoal')||'').trim();
if((newName||newColor||newGoal)&&(!newName||!newColor||!newGoal)){toast(t('Название проекта')+' · '+t('Цвет нового проекта')+' · '+t('Новая цель'));return;}
if(!title||goal_id&&!goal(goal_id))return;let selected_date=fd.get('where')==='day'?String(fd.get('day')||''):null,start_minute=null;
const v=String(fd.get('at')||'');if(selected_date&&v)start_minute=clamp(Math.round((+v.slice(0,2)*60+ +v.slice(3))/15)*15,480,1440-duration_minutes);
if(start_minute!=null){const collision=ctx.conflict(fromIso(selected_date),start_minute,duration_minutes);if(collision){start_minute=null;
toast(t('Время занято; кубик будет в дне без времени.'));}}await send('cube.create',{goal_id:goal_id||null,title,
duration_minutes,activity_kind:String(fd.get('activity_kind')|| (goal_id?'work':'personal')),selected_date:selected_date||null,start_minute,
...(newName?{new_project:{name:newName,color:newColor,goal:newGoal}}:{})},
t('Кубик добавлен'),close);}
    }finally{if(button?.isConnected)button.disabled=false;}
  });
  Object.assign(ctx,{ACTS});
}
