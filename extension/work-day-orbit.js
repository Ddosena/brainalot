import {localeTag,t} from './i18n.js';
// Read-only clock projection of the canonical timeline. Noon–midnight matches
// the owner's original radial reference; morning has a separate half-day.
const DAY=1440,HALF=720,C=180,R=112;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const clock=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(Math.round(n%60)).padStart(2,'0');
const range=half=>half==='morning'?{start:0,end:HALF}:{start:HALF,end:DAY};
const angle=(n,s=HALF,e=DAY)=>(n-s)/(e-s)*Math.PI*2-Math.PI/2;
function point(n,s=HALF,e=DAY,r=R){const a=angle(n,s,e);return{x:C+r*Math.cos(a),y:C+r*Math.sin(a)};}
function upright(n,start=HALF,end=DAY){let rotation=(n-start)/(end-start)*360;if(rotation>90&&rotation<270)rotation+=180;return (rotation+180)%360-180;}
export function currentDayPosition(date,clockDate=new Date()){
 const p=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(clockDate)),v=k=>p.find(x=>x.type===k)?.value||'00';
 const today=date===v('year')+'-'+v('month')+'-'+v('day');
 return {today,nowMinutes:today?Number(v('hour'))*60+Number(v('minute')):null,label:today?v('hour')+':'+v('minute'):''};
}
export function orbitArcPath(from,to,start=HALF,end=DAY,radius=R){
 if(![from,to,start,end,radius].every(Number.isFinite)||end<=start||to<=from)return '';
 const a=point(from,start,end,radius),b=point(to,start,end,radius),arc=' A '+radius+' '+radius+' 0 ';
 if(to-from>=end-start){const m=point(from+(end-start)/2,start,end,radius);return 'M '+a.x+' '+a.y+arc+'1 1 '+m.x+' '+m.y+arc+'1 1 '+a.x+' '+a.y;}
 return 'M '+a.x.toFixed(2)+' '+a.y.toFixed(2)+arc+(to-from>(end-start)/2?1:0)+' 1 '+b.x.toFixed(2)+' '+b.y.toFixed(2);
}
function shortDate(date){return /^\d{4}-\d{2}-\d{2}$/.test(String(date))?new Intl.DateTimeFormat(localeTag(),{day:'numeric',month:'short',timeZone:'UTC'}).format(new Date(date+'T12:00:00Z')):'';}
export function layoutLessonLabels(rows,_mode='tangent',half='afternoon'){
 const {start,end}=range(half),boxes=[];
 for(const row of rows){
  const mid=(row.from+row.to)/2,boxWidth=40,boxHeight=36,rotation=upright(mid,start,end),radians=rotation*Math.PI/180,width=Math.abs(boxWidth*Math.cos(radians))+Math.abs(boxHeight*Math.sin(radians)),height=Math.abs(boxWidth*Math.sin(radians))+Math.abs(boxHeight*Math.cos(radians));
  // Prefer the true time angle. Dense lessons can use an interior callout;
  // its leader always keeps the exact time anchor visible on the sector.
  const a=angle(mid,start,end),tangent={x:-Math.sin(a),y:Math.cos(a)},candidates=[R,60].map(r=>point(mid,start,end,r));
  for(const offset of [48,-48,80,-80]){const p=point(mid,start,end,86);candidates.push({x:p.x+tangent.x*offset,y:p.y+tangent.y*offset});}
  const center=candidates.find(c=>{
   const left=c.x-width/2,top=c.y-height/2;
   if(left<224&&left+width>136&&top<202&&top+height>162)return false;
   if([[-1,-1],[-1,1],[1,-1],[1,1]].some(([x,y])=>Math.hypot(c.x+x*boxWidth/2*Math.cos(radians)-y*boxHeight/2*Math.sin(radians)-C,c.y+x*boxWidth/2*Math.sin(radians)+y*boxHeight/2*Math.cos(radians)-C)>143))return false;
   return !boxes.some(b=>left<b.left+b.width+2&&left+width+2>b.left&&top<b.top+b.height+2&&top+height+2>b.top);
  })||point(mid,start,end,R);
  boxes.push({...row,left:center.x-width/2,top:center.y-height/2,width,height,center,boxWidth,boxHeight,rotation,anchor:point(mid,start,end)});
 }
 return boxes;
}
export function orbitHalfCounts(events=[],cubes=[]){
 const rows=[...events.filter(e=>e.required),...cubes].filter(e=>Number.isFinite(e.start)&&Number.isFinite(e.minutes)&&e.minutes>0);
 return Object.fromEntries(['morning','afternoon'].map(half=>{const {start,end}=range(half);return [half,rows.filter(e=>e.start<end&&e.start+e.minutes>start).length];}));
}
function workLabel(title,span){
 const text=String(title||t('Работа')).replace(/^(?:уточнить|проверить|подготовить|собрать|сравнить|сделать)\s+/i,'').replace(/^практика\s*[·:—–-]\s*/i,'');
 const chars=Math.floor((span/HALF*2*Math.PI*R-8)/4.6);
 return chars<2?'':text.length>chars?text.slice(0,Math.max(1,chars-1))+'…':text;
}
export function renderDayOrbit({date,events=[],cubes=[],nowMinutes=null,today=false,half='afternoon'}={}){
 const period=half==='morning'?'morning':'afternoon',{start,end}=range(period);
 const rows=[...events.filter(e=>e?.required).map(e=>({...e,fixed:true})),...cubes.filter(c=>Number.isFinite(c?.start))].filter(e=>Number.isFinite(e.start)&&Number.isFinite(e.minutes)&&e.minutes>0).map(e=>({...e,from:Math.max(start,e.start),to:Math.min(end,e.start+e.minutes)})).filter(e=>e.to>e.from).sort((a,b)=>a.from-b.from).map(e=>({...e,color:/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(e.color)?e.color:e.fixed?'#e2b778':'#83b7ca'}));
 const lessons=layoutLessonLabels(rows.filter(e=>e.fixed),'tangent',period);
 let ticks='';
 for(let step=0;step<12;step++){
  const minute=start+step*60,p=point(minute,start,end,148),b=point(minute,start,end,step%3===0?154:151);
  ticks+='<line class="cc-orbit-tick" x1="'+p.x+'" y1="'+p.y+'" x2="'+b.x+'" y2="'+b.y+'"/>';
  if(step%3===0){const n=point(minute,start,end,168);ticks+='<text class="cc-day-orbit-hour" data-hour="'+minute/60+'" x="'+n.x+'" y="'+n.y+'" text-anchor="middle" dominant-baseline="middle">'+String(minute/60).padStart(2,'0')+'</text>';}
 }
 const arcs=rows.map(e=>{
  const label=clock(e.start)+'–'+clock(e.start+e.minutes)+' · '+(e.title||t('Кубик')),mid=(e.from+e.to)/2,p=point(mid,start,end),text=e.fixed?'':workLabel(e.title,e.to-e.from);
  return '<g class="cc-orbit-interval" data-orbit-id="'+esc(e.id)+'" data-start="'+e.start+'" data-minutes="'+e.minutes+'"><path class="cc-day-orbit-sector '+(e.fixed?'is-fixed':'is-work')+'" d="'+orbitArcPath(e.from,e.to,start,end)+'" stroke="'+e.color+'" data-ctooltip="'+esc(label)+'"'+(e.fixed?' aria-label="'+esc(label)+'"':' data-action="inspect" data-id="'+esc(e.id)+'" role="button" tabindex="0" aria-label="'+esc(t('Открыть: {label}',{label}))+'"')+'></path>'+(text?'<text class="cc-orbit-work-label" transform="translate('+p.x+' '+p.y+') rotate('+upright(mid,start,end)+')" text-anchor="middle" dominant-baseline="middle">'+esc(text)+'</text>':'')+'</g>';
 }).join('');
 const leaders=lessons.filter(e=>Math.hypot(e.center.x-e.anchor.x,e.center.y-e.anchor.y)>2).map(e=>'<line class="cc-orbit-lesson-leader" x1="'+e.anchor.x+'" y1="'+e.anchor.y+'" x2="'+e.center.x+'" y2="'+e.center.y+'"/>').join('');
 const lessonHtml=lessons.map(e=>{
  const full=String(e.title||t('Урок')).replace(/^Урок\s*[·:—–-]\s*/i,''),short=full.replace(/моделирование/ig,t('Модел.')).replace(/разбор работ/ig,t('Разбор'));
  return '<span class="cc-orbit-lesson" data-lesson-id="'+esc(e.id)+'" data-rotation="'+e.rotation.toFixed(2)+'" style="left:'+e.center.x/3.6+'%;top:'+e.center.y/3.6+'%;width:'+e.boxWidth/3.6+'%;height:'+e.boxHeight/3.6+'%;--lesson:'+e.color+';--lesson-angle:'+e.rotation+'deg" data-ctooltip="'+esc(clock(e.start)+'–'+clock(e.start+e.minutes)+' · '+full)+'" aria-label="'+esc(full)+'"><time>'+clock(e.start)+'</time><b>'+esc(short)+'</b></span>';
 }).join('');
 const now=today&&Number.isFinite(nowMinutes)?((nowMinutes%DAY)+DAY)%DAY:null;
 let marker='',currentPeriod='';if(now!==null){
  // A clock hand follows the actual twelve-hour position even while the owner
  // inspects another half-day. The small period label prevents AM/PM ambiguity;
  // timeline sectors above still belong only to the explicitly selected half.
  const actualHalf=now<HALF?'morning':'afternoon',displayMinute=start+now%HALF,p=point(displayMinute,start,end,145),other=actualHalf!==period,periodLabel=actualHalf==='morning'?'00–12':'12–24';
  marker='<path class="cc-day-orbit-hand-tip" data-now="'+now+'" data-now-half="'+actualHalf+'" data-other-half="'+other+'" aria-label="'+esc(t('Текущее время {time} · {period}',{time:clock(now),period:periodLabel}))+'" transform="translate('+p.x+' '+p.y+') rotate('+(angle(displayMinute,start,end)*180/Math.PI+90)+')" d="M 0 0 L -8 -15 L 8 -15 Z"/>';
  if(other)currentPeriod='<text class="cc-day-orbit-clock-period" x="180" y="212" text-anchor="middle" aria-label="'+esc(t('Текущая половина суток'))+'">'+periodLabel+'</text>';
 }
 return '<section class="cc-day-orbit" data-half="'+period+'" data-lesson-layout="tangent" aria-label="'+esc(t('Часы {start}–{end}: уроки и работа',{start:clock(start),end:clock(end)}))+'"><div class="cc-day-orbit-face"><svg viewBox="0 0 360 360" aria-label="'+esc(t('Время и интервалы: {start}–{end}',{start:clock(start),end:clock(end)}))+'"><circle class="cc-day-orbit-base" cx="180" cy="180" r="112"/>'+arcs+ticks+leaders+'<text class="cc-day-orbit-center-time" x="180" y="190" text-anchor="middle">'+esc(now===null?shortDate(date):clock(now))+'</text>'+currentPeriod+marker+'</svg>'+lessonHtml+'</div></section>';
}
