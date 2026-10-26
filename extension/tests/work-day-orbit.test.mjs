import test from 'node:test';
import assert from 'node:assert/strict';
import {currentDayPosition,orbitArcPath,renderDayOrbit,lessonTextPath,lessonTitle,wrapLessonTitle,orbitHalfCounts} from '../work-day-orbit.js';
test('Moscow midnight belongs to the new day',()=>{
 assert.deepEqual(currentDayPosition('2026-10-24',new Date('2026-10-23T21:30:00Z')),{today:true,nowMinutes:30,label:'00:30'});
 assert.equal(currentDayPosition('2026-10-23',new Date('2026-10-23T21:30:00Z')).today,false);
});
test('original afternoon dial has12top15right18bottom21left',()=>{
 const html=renderDayOrbit({date:'2026-10-24'});
 const labels=[...html.matchAll(/data-hour="(\d+)" x="([\d.]+)" y="([\d.]+)"/g)].map(m=>m.slice(1).map(Number));
 assert.deepEqual(labels.map(x=>x[0]),[12,15,18,21]);
 for(const [i,x,y] of [[0,180,12],[1,348,180],[2,180,348],[3,12,180]]){assert(Math.abs(labels[i][1]-x)<.001);assert(Math.abs(labels[i][2]-y)<.001);}
});
test('half-day projection never overlays morning and evening data',()=>{
 const cubes=[{id:'morning',title:'Силуэт',start:570,minutes:45},{id:'evening',title:'Печать',start:1290,minutes:45},{id:'noon',title:'Стык',start:690,minutes:60},{id:'floating',start:null,minutes:60}];
 assert.deepEqual(orbitHalfCounts([],cubes),{morning:2,afternoon:2});
 const am=renderDayOrbit({cubes,half:'morning'}),pm=renderDayOrbit({cubes});
 assert.match(am,/data-orbit-id="morning" data-start="570" data-minutes="45"/);assert.doesNotMatch(am,/data-orbit-id="evening"/);
 assert.match(pm,/data-orbit-id="evening"/);assert.doesNotMatch(pm,/data-orbit-id="morning"|data-orbit-id="floating"/);
 assert.match(am,/data-orbit-id="noon"/);assert.match(pm,/data-orbit-id="noon"/);
});
test('external clock marker remains visible in both halves on the actual day',()=>{
 for(const nowMinutes of [0,30,420,1380]){const half=nowMinutes<720?'morning':'afternoon',other=half==='morning'?'afternoon':'morning',same=renderDayOrbit({today:true,nowMinutes,half}),opposite=renderDayOrbit({today:true,nowMinutes,half:other});assert.match(same,new RegExp('data-now="'+nowMinutes+'"'));assert.match(opposite,new RegExp('data-now="'+nowMinutes+'" data-now-half="'+half+'" data-other-half="true"'));assert.doesNotMatch(opposite,/cc-day-orbit-clock-period/);assert.equal(same.match(/data-other-half="false"[^>]*transform="([^"]+)"/)[1],opposite.match(/data-other-half="true"[^>]*transform="([^"]+)"/)[1]);}
 assert.doesNotMatch(renderDayOrbit({today:false,nowMinutes:900}),/cc-day-orbit-hand-tip/);
});
test('visible current marker has a readable label and moves with clock minutes',()=>{
 const first=renderDayOrbit({today:true,half:'morning',nowMinutes:90}),next=renderDayOrbit({today:true,half:'morning',nowMinutes:91});
 assert.match(first,/aria-label="Текущее время 01:30 · 00–12"/);assert.match(first,/L -8 -15 L 8 -15/);
 assert.notEqual(first.match(/data-now="90"[^>]*transform="([^"]+)"/)[1],next.match(/data-now="91"[^>]*transform="([^"]+)"/)[1]);
});
test('fixed lessons use curved subjects on the correct time sectors; work has letters',()=>{
 const html=renderDayOrbit({events:[{id:'lesson',title:'Урок · Моделирование',required:true,start:720,minutes:60},{id:'optional',title:'Встреча',required:false,start:840,minutes:30}],cubes:[{id:'placed',title:'Уточнить силуэт и пропорции',start:900,minutes:45}]});
 assert.match(html,/data-orbit-lesson-title="Моделирование"/);assert.match(html,/<textPath /);assert.match(html,/<title>Урок · Моделирование · 12:00–13:00<\/title>/);assert.match(html,/cc-orbit-work-label/);assert.match(html,/силуэт/);assert.doesNotMatch(html,/data-orbit-id="optional"|cc-orbit-key|Сегодня|Сейчас/);
 assert.equal(lessonTitle('Урок: разбор работ'),'разбор работ');
 const widths=[132,110].map(r=>r*60/720*Math.PI*2),measure=(value,font)=>Array.from(value).length*font*.54;
 assert.deepEqual(wrapLessonTitle('разбор работ',widths,measure).lines,['разбор','работ']);
 assert.match(lessonTextPath(1080,1140,720,1440,100,true),/ 0 0 /);
});
test('arc geometry reacts to real movement, clipping and resizing',()=>{
 assert.notEqual(orbitArcPath(900,945),orbitArcPath(900,990));assert.notEqual(orbitArcPath(900,990),orbitArcPath(1080,1170));assert.equal(orbitArcPath(600,600),'');assert.equal((orbitArcPath(720,1440).match(/ A /g)||[]).length,2);
});
test('lesson content is escaped and fixed sector retains exact bounds',()=>{
 const html=renderDayOrbit({events:[{id:'x',title:'<script>',required:true,start:720,minutes:30,color:'url(evil)'}]});assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/url\(evil\)/);
 assert.match(html,/data-start="720" data-minutes="30"/);assert.ok(html.includes(`d="${orbitArcPath(720,750)}"`));
});
