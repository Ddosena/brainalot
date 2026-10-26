import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDayOrbit,orbitArcPath,lessonTextPath,lessonTitle,wrapLessonTitle,fitOrbitLabels} from '../work-day-orbit.js';

const event=(id,start,minutes,title)=>({id,start,minutes,title,required:true,color:'#e8b55b'});
const measure=(value,font)=>Array.from(value).length*font*.54;
const lengths=minutes=>[132,110].map(radius=>radius*minutes/720*Math.PI*2);

test('normal lessons keep readable two-line subjects at 60, 90 and 120 minutes',()=>{
  for(const minutes of [60,90,120]){
    for(const title of ['моделирование','разбор работ']){
      const layout=wrapLessonTitle(title,lengths(minutes),measure);
      assert.equal(layout.truncated,false,`${minutes} ${title}`);
      assert.ok(layout.font>=13);
      assert.equal(layout.lines.join('').replace(/[-\s]/g,''),title.replace(/\s/g,''));
      if(title==='разбор работ'&&minutes===60)assert.deepEqual(layout.lines,['разбор','работ']);
    }
  }
});

test('short and long Cyrillic titles stay bounded and escaped',()=>{
  assert.equal(lessonTitle('Урок: разбор работ'),'разбор работ');
  assert.equal(wrapLessonTitle('Лепка',lengths(90),measure).lines[0],'Лепка');
  const tiny=wrapLessonTitle('очень длинное название обязательного занятия',lengths(15),measure);
  assert.equal(tiny.truncated,true);
  assert.equal(tiny.lines[1],'');
  const html=renderDayOrbit({date:'2026-10-26',events:[event('quoted',720,60,'Урок: Лепка <лица> & рук')],today:false});
  assert.match(html,/Лепка &lt;лица&gt; &amp; рук/);
  assert.doesNotMatch(html,/Лепка <лица>/);
});

test('fixed sectors preserve exact time angle and unique per-render text paths',()=>{
  const args={date:'2026-10-26',events:[event('a',720,60,'Урок: моделирование'),event('b',1080,90,'Урок: разбор работ')],today:true,nowMinutes:980,cubes:[{id:'work',start:840,minutes:60,title:'Практика',color:'#70bdb4'}]};
  const one=renderDayOrbit(args),two=renderDayOrbit(args);
  assert.match(one,/data-start="720" data-minutes="60"/);
  assert.match(one,/data-start="1080" data-minutes="90"/);
  assert.ok(one.includes(`d="${orbitArcPath(720,780)}"`));
  assert.ok(one.includes(`d="${orbitArcPath(1080,1170)}"`));
  assert.notEqual(one.match(/id="(cc-orbit-title-[^"]+)"/)?.[1],two.match(/id="(cc-orbit-title-[^"]+)"/)?.[1]);
  assert.match(one,/data-action="inspect" data-id="work"/);
  assert.match(one,/data-now="980"/);
  assert.match(one,/<path class="cc-day-orbit-sector is-fixed"[^>]*><title>12:00–13:00 · Урок: моделирование<\/title><\/path>/);
  assert.doesNotMatch(one,/<span class="cc-orbit-lesson"|<time>/);
  assert.doesNotMatch(one,/data-half="morning"/);
});

test('bottom-half labels reverse direction and boundary sectors are clipped',()=>{
  assert.match(lessonTextPath(1080,1140,720,1440,100,true),/ 0 0 /);
  const html=renderDayOrbit({date:'2026-10-26',events:[event('cross-noon',690,60,'Урок: начало'),event('cross-midnight',1410,60,'Урок: конец')]});
  assert.ok(html.includes(`d="${orbitArcPath(720,750)}"`));
  assert.ok(html.includes(`d="${orbitArcPath(1410,1440)}"`));
  assert.match(html,/data-start="690" data-minutes="60"/);
  assert.match(html,/data-start="1410" data-minutes="60"/);
  assert.doesNotMatch(html,/>00–12<|>12–24</);
});

