import {t} from '../i18n.js';
// Approved Work prototype, 2026-10-28: shared clock implementation.
export function installClock(ctx) {
const {clk,ddmm,dow,dur,esc,hm,nowMinute}=ctx;
const {TODAY,HALF,RING,RW,FW,NUMR}=ctx;
const ang=m=>((m-HALF)/HALF)*Math.PI*2-Math.PI/2;
const pt=(m,r)=>({x:180+r*Math.cos(ang(m)),y:180+r*Math.sin(ang(m))});
function arc(from,to,r,reverse=false){const a=pt(reverse?to:from,r),b=pt(reverse?from:to,r),large=(to-from)>HALF/2?1:0;
const sweep=reverse?0:1;return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} A ${r} ${r} 0 ${large} ${sweep} ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;}
// Sector labels: along the arc when the title fits; otherwise smaller and radial,
// from the inner edge of the ring to the outer one, wrapped and cut with "…".
const measure=document.createElement('canvas').getContext('2d');
function textW(s,size){measure.font=`650 ${size}px "Segoe UI",system-ui,sans-serif`;return measure.measureText(s).width;}
function fitEllipsis(s,size,maxW){if(textW(s,size)<=maxW)return s;let t=s.replace(/…$/,'');while(t.length>1&&textW(t+'…',size)>maxW)t=t.slice(0,
-1);return t.trimEnd()+'…';}
// With hyphen=true a word wider than the ring is split as "Смодели-/ровать".
function wrapWords(title,size,maxW,hyphen){
  const lines=[];let cur='';
  for(let w of title.split(/\s+/)){
    if(hyphen)while(textW(w,size)>maxW&&w.length>5){
      let k=w.length-2;while(k>3&&textW(w.slice(0,k)+'-',size)>maxW)k--;
      // Prefer a break after a vowel, as in "Смодели-ровать".
      for(let j=k;j>=3;j--)if(/[аеёиоуыэюяaeiouy]/i.test(w[j-1])&&!/[аеёиоуыэюяьъйaeiouy]/i.test(w[j])){k=j;break;}
      if(cur){lines.push(cur);cur='';}lines.push(w.slice(0,k)+'-');w=w.slice(k);
    }
    const t=cur?cur+' '+w:w;if(!cur||textW(t,size)<=maxW)cur=t;else{lines.push(cur);cur=w;}
  }
  if(cur)lines.push(cur);return lines;
}
function sectorLabel(e,from,to,w,id,done){
  const cls='ck-label'+(e.fixed?' fixed':'')+(done?' done':''),title=e.title,arcLen=(to-from)/HALF*Math.PI*2*RING;
  for(const size of e.fixed?[12,11]:[11,10])if(textW(title,size)+
12<=arcLen)return `<text class="${cls}" dy="4" style="font-size:${size}px">`+
    `<textPath href="#${id}" startOffset="50%" text-anchor="middle">${esc(title)}</textPath></text>`;
  const maxW=w-8,across=(to-from)/HALF*Math.PI*2*(RING-w/2)-3;let pick=null;
  const tries=[[10,false],[9,false],[8,false],[9,true],[8,true]];
  for(const [size,hy] of tries){const lh=size*1.12,n=Math.max(1,Math.floor(across/lh)),lines=wrapWords(title,size,maxW,hy);if(lines.length<=n&&
lines.every(l=>textW(l,size)<=maxW)){pick={size,lh,lines};break;}}
  if(!pick){const size=8,lh=size*1.12,n=Math.max(1,Math.floor(across/lh)),all=wrapWords(title,size,maxW,true),lines=all.slice(0,n);
if(all.length>n)lines[n-1]+='…';pick={size,lh,lines:lines.map(l=>fitEllipsis(l,size,maxW))};}
  const mid=(from+to)/2,p=pt(mid,RING),n=pick.lines.length;let deg=ang(mid)*180/Math.PI;if(deg>90&&deg<270)deg-=180;
  const lines=pick.lines.map((line,k)=>{
    const y=((k-(n-1)/2)*pick.lh).toFixed(1);
    return `<tspan x="0" y="${y}" dominant-baseline="central">${esc(line)}</tspan>`;
  }).join('');
  return `<text class="${cls}" text-anchor="middle" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)}) `+
    `rotate(${deg.toFixed(1)})" style="font-size:${pick.size}px">${lines}</text>`;
}
function sectorPath(e,from,to,w,done){
  const drag=!e.fixed&&!e.isGhost&&!done;
  const cls=(e.fixed?' fixed':'')+(e.isGhost?' ghost':'')+(done?' done':'');
  const cube=!e.fixed&&!e.isGhost?` data-cube="${e.id}"`:'';
  const dragAttr=drag?` data-drag="${e.id}"`:'';
  const color=e.fixed?'':`stroke:color-mix(in srgb,${ctx.colorOf(e)} 80%,#ffffff)`;
  const end=e.at+e.min;
  return `<path class="ck-sector${cls}"${cube}${dragAttr} d="${arc(from,to,RING)}" style="stroke-width:${w};${color}">`+
    `<title>${esc(e.title)} · ${clk(e.at)}–${clk(end)}</title></path>`;
}
function clockCenter(i,now){
  const ghost=ctx.D.ghost;
  if(ghost?.where==='clock'){
    const end=ghost.at+(ctx.cube(ghost.id)?.min||0);
    return `<text class="ck-hint" x="180" y="174">${clk(ghost.at)}–${clk(end)}</text>`+
      `<text class="ck-sub" x="180" y="196">${t('отпусти, чтобы поставить')}</text>`;
  }
  if(i===TODAY){
    const left=now<60?60-now:25*60-now;
    return `<text class="ck-time" x="180" y="186">${clk(now)}</text>`+
      `<text class="ck-sub" x="180" y="208">${t('до сна ·')} ${hm(left)}</text>`;
  }
  return `<text class="ck-time" x="180" y="186" style="font-size:26px">${dow(i)} ${ddmm(i)}</text>`+
    `<text class="ck-sub" x="180" y="208">${t('в плане ·')} ${dur(ctx.dayLoad(i))}</text>`;
}
function clockSvg(i){
  const now=nowMinute(),hand=HALF+now%HALF;let defs='',body=`<circle class="ck-base" cx="180" cy="180" r="${RING}" style="stroke-width:${RW}"/>`;
  for(let h=0;h<12;h++){
    const a=pt(HALF+h*60,148),b=pt(HALF+h*60,h%3?152:156),major=h%3?'':' major';
    body+=`<line class="ck-tick${major}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  }
  for(let h=0;h<12;h+=3){const n=pt(HALF+h*60,NUMR),hour=12+h;body+=`<text class="ck-num major" x="${n.x}" y="${n.y}">${hour}</text>`;}
  ctx.trackRows(i,'clock').forEach((e,n)=>{
    const from=Math.max(HALF,e.at),to=Math.min(1440,e.at+e.min);if(to<=from)return;
    const w=e.fixed?FW:RW,mid=(from+to)/2,turn=(mid-HALF)/HALF*360,rev=turn>90&&turn<270,id=`ck-${n}`,done=!e.fixed&&e.pct>=100;
    defs+=`<path id="${id}" d="${arc(from,to,RING,rev)}"/>`;
    body+=sectorPath(e,from,to,w,done);
    body+=sectorLabel(e,from,to,w,id,done);
  });
  if(i===TODAY){const tp=pt(hand,RING+FW/2+2),deg=ang(hand)*180/Math.PI+90;
body+=`<path class="ck-hand" transform="translate(${tp.x} ${tp.y}) rotate(${deg})" d="M 0 0 L -8 -15 L 8 -15 Z"/>`;}
  const center=clockCenter(i,now);
  return `<svg class="ck" viewBox="0 0 360 360" data-drop="clock" data-day="${i}" role="img" aria-label="${t('Часы дня')}">`+
    `<defs>${defs}</defs>${body}${center}</svg>`;
}
Object.assign(ctx,{arc,textW,fitEllipsis,wrapWords,sectorLabel,clockSvg,ang,pt});
}
