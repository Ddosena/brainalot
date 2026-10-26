import {localeTag,t} from './i18n.js';
// Read-only clock projection of the canonical timeline. Noon–midnight matches
// the owner's original radial reference; morning has a separate half-day.
const DAY=1440,HALF=720,C=180,R=112;
let orbitRenderSequence=0;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const clock=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(Math.round(n%60)).padStart(2,'0');
const range=half=>half==='morning'?{start:0,end:HALF}:{start:HALF,end:DAY};
const angle=(n,s=HALF,e=DAY)=>(n-s)/(e-s)*Math.PI*2-Math.PI/2;
function point(n,s=HALF,e=DAY,r=R){const a=angle(n,s,e);return{x:C+r*Math.cos(a),y:C+r*Math.sin(a)};}
export function upcomingNightDeadline(nowMinutes){
 if(!Number.isFinite(nowMinutes)||nowMinutes<0||nowMinutes>=DAY)return null;
 return DAY+60;
}
export function unwrapOrbitPointer(x,y,nowMinutes,previousMinute=null){
 if(upcomingNightDeadline(nowMinutes)===null||![x,y].every(Number.isFinite))return null;
 const radians=(Math.atan2(x-C,C-y)+Math.PI*2)%(Math.PI*2);
 let minute=HALF+radians/(Math.PI*2)*HALF;
 if(Number.isFinite(previousMinute))return minute+Math.round((previousMinute-minute)/HALF)*HALF;
 // The real hand shares an angle with its twelve-hour counterpart. In the
 // morning, use the morning cycle so its ray means now and earlier rays clamp.
 if(nowMinutes>=60&&nowMinutes<HALF)minute-=HALF;
 // After 13:00 (and during 00:00–01:00), the first hour is tomorrow's 00–01.
 if(minute<=HALF+60+1e-8&&(nowMinutes>=HALF+60||nowMinutes<60))minute+=HALF;
 return minute;
}
function clampOrbitMinute(minute,nowMinutes){
 const deadline=upcomingNightDeadline(nowMinutes);
 return deadline===null||minute===null?null:Math.min(deadline,Math.max(nowMinutes<60?DAY+nowMinutes:nowMinutes,minute));
}
export function orbitHoverMinute(x,y,nowMinutes,previousMinute=null){
 return clampOrbitMinute(unwrapOrbitPointer(x,y,nowMinutes,previousMinute),nowMinutes);
}
export function orbitCountdownMinutes(selectedMinute,nowMinutes){
 const deadline=upcomingNightDeadline(nowMinutes);
 const current=nowMinutes<60?DAY+nowMinutes:nowMinutes;
 return deadline===null||!Number.isFinite(selectedMinute)?null:Math.max(0,Math.round(deadline-Math.min(deadline,Math.max(current,selectedMinute))));
}
const countdownText=minutes=>{
 const hours=Math.floor(minutes/60),rest=minutes%60;
 return hours?t('{hours} ч {minutes} мин',{hours,minutes:String(rest).padStart(2,'0')}):t('{minutes} мин',{minutes:rest});
};
export function mountOrbitCountdown(host,{getNow=()=>new Date()}={}){
 const section=host?.matches?.('.cc-day-orbit')?host:host?.querySelector?.('.cc-day-orbit');
 const svg=section?.querySelector?.('svg');
 if(!svg)return ()=>{};
 const ns='http://www.w3.org/2000/svg',make=(name,className)=>{
  const node=svg.ownerDocument.createElementNS(ns,name);node.setAttribute('class',className);return node;
 };
 const layer=make('g','cc-orbit-countdown');layer.setAttribute('hidden','');layer.setAttribute('aria-hidden','true');
 const line=make('line','cc-orbit-countdown-line'),dot=make('circle','cc-orbit-countdown-dot');dot.setAttribute('r','3');
 const label=make('text','cc-orbit-countdown-label');label.setAttribute('x',String(C));label.setAttribute('y','226');label.setAttribute('text-anchor','middle');
 layer.append(line,dot,label);svg.append(layer);
 let previousPointerMinute=null;
 const hide=()=>{previousPointerMinute=null;layer.setAttribute('hidden','');};
 const move=event=>{
  if(event.pointerType!=='mouse'&&event.pointerType!=='pen'){hide();return;}
  const {today,nowMinutes}=currentDayPosition(section.dataset.orbitDate,getNow());
  if(!today){hide();return;}
  const matrix=svg.getScreenCTM?.();if(!matrix){hide();return;}
  const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;
  const local=p.matrixTransform(matrix.inverse()),unwrapped=unwrapOrbitPointer(local.x,local.y,nowMinutes,previousPointerMinute);
  const target=clampOrbitMinute(unwrapped,nowMinutes);
  if(target===null){hide();return;}
  previousPointerMinute=unwrapped;
  const display=HALF+target%HALF,inner=point(display,HALF,DAY,83),outer=point(display,HALF,DAY,141);
  line.setAttribute('x1',String(inner.x));line.setAttribute('y1',String(inner.y));line.setAttribute('x2',String(outer.x));line.setAttribute('y2',String(outer.y));
  dot.setAttribute('cx',String(outer.x));dot.setAttribute('cy',String(outer.y));
  label.textContent=t('До 01:00 · {remaining}',{remaining:countdownText(orbitCountdownMinutes(target,nowMinutes))});
  layer.removeAttribute('hidden');
 };
 svg.addEventListener('pointermove',move);svg.addEventListener('pointerleave',hide);svg.addEventListener('pointercancel',hide);
 return ()=>{svg.removeEventListener('pointermove',move);svg.removeEventListener('pointerleave',hide);svg.removeEventListener('pointercancel',hide);layer.remove();};
}
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
export function lessonTitle(title){return String(title||t('Урок')).trim().replace(/^Урок\s*[·:—–-]\s*/i,'').trim()||t('Урок');}
export function lessonTextPath(from,to,start,end,radius,reverse=false){
 const a=point(reverse?to:from,start,end,radius),b=point(reverse?from:to,start,end,radius),large=to-from>(end-start)/2?1:0;
 return 'M '+a.x.toFixed(2)+' '+a.y.toFixed(2)+' A '+radius+' '+radius+' 0 '+large+' '+(reverse?0:1)+' '+b.x.toFixed(2)+' '+b.y.toFixed(2);
}
const graphemeSegmenter=globalThis.Intl?.Segmenter?new Intl.Segmenter(undefined,{granularity:'grapheme'}):null;
const chars=value=>graphemeSegmenter?[...graphemeSegmenter.segment(String(value))].map(part=>part.segment):Array.from(String(value));
function clippedText(value,maxWidth,measure,font){
 const letters=chars(value.trim());if(!letters.length)return '';
 let best='…';for(let count=1;count<=letters.length;count++){
  const incomplete=count<letters.length,text=letters.slice(0,count).join('').trimEnd()+(incomplete?'…':'');
  if(measure(text,font)<=maxWidth)best=text;else break;
 }return best;
}
export function wrapLessonTitle(title,widths,measure,{minFont=13,maxFont=16}={}){
 const clean=lessonTitle(title),letters=chars(clean),limit=widths.map(width=>Math.max(8,width-5));
 const choose=(wordBoundaryOnly)=>{
  for(let font=maxFont;font>=minFont;font-=.5){
   if(measure(clean,font)<=limit[0])return {lines:[clean,''],font,truncated:false};
   const options=[];
   for(let i=2;i<letters.length-1;i++){
    const left=letters.slice(0,i).join(''),right=letters.slice(i).join(''),boundary=/\s/.test(letters[i-1])||/\s/.test(letters[i]),hyphen=!boundary&&!/[—–-]/.test(letters[i-1]);
    if(wordBoundaryOnly&&!boundary)continue;
    const leftWord=chars(left.trimEnd().split(/\s+/).at(-1)||''),rightWord=chars(right.trimStart().split(/\s+/)[0]||'');
    if(hyphen&&(leftWord.length<2||rightWord.length<2))continue;
    const first=left.trimEnd()+(hyphen?'-':''),second=right.trimStart();if(!first||!second)continue;
    const a=measure(first,font)/limit[0],b=measure(second,font)/limit[1];
    if(a<=1&&b<=1)options.push({lines:[first,second],score:Math.max(a,b)+Math.abs(a-b)*.04+(hyphen?.035:0)});
   }
   options.sort((a,b)=>a.score-b.score);if(options.length)return {...options[0],font,truncated:false};
  }
  return null;
 };
 return choose(true)||choose(false)||{lines:[clippedText(clean,limit[0],measure,minFont),''],font:minFont,truncated:true};
}
export function fitOrbitLabels(host){
 if(!host?.querySelectorAll)return [];
 const canvas=globalThis.document?.createElement?.('canvas'),ctx=canvas?.getContext?.('2d');if(!ctx)return [];
 const measure=(value,font)=>{ctx.font=`650 ${font}px "Segoe UI", Arial, sans-serif`;return ctx.measureText(value).width;},results=[];
 for(const group of host.querySelectorAll('[data-orbit-lesson-title]')){
  const paths=[...group.querySelectorAll('defs path')],texts=[...group.querySelectorAll('.cc-orbit-fixed-title')];if(paths.length!==2||texts.length!==2)continue;
  const widths=paths.map(path=>path.getTotalLength());let layout=wrapLessonTitle(group.dataset.orbitLessonTitle,widths,measure);
  const apply=()=>texts.forEach((node,index)=>{node.style.fontSize=layout.font+'px';node.querySelector('textPath').textContent=layout.lines[index];node.toggleAttribute('hidden',!layout.lines[index]);});
  apply();
  while(layout.font>13&&texts.some((node,index)=>!node.hasAttribute('hidden')&&node.getComputedTextLength()>widths[index]-4)){
   const font=Math.max(13,layout.font-.5);layout=wrapLessonTitle(group.dataset.orbitLessonTitle,widths,measure,{minFont:font,maxFont:font});apply();
  }
  for(let index=0;index<2;index++){
   const node=texts[index],path=node.querySelector('textPath');if(node.hasAttribute('hidden'))continue;
   while(path.textContent.length>1&&node.getComputedTextLength()>widths[index]-4){path.textContent=chars(path.textContent).slice(0,-2).join('')+'…';layout.truncated=true;}
   if(node.getComputedTextLength()>widths[index]-4){node.setAttribute('hidden','');path.textContent='';layout.truncated=true;}
   layout.lines[index]=path.textContent;
  }
  results.push({title:group.dataset.orbitLessonTitle,lines:layout.lines,font:layout.font,truncated:layout.truncated,widths,actualWidths:texts.map(node=>node.hasAttribute('hidden')?0:node.getComputedTextLength())});
 }
 return results;
}
export function renderDayOrbit({date,events=[],cubes=[],nowMinutes=null,today=false,half='afternoon'}={}){
 const period=half==='morning'?'morning':'afternoon',{start,end}=range(period);
 const rows=[...events.filter(e=>e?.required).map(e=>({...e,fixed:true})),...cubes.filter(c=>Number.isFinite(c?.start))].filter(e=>Number.isFinite(e.start)&&Number.isFinite(e.minutes)&&e.minutes>0).map(e=>({...e,from:Math.max(start,e.start),to:Math.min(end,e.start+e.minutes)})).filter(e=>e.to>e.from).sort((a,b)=>a.from-b.from).map(e=>({...e,color:/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(e.color)?e.color:e.fixed?'#e2b778':'#83b7ca'}));
 const fixed=rows.filter(e=>e.fixed),paintRows=[...rows.filter(e=>!e.fixed),...fixed],renderId=++orbitRenderSequence;
 let ticks='';
 for(let step=0;step<12;step++){
  const minute=start+step*60,p=point(minute,start,end,148),b=point(minute,start,end,step%3===0?154:151);
  ticks+='<line class="cc-orbit-tick" x1="'+p.x+'" y1="'+p.y+'" x2="'+b.x+'" y2="'+b.y+'"/>';
  if(step%3===0){const n=point(minute,start,end,168);ticks+='<text class="cc-day-orbit-hour" data-hour="'+minute/60+'" x="'+n.x+'" y="'+n.y+'" text-anchor="middle" dominant-baseline="middle">'+String(minute/60).padStart(2,'0')+'</text>';}
 }
 const arcs=paintRows.map(e=>{
  const label=clock(e.start)+'–'+clock(e.start+e.minutes)+' · '+(e.title||t('Кубик')),mid=(e.from+e.to)/2,p=point(mid,start,end),text=e.fixed?'':workLabel(e.title,e.to-e.from);
  return '<g class="cc-orbit-interval" data-orbit-id="'+esc(e.id)+'" data-start="'+e.start+'" data-minutes="'+e.minutes+'"><path class="cc-day-orbit-sector '+(e.fixed?'is-fixed':'is-work')+'" d="'+orbitArcPath(e.from,e.to,start,end)+'" stroke="'+e.color+'" data-ctooltip="'+esc(label)+'"'+(e.fixed?' aria-label="'+esc(label)+'"':' data-action="inspect" data-id="'+esc(e.id)+'" role="button" tabindex="0" aria-label="'+esc(t('Открыть: {label}',{label}))+'"')+'>'+(e.fixed?'<title>'+esc(label)+'</title>':'')+'</path>'+(text?'<text class="cc-orbit-work-label" transform="translate('+p.x+' '+p.y+') rotate('+upright(mid,start,end)+')" text-anchor="middle" dominant-baseline="middle">'+esc(text)+'</text>':'')+'</g>';
 }).join('');
 const lessonHtml=fixed.map((e,index)=>{
  const raw=String(e.title||t('Урок')),title=lessonTitle(raw),mid=(e.from+e.to)/2,turn=(mid-start)/(end-start)*360,reverse=turn>90&&turn<270,radii=reverse?[110,132]:[132,110];
  const ids=radii.map((_,line)=>'cc-orbit-title-'+renderId+'-'+index+'-'+line),span=(e.to-e.from)/(end-start)*Math.PI*2;
  const lines=wrapLessonTitle(title,radii.map(radius=>radius*span),(value,font)=>chars(value).length*font*.54).lines;
  return '<g class="cc-orbit-fixed-label" data-orbit-lesson-title="'+esc(title)+'" data-lesson-id="'+esc(e.id)+'" data-start="'+e.start+'" data-minutes="'+e.minutes+'" aria-label="'+esc(raw+' · '+clock(e.start)+'–'+clock(e.start+e.minutes))+'" data-ctooltip="'+esc(raw+' · '+clock(e.start)+'–'+clock(e.start+e.minutes))+'"><title>'+esc(raw+' · '+clock(e.start)+'–'+clock(e.start+e.minutes))+'</title><defs>'+radii.map((radius,line)=>'<path id="'+ids[line]+'" d="'+lessonTextPath(e.from,e.to,start,end,radius,reverse)+'"/>').join('')+'</defs>'+ids.map((id,line)=>'<text class="cc-orbit-fixed-title" data-line="'+line+'"'+(lines[line]?'':' hidden')+'><textPath href="#'+id+'" startOffset="50%" text-anchor="middle">'+esc(lines[line])+'</textPath></text>').join('')+'</g>';
 }).join('');
 const now=today&&Number.isFinite(nowMinutes)?((nowMinutes%DAY)+DAY)%DAY:null;
 let marker='';if(now!==null){
  // Preserve the actual twelve-hour hand angle and truthful 24-hour aria label.
  const actualHalf=now<HALF?'morning':'afternoon',displayMinute=start+now%HALF,p=point(displayMinute,start,end,145),other=actualHalf!==period,periodLabel=actualHalf==='morning'?'00–12':'12–24';
  marker='<path class="cc-day-orbit-hand-tip" data-now="'+now+'" data-now-half="'+actualHalf+'" data-other-half="'+other+'" aria-label="'+esc(t('Текущее время {time} · {period}',{time:clock(now),period:periodLabel}))+'" transform="translate('+p.x+' '+p.y+') rotate('+(angle(displayMinute,start,end)*180/Math.PI+90)+')" d="M 0 0 L -8 -15 L 8 -15 Z"/>';
 }
 return '<section class="cc-day-orbit" data-orbit-date="'+esc(date)+'" data-half="'+period+'" data-lesson-layout="curved" aria-label="'+esc(t('Часы {start}–{end}: уроки и работа',{start:clock(start),end:clock(end)}))+'"><div class="cc-day-orbit-face"><svg viewBox="0 0 360 360" aria-label="'+esc(t('Время и интервалы: {start}–{end}',{start:clock(start),end:clock(end)}))+'"><circle class="cc-day-orbit-base" cx="180" cy="180" r="112"/>'+arcs+lessonHtml+ticks+'<text class="cc-day-orbit-center-time" x="180" y="190" text-anchor="middle">'+esc(now===null?shortDate(date):clock(now))+'</text>'+marker+'</svg></div></section>';
}