test('side arcs turn text upright at 16:00 and keep 22:00 clockwise',()=>{
  const html=renderDayOrbit({date:'2026-10-26',events:[event('sixteen',960,60,'моделирование'),event('twentytwo',1320,60,'разбор работ')]});
  const first=html.match(/data-lesson-id="sixteen"[\s\S]*?<defs><path id="[^"]+" d="([^"]+)"/)?.[1];
  const second=html.match(/data-lesson-id="twentytwo"[\s\S]*?<defs><path id="[^"]+" d="([^"]+)"/)?.[1];
  assert.equal(first,lessonTextPath(960,1020,720,1440,110,true));
  assert.equal(second,lessonTextPath(1320,1380,720,1440,132,false));
});

test('adaptive labels keep roomy incumbent layout and rotate only tight work and lessons',()=>{
 const html=renderDayOrbit({date:'2026-10-26',events:[
  event('roomy-lesson',1080,90,'Урок: разбор работ'),
  event('tight-lesson',1380,20,'Урок: подготовка к лепке')
 ],cubes:[
  {id:'roomy-work',start:900,minutes:90,title:'Печать'},
  {id:'tight-work',start:1020,minutes:20,title:'Подготовить урок по Blender'}
 ]});
 assert.match(html,/data-lesson-layout="adaptive"/);
 assert.match(html,/data-orbit-id="roomy-work"[^>]*data-orbit-label-mode="tangent"/);
 assert.match(html,/data-orbit-id="tight-work"[^>]*data-orbit-label-mode="radial"/);
 assert.match(html,/data-lesson-id="roomy-lesson"[^>]*data-orbit-label-mode="tangent"|data-orbit-label-mode="tangent"[^>]*data-lesson-id="roomy-lesson"/);
 assert.match(html,/data-lesson-id="tight-lesson"[^>]*data-orbit-label-mode="radial"|data-orbit-label-mode="radial"[^>]*data-lesson-id="tight-lesson"/);
 assert.match(html,/data-orbit-label-full="Подготовить урок по Blender"/);
 assert.match(html,/Открыть: 17:00–17:20 · Подготовить урок по Blender/);
 assert.match(html,/<title>Урок: подготовка к лепке · 23:00–23:20<\/title>/);
 assert.match(html,/cc-orbit-radial-title[^>]*rotate\(/);
 assert.doesNotMatch(html,/<text[^>]*>[^<]*<script>/);
 const radial=html.match(/data-orbit-id="tight-work"[\s\S]*?cc-orbit-radial-title" data-line="0" transform="translate\(([-\d.]+) ([-\d.]+)\)/);
 assert.ok(radial,'tight work has radial text');
 assert.ok(Math.abs(Math.hypot(Number(radial[1])-180,Number(radial[2])-180)-128)<1,'radial label center is outside the center readout');
});

test('actual SVG text measurement can switch a predicted tangent label to radial',()=>{
 const originalDocument=globalThis.document;
 const node=()=>({style:{},attrs:new Set(),textContent:'',setAttribute(key){this.attrs.add(key);},removeAttribute(key){this.attrs.delete(key);},hasAttribute(key){return this.attrs.has(key);},toggleAttribute(key,value){value?this.attrs.add(key):this.attrs.delete(key);}});
 const tangentText={...node(),getComputedTextLength(){return 120;},querySelector(){return null;}};
 const radialTexts=[node(),node()].map(text=>({...text,getComputedTextLength(){return this.textContent.length*12;}}));
 const tangent={...node(),querySelectorAll:()=>[tangentText]};
 const radial={...node(),querySelectorAll:()=>radialTexts};
 const group={dataset:{orbitLabelFull:'Печать',orbitLabelKind:'work',orbitTangentWidth:'100'},querySelector:selector=>selector.includes('tangent')?tangent:radial};
 globalThis.document={createElement:()=>({getContext:()=>({font:'',measureText:value=>({width:value.length*5})})})};
 try{
  const result=fitOrbitLabels({querySelectorAll:()=>[group]});
  assert.equal(result[0].mode,'radial');
  assert.equal(group.dataset.orbitLabelMode,'radial');
  assert.equal(tangent.hasAttribute('hidden'),true);
  assert.equal(radial.hasAttribute('hidden'),false);
  assert.ok(radialTexts[0].getComputedTextLength()<=70,'measured radial line stays inside the 70px allowance');
  assert.match(radialTexts[0].textContent,/^Печ/);
 }finally{globalThis.document=originalDocument;}
});
