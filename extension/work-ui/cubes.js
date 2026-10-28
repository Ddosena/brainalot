import {t} from '../i18n.js';
// Approved Work prototype, 2026-10-28: shared cubes implementation.
export function installCubes(ctx) {
const {clk,ddmm,dow,dur,esc}=ctx;
const {S}=ctx;
function seg(kind,id,pct,n){
  const f=Math.round(pct/(100/n));
  const full=pct>=100?' full':'';
  const buttons=Array.from({length:n},(_,k)=>{
    const on=k<f?'on':'',value=Math.round((k+1)*100/n);
    return `<button type="button" class="${on}" aria-label="${value}%"></button>`;
  }).join('');
  return `<div class="seg${full}" data-seg-kind="${kind}" data-seg-id="${esc(id)}" data-n="${n}" `+
    `role="group" aria-label="${t('Готовность')} ${pct}%">${buttons}<output>${pct}%</output></div>`;
}
// Goal readiness, read-only: one share per cube, each filled by that cube's readiness.
function readinessCubes(g){
  const ids=Array.isArray(g.readiness_cube_ids)?new Set(g.readiness_cube_ids):null;
  return S.cubes.filter(k=>ids?ids.has(k.id):k.goal===g.id&&!k.candidate);
}
function goalBar(g,small){
  const own=readinessCubes(g),done=own.filter(k=>k.pct>=100).length;
  const parts=own.length?own.map(k=>{
    const full=k.pct>=100?' full':'';
    return `<span class="gp${full}" title="${esc(k.title)} · ${k.pct}%"><i style="width:${k.pct}%"></i></span>`;
  }).join(''):`<span class="gp none"><i style="width:${g.pct}%"></i></span>`;
  const title=own.length?`${t('Готовность по кубикам:')} ${done} ${t('из')} ${own.length} ${t('готово')}`:t('Кубиков нет');
  const suffix=own.length?` · ${done}/${own.length}`:'',size=small?' sm':'';
  return `<div class="gprog${size}" title="${title}">${parts}<output>${g.pct}%${suffix}</output></div>`;
}
function pinButton(k){
  const label=k.pinned?t('Открепить кубик'):t('Закрепить кубик'),star=k.pinned?'★':'☆';
  return `<button type="button" class="pin-btn" data-act="cube-pin" data-arg="${esc(k.id)}" `+
    `aria-pressed="${!!k.pinned}" aria-label="${label}" title="${label}">${star}</button>`;
}
function keycap(k,opt={}){
  const repeatable=!!k.isRepeatableTemplate,done=k.pct>=100,possible=!k.goal&&!repeatable&&!k.repeatable_instance,drag=!done&&!opt.nodrag;
  let where='';
  if(opt.where){
    const time=k.at!=null?' · '+clk(k.at):'';
    where=k.day==null?t('на полке цели'):`${dow(k.day)} ${ddmm(k.day)}${time}`;
  }else if(opt.time){
    const end=k.at+k.min;
    where=k.at!=null?`${clk(k.at)}–${clk(end)}`:t('без времени');
  }
  else if(possible&&k.src)where=k.src;
  const g=k.goal&&ctx.goal(k.goal);
  const canPin=repeatable||possible&&k.day==null&&!done;
  const play=ctx.timerButton?ctx.timerButton(k):'';
  const pin=canPin?pinButton(k):'',role=canPin||play?'group':'button';
  const classes=(repeatable?' repeatable':'')+(possible?' possible':'')+(done?' done':'');
  const action=repeatable?'repeatable-edit':'open-cube',arg=repeatable?k.repeatableId:k.id;
  const dragAttr=drag?` data-drag="${esc(k.id)}"`:'';
  const goalTitle=g?' · '+esc(g.title):'',progress=k.pct?` · ${k.pct}%`:'';
  const source=k.links?.length?`<span class="srcn" title="${t('Источники:')} ${k.links.length}">${CLIP}${k.links.length}</span>`:'';
  const marker=repeatable?'∞':done?'✓':k.pct?k.pct+'%':'';
  const location=where?`<span class="where">${esc(where)}</span>`:'',bar=repeatable?'':'<span class="bar"><i></i></span>';
  return `<div class="cbK${classes}" role="${role}" tabindex="0" aria-label="${esc(k.title)}" `+
    `data-act="${action}" data-arg="${esc(arg)}" data-cube="${esc(k.id)}"${dragAttr} `+
    `style="--c:${ctx.colorOf(k)};--p:${k.pct}%" title="${esc(k.title)} · ${dur(k.min)}${goalTitle}${progress}">`+
    `<b>${esc(k.title)}</b><span class="row"><span>${dur(k.min)}</span>${source}<span>${marker}${pin}${play}</span></span>`+
    `${location}${bar}</div>`;
}
const CLIP='<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">'+
  '<path d="M10.5 4.5 5.8 9.2a1.5 1.5 0 0 0 2.1 2.1l5-5a3 3 0 0 0-4.2-4.2L3.5 7.3a4.5 4.5 0 0 0 6.4 6.4L14 9.6" '+
  'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
function shortUrl(u){try{const x=new URL(u),path=x.pathname.length>1?x.pathname:'';return x.hostname.replace(/^www\./,'')+(path.length>22?
path.slice(0,22)+'…':path);}catch{return u;}}
// A source is a link (only http/https) or a short note.

function trophy(k){
  const g=k.goal&&ctx.goal(k.goal);
  const goalTitle=g?' · '+esc(g.title):'',subtitle=esc(g?g.title:k.src||'');
  return `<div class="trophy" role="button" tabindex="0" aria-label="${esc(k.title)}" data-act="open-cube" `+
    `data-arg="${esc(k.id)}" data-cube="${esc(k.id)}" style="--c:${ctx.colorOf(k)}" `+
    `title="${esc(k.title)} · ${dur(k.min)}${goalTitle} ${t('— открыть')}"><span class="medal">✓</span>`+
    `<b>${esc(k.title)}</b><small>${subtitle}</small><span class="row"><span>${dur(k.min)}</span><span>100%</span></span></div>`;
}
Object.assign(ctx,{seg,readinessCubes,goalBar,keycap,shortUrl,trophy,CLIP});
}
