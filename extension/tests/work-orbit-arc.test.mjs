import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDayOrbit,orbitArcPath,lessonTextPath,lessonTitle,wrapLessonTitle} from '../work-day-orbit.js';

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
