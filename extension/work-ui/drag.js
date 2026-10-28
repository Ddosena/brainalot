import {t} from '../i18n.js';
// Approved Work prototype, 2026-10-28: shared drag implementation.
export function installDrag(ctx) {
const {clamp,clk,ddmm,dow,iso}=ctx;
const {U,DAY_FROM,DAY_SPAN,CUBE_MAX,HALF,PPD_MIN,PPD_MAX}=ctx;
function setOver(el){if(ctx.D.overEl===el)return;ctx.D.overEl?.classList.remove('dz-over');ctx.D.overEl=el;el?.classList.add('dz-over');}
function cancelDrags(){if(ctx.D.bdrag)clearTimeout(ctx.D.bdrag.timer);ctx.D.bdrag=ctx.D.gdrag=ctx.D.pdrag=ctx.D.scaling=null;U.arrowDrag=null;
if(ctx.D.rdrag){Object.assign(ctx.D.rdrag.k,{at:ctx.D.rdrag.at,min:ctx.D.rdrag.min});ctx.D.rdrag=null;}ctx.D.ghost=null;ctx.D.ghostEl?.remove();
ctx.D.ghostEl=null;setOver(null);ctx.render();}
function ghostText(text,out){if(!ctx.D.ghostEl){ctx.D.ghostEl=document.createElement('div');ctx.D.ghostEl.className='dragghost';
document.body.append(ctx.D.ghostEl);}ctx.D.ghostEl.textContent=text;ctx.D.ghostEl.classList.toggle('out',!!out);}
function hitAt(x,y){
  const el=document.elementFromPoint(x,y),zone=el?.closest?.('[data-drop]');if(!zone)return null;
  const r=zone.getBoundingClientRect(),kind=zone.dataset.drop,day=+zone.dataset.day;
  if(kind==='shelf')return {where:'shelf',zone};
  if(kind==='tray'||kind==='day')return {where:kind,day,at:null,zone};
  if(kind==='track')return {where:'track',day,at:Math.round((DAY_FROM+(x-r.left)/r.width*DAY_SPAN)/15)*15,zone};
  if(kind==='vtrack')return {where:'vtrack',day,at:Math.round((DAY_FROM+(y-r.top)/r.height*DAY_SPAN)/15)*15,zone};
  const sc=r.width/360,lx=(x-r.left)/sc-180,ly=(y-r.top)/sc-180,rad=Math.hypot(lx,ly);if(rad<70||rad>160)return null;
  const a=(Math.atan2(lx,-ly)+Math.PI*2)%(Math.PI*2);return {where:'clock',day,at:Math.round((HALF+a/(Math.PI*2)*HALF)/15)*15,zone};
}
ctx.listen(document,'pointerdown',e=>{
  if(e.button!==0)return;
  if(e.target.closest('[data-act="cube-pin"],[data-act^="timer-"]'))return;
  if(U.menu&&!e.target.closest('.menu')){U.menu=null;ctx.renderLayer();}
  if(U.quick&&!e.target.closest('.quick')){U.quick=null;ctx.render();return;}
  const t=e.target;
  const sc=t.closest('[data-scaler]');if(sc){e.preventDefault();const av=ctx.$('#av');ctx.D.scaling={day:(av.scrollTop+av.clientHeight/2)/U.ppd};
try{sc.setPointerCapture(e.pointerId);}catch{}applyScale(e);return;}
  if(t.closest('.overlay')){const c=t.closest('[data-cube]');if(c&&!t.closest('.seg'))ctx.D.pdrag={id:c.dataset.cube,x:e.clientX,y:e.clientY,moved:
false,canDrag:false};return;}
  const rz=t.closest('[data-resize]');
  if(rz){const k=ctx.cube(rz.dataset.id),zone=rz.closest('[data-drop]');ctx.D.rdrag={k,zoneId:zone.id,axis:zone.dataset.drop==='vtrack'?'y':'x',start:
rz.dataset.resize==='l'||rz.dataset.resize==='t',at:k.at,min:k.min,changed:false};e.preventDefault();return;}
  const band=t.closest('.gband[data-band]');
  if(band){const grid=band.closest('.wk'),g=ctx.goal(band.dataset.goal);ctx.D.bdrag={band,g,x:e.clientX,mode:t.closest('.edge.l')?'start':
t.closest('.edge.r')?'end':'move',s:g.s,e:g.e,ns:g.s,ne:g.e,cell:grid.clientWidth/7,timer:null,dir:0,moved:false};e.preventDefault();return;}
  const gl=t.closest('[data-gl]');
  if(gl){const g=ctx.goal(gl.dataset.goal),av=ctx.$('#av');ctx.D.gdrag={g,y0:e.clientY+(av?.scrollTop||0),x:e.clientX,y:e.clientY,delta:0,moved:
false};e.preventDefault();return;}
  const c=t.closest('[data-cube]');
  if(c&&!t.closest('.seg')){const strip=c.closest('[data-scroll-strip]');ctx.D.pdrag={id:c.dataset.cube,x:e.clientX,y:e.clientY,moved:false,canDrag:!!c.dataset.drag,src:c,strip,scrollLeft:strip?.scrollLeft,gesture:null};e.preventDefault();}
  else {const strip=t.closest('[data-scroll-strip]');if(strip){ctx.D.pdrag={x:e.clientX,y:e.clientY,moved:false,canDrag:true,strip,scrollLeft:strip.scrollLeft,gesture:'scroll'};e.preventDefault();}}
});
ctx.listen(document,'pointermove',e=>{
  if(ctx.D.scaling)return applyScale(e);
  if(ctx.D.bdrag)return moveBand(e);
  if(ctx.D.gdrag)return moveGl(e);
  if(ctx.D.rdrag)return moveResize(e);
  if(ctx.D.pdrag)return moveCube(e);
});
ctx.listen(document,'pointerup',e=>{try{
  if(ctx.D.scaling){ctx.D.scaling=null;const top=ctx.$('#av')?.scrollTop;ctx.saveUI();ctx.render();const n=ctx.$('#av');if(n&&top!=null)n.scrollTop=top;return;}
  if(ctx.D.bdrag)return endBand(e);
  if(ctx.D.gdrag)return endGl(e);
  if(ctx.D.rdrag)return endResize(e);
  if(ctx.D.pdrag)return endCube(e);
}finally{ctx.idle();}});
ctx.listen(window,'blur',()=>{if(ctx.D.pdrag||ctx.D.rdrag||ctx.D.bdrag||ctx.D.gdrag){cancelDrags();ctx.idle();}});
ctx.listen(document,'pointercancel',()=>{if(ctx.D.pdrag||ctx.D.rdrag||ctx.D.bdrag||ctx.D.gdrag){cancelDrags();ctx.idle();}});
function moveBand(e){
  const d=ctx.D.bdrag,g=d.g,ws=U.weekStart,days=Math.round((e.clientX-d.x)/d.cell);
  if(Math.abs(e.clientX-d.x)>4&&!d.moved){d.moved=true;d.band.classList.add('dragging');}
  if(!d.moved)return;
  let s=d.s,en=d.e;if(d.mode==='move'){s+=days;en+=days;}else if(d.mode==='start')s=Math.min(d.s+days,d.e);else en=Math.max(d.e+days,d.s);
  d.ns=s;d.ne=en;
  const a=clamp(s-ws,0,6)+1,b=clamp(en-ws,0,6)+2;d.band.style.gridColumn=`${a}/${b}`;
  const small=d.band.querySelector('small');if(small)small.textContent=`${ddmm(s)}–${ddmm(en)}`;
  const grid=d.band.closest('.wk'),rect=grid.getBoundingClientRect(),dir=d.mode==='move'?(e.clientX>rect.right-26?1:e.clientX<rect.left+26?-1:0):0;
  if(dir!==d.dir){clearTimeout(d.timer);d.dir=dir;grid.classList.remove('edge-next','edge-prev');
    if(dir){grid.dataset.edge=dir>0?t("на следующую неделю →"):t("← на прошлую неделю");grid.classList.add(dir>0?
'edge-next':'edge-prev');
      d.timer=setTimeout(()=>{ctx.D.bdrag=null;U.weekStart+=7*dir;U.day+=7*dir;U.sel=g.id;void ctx.send('goal.move',{id:g.id,delta_days:7*dir});},650);}}
}
function endBand(){
  const d=ctx.D.bdrag;ctx.D.bdrag=null;clearTimeout(d.timer);ctx.D.suppressClick=true;setTimeout(()=>ctx.D.suppressClick=false,0);
  if(!d.moved){ctx.openGoal(d.g.id,true);ctx.render();return;}
  if(d.ns===d.s&&d.ne===d.e){ctx.render();return;}
  return d.mode==='move'?ctx.send('goal.move',{id:d.g.id,delta_days:d.ns-d.s}):ctx.send('goal.update',{id:d.g.id,start_date:iso(d.ns),end_date:
iso(d.ne)});
}
// Goal dots and labels on the arrow: hold and drag along the days; near the edges the arrow scrolls by itself.
function paintArrow(){const av=ctx.$('#av');if(!av)return;const top=av.scrollTop;av.innerHTML=ctx.arrow();av.scrollTop=top;}
function moveGl(e){
  const d=ctx.D.gdrag,av=ctx.$('#av');if(!av)return;d.x=e.clientX;d.y=e.clientY;
  const dy=e.clientY+av.scrollTop-d.y0;if(!d.moved&&Math.abs(dy)>4){d.moved=true;edgeScroll();}if(!d.moved)return;
  const delta=Math.round(dy/U.ppd),g=d.g,at=(g.due??g.e)+delta;
  const start=ddmm(g.s+delta),end=ddmm(g.e+delta);
  const period=g.s===g.e?start:start+'–'+end;
  const deadline=g.due!=null?' '+t('· срок')+' '+ddmm(at):'';
  ghostText(`${g.title}: ${period}${deadline}`);
  ctx.D.ghostEl.style.left=e.clientX+'px';ctx.D.ghostEl.style.top=e.clientY+'px';
  if(delta!==d.delta){d.delta=delta;U.arrowDrag={id:g.id,delta};paintArrow();}
}
function edgeScroll(){
  if(!ctx.D.gdrag?.moved)return;const av=ctx.$('#av');
  if(av){const r=av.getBoundingClientRect(),step=ctx.D.gdrag.y<r.top+36?-12:ctx.D.gdrag.y>r.bottom-36?12:0;if(step){av.scrollTop+=step;
moveGl({clientX:ctx.D.gdrag.x,clientY:ctx.D.gdrag.y});}}
  requestAnimationFrame(edgeScroll);
}
function endGl(){
  const d=ctx.D.gdrag;ctx.D.gdrag=null;U.arrowDrag=null;ctx.D.ghostEl?.remove();ctx.D.ghostEl=null;ctx.D.suppressClick=true;setTimeout(()=>
ctx.D.suppressClick=false,0);
  if(!d.moved){ctx.pickGoal(d.g.id);ctx.render();return;}
  if(!d.delta){ctx.render();return;}
  return ctx.send('goal.move',{id:d.g.id,delta_days:d.delta});
}
// Timeline edges change the duration in 15-minute steps (up to 4 h, never over another block).
function moveResize(e){
  const d=ctx.D.rdrag,k=d.k,zone=document.getElementById(d.zoneId);if(!zone)return;const r=zone.getBoundingClientRect();
  const pos=d.axis==='x'?(e.clientX-r.left)/r.width:(e.clientY-r.top)/r.height,m=Math.round((DAY_FROM+pos*DAY_SPAN)/15)*15;
  let at=d.at,min=d.min;
  if(d.start){const end=d.at+d.min;at=clamp(m,Math.max(DAY_FROM,end-CUBE_MAX),end-15);min=end-at;}
  else min=clamp(m-d.at,15,Math.min(CUBE_MAX,24*60-d.at));
  if(ctx.conflict(k.day,at,min,k.id))return;
  if(at!==k.at||min!==k.min){k.at=at;k.min=min;d.changed=true;ctx.paintDrop();}
}
function endResize(){
  const d=ctx.D.rdrag;ctx.D.rdrag=null;ctx.D.suppressClick=true;setTimeout(()=>ctx.D.suppressClick=false,0);
  if(!d.changed)return;
  const fin={at:d.k.at,min:d.k.min};Object.assign(d.k,{at:d.at,min:d.min});
  return ctx.send('cube.update',{id:d.k.id,start_minute:fin.at,duration_minutes:fin.min});
}
// Cubes: one drag for shelves, trays, the day cells, both timelines and the clock.
function cubeDropHint(k,hit){
  if(!hit||hit.where==='shelf'){
    if(k.day==null)return {text:k.title,out:false};
    const destination=hit
      ?(k.goal?t('на полку цели'):t('в «Другие»'))
      :(k.goal?t('вернуть на полку цели'):t('вернуть в «Другие»'));
    return {text:k.title+' · '+destination,out:true};
  }
  if(hit.at==null)return {text:k.title+' · '+dow(hit.day)+' '+ddmm(hit.day)+t(', без времени'),out:false};
  const at=clamp(hit.at,DAY_FROM,24*60-k.min),occupied=ctx.conflict(hit.day,at,k.min,k.id);
  const text=occupied?t('занято: «')+occupied.title+'»':k.title+' · '+clk(at)+'–'+clk(at+k.min);
  return {text,out:false};
}
function moveCube(e){
  const d=ctx.D.pdrag;if(!d.canDrag&&!d.strip)return;
  if(d.strip&&!d.gesture){const dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.hypot(dx,dy)<5)return;
    d.gesture=d.canDrag&&Math.abs(dy)>Math.abs(dx)?'drag':'scroll';}
  if(d.gesture==='scroll'){
    const rect=d.strip.getBoundingClientRect();
    if(d.id&&d.canDrag&&(e.clientY<rect.top-8||e.clientY>rect.bottom+8)){d.gesture='drag';d.moved=false;}
    else {d.moved=true;d.strip.scrollLeft=d.scrollLeft-(e.clientX-d.x);return;}
  }
  if(!d.moved&&Math.hypot(e.clientX-d.x,e.clientY-d.y)<5)return;
  if(!d.moved){d.moved=true;d.src?.classList.add('dragsrc');}
  const k=ctx.cube(d.id),hit=hitAt(e.clientX,e.clientY);
  const {text,out}=cubeDropHint(k,hit);
  ghostText(text,out);ctx.D.ghostEl.style.left=e.clientX+'px';ctx.D.ghostEl.style.top=e.clientY+'px';
  setOver(hit&&['shelf','tray','day'].includes(hit.where)?hit.zone:null);
  const next=hit&&hit.at!=null?{id:k.id,where:hit.where,day:hit.day,at:clamp(hit.at,DAY_FROM,24*60-k.min)}:null;
  if(JSON.stringify(next)!==JSON.stringify(ctx.D.ghost)){ctx.D.ghost=next;ctx.paintDrop();}
}
function endCube(e){
  const d=ctx.D.pdrag;ctx.D.pdrag=null;ctx.D.ghostEl?.remove();ctx.D.ghostEl=null;setOver(null);ctx.D.suppressClick=true;setTimeout(()=>
ctx.D.suppressClick=false,0);
  if(d.gesture==='scroll')return;
  const k=ctx.cube(d.id);if(!k)return;
  if(!d.moved){ctx.D.ghost=null;ctx.openModal(k.isRepeatableTemplate?{type:'repeatable',id:k.repeatableId}:{type:'cube',id:k.id});return;}
  const hit=hitAt(e.clientX,e.clientY);ctx.D.ghost=null;
  if(!hit||hit.where==='shelf'){if(k.day!=null)return ctx.send('cube.return',{id:k.id});return ctx.render();}
  if(k.isRepeatableTemplate)return ctx.send('repeatable.drop',{repeatable_id:k.repeatableId,selected_date:iso(hit.day),start_minute:hit.at==null?
null:clamp(hit.at,DAY_FROM,1440-k.min)});
  return ctx.send('cube.place',{id:k.id,selected_date:iso(hit.day),start_minute:hit.at==null?null:clamp(hit.at,DAY_FROM,1440-k.min),
...(k.duration_unknown?{duration_minutes:k.min}:{})});
}
// Scale slider and the wheel on the arrow.
function applyScale(e){
  const sc=ctx.$('[data-scaler]');if(!sc||!ctx.D.scaling)return;const r=sc.getBoundingClientRect(),y=clamp(e.clientY-r.top,30,r.height-30),
frac=1-(y-30)/(r.height-60);
  U.ppd=PPD_MIN*Math.pow(PPD_MAX/PPD_MIN,frac);const day=ctx.D.scaling.day;ctx.render();const av=ctx.$('#av');av.scrollTop=day*U.ppd-av.clientHeight/2;
}
ctx.listen(document,'wheel',e=>{
  const strip=e.target.closest('[data-scroll-strip]');if(strip&&!e.ctrlKey){const delta=e.deltaY||e.deltaX,max=strip.scrollWidth-strip.clientWidth;if((delta>0&&strip.scrollLeft<max-1)||(delta<0&&strip.scrollLeft>0)){e.preventDefault();strip.scrollLeft+=delta;}return;}
  const av=e.target.closest('#av');if(!av||!e.ctrlKey)return;e.preventDefault();
  const rect=av.getBoundingClientRect(),day=(av.scrollTop+e.clientY-rect.top)/U.ppd;
  U.ppd=clamp(U.ppd*Math.exp(-e.deltaY*.0015),PPD_MIN,PPD_MAX);ctx.render();const n=ctx.$('#av');n.scrollTop=day*U.ppd-(e.clientY-rect.top);
},{passive:false});
Object.assign(ctx,{setOver,cancelDrags,ghostText,hitAt,moveBand,endBand,paintArrow,moveGl,edgeScroll,endGl,moveResize,endResize,moveCube,endCube,applyScale});
}
