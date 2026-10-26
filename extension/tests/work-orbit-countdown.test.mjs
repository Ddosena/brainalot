import test from 'node:test';
import assert from 'node:assert/strict';
import {orbitHoverMinute,orbitCountdownMinutes,upcomingNightDeadline,renderDayOrbit,mountOrbitCountdown,unwrapOrbitPointer} from '../work-day-orbit.js';

const polar=minute=>{
 const a=(minute-720)/720*Math.PI*2-Math.PI/2;
 return [180+112*Math.cos(a),180+112*Math.sin(a)];
};
const hover=(minute,now)=>orbitHoverMinute(...polar(minute),now);

test('upcoming 01:00 crosses midnight and rolls forward at exactly 01:00',()=>{
 assert.equal(upcomingNightDeadline(0),1500);
 assert.equal(upcomingNightDeadline(59),1500);
 assert.equal(upcomingNightDeadline(60),1500);
 assert.equal(upcomingNightDeadline(1439),1500);
 assert.equal(upcomingNightDeadline(-1),null);
});

test('future position follows the pointer and a past position stops at now',()=>{
 assert.ok(Math.abs(hover(1140,1080)-1140)<.001);
 assert.equal(hover(1020,1080),1080);
 assert.equal(orbitCountdownMinutes(1140,1080),360);
});

test('top to one means tomorrow 00–01 after 13:00',()=>{
 assert.ok(Math.abs(hover(750,1140)-1470)<.001);
 assert.equal(orbitCountdownMinutes(1470,1140),30);
 assert.equal(hover(780,1140),1500);
});

test('noon first hour stays today until 13:00',()=>{
 assert.ok(Math.abs(hover(765,750)-765)<.001);
 assert.equal(hover(735,750),750);
 assert.ok(Math.abs(hover(750,779)-779)<.001);
 assert.ok(Math.abs(hover(750,780)-1470)<.001);
});

test('during 00–01 the first hour is current night and cannot go backward',()=>{
 assert.ok(Math.abs(hover(750,30)-1470)<.001);
 assert.equal(hover(735,30),1470);
 assert.equal(hover(780,30),1500);
 assert.equal(orbitCountdownMinutes(1500,60),0);
});

test('morning now ray is now, its earlier neighbor clamps, and later neighbor advances',()=>{
 assert.equal(hover(780,60),60);
 assert.equal(hover(779,60),60);
 assert.ok(Math.abs(hover(781,60)-61)<.001);
 assert.equal(orbitCountdownMinutes(hover(780,60),60),1440);
 assert.equal(hover(1260,540),540);
 assert.equal(hover(1259,540),540);
 assert.ok(Math.abs(hover(1261,540)-541)<.001);
});

test('backward sweep stays clamped through 13 and 12 instead of escaping to tomorrow',()=>{
 const now=980;let previous=null;
 for(const dialMinute of [960,900,780,750,720,1439]){
  const xy=polar(dialMinute),unwrapped=unwrapOrbitPointer(...xy,now,previous);
  assert.equal(orbitHoverMinute(...xy,now,previous),now);
  previous=unwrapped;
 }
 assert.ok(previous<720);
 assert.ok(Math.abs(hover(750,now)-1470)<.001,'fresh entry still selects tomorrow 00:30');
});

test('forward sweep crosses 24:00 seam and reaches 01:00',()=>{
 const now=1140;let previous=null;
 for(const [dialMinute,expected] of [[1410,1410],[1439,1439],[721,1441],[750,1470],[780,1500]]){
  const xy=polar(dialMinute);
  assert.ok(Math.abs(orbitHoverMinute(...xy,now,previous)-expected)<.001);
  previous=unwrapOrbitPointer(...xy,now,previous);
 }
});

test('render carries selected date for mount to suppress non-today hover',()=>{
 assert.match(renderDayOrbit({date:'2026-10-26'}),/data-orbit-date="2026-10-26"/);
});

class FakeNode{
 constructor(name){this.name=name;this.attributes=new Map();this.children=[];this.listeners=new Map();this.textContent='';this.ownerDocument={createElementNS:(_ns,tag)=>new FakeNode(tag)};}
 setAttribute(key,value){this.attributes.set(key,String(value));}
 getAttribute(key){return this.attributes.get(key)??null;}
 removeAttribute(key){this.attributes.delete(key);}
 append(...nodes){for(const node of nodes){this.children.push(node);node.parent=this;}}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
 addEventListener(type,fn){this.listeners.set(type,fn);}
 removeEventListener(type,fn){if(this.listeners.get(type)===fn)this.listeners.delete(type);}
 dispatch(type,event={}){this.listeners.get(type)?.(event);}
}

test('mount starts at now, changes one sleep countdown on hover, resets and cleans up',()=>{
 const svg=new FakeNode('svg');
 svg.getScreenCTM=()=>({inverse:()=>({})});
 svg.createSVGPoint=()=>({x:0,y:0,matrixTransform(){return this;}});
 const section={dataset:{orbitDate:'2026-10-26'},matches:()=>true,querySelector:selector=>selector==='svg'?svg:null};
 let now='2026-10-26T15:00:00Z';
 const cleanup=mountOrbitCountdown(section,{getNow:()=>new Date(now)});
 const layer=svg.children[0],line=layer.children[0],label=layer.children[1];
 assert.equal(layer.getAttribute('hidden'),null);
 assert.equal(line.getAttribute('hidden'),'');
 assert.match(label.textContent,/До сна · 7 ч 00 мин/);
 assert.equal(layer.children.length,2,'one readout and no outer dot');
 const [clientX,clientY]=polar(1140);
 svg.dispatch('pointermove',{pointerType:'mouse',clientX,clientY});
 assert.equal(layer.getAttribute('hidden'),null);
 assert.equal(line.getAttribute('hidden'),null);
 for(const [x,y] of [[line.getAttribute('x1'),line.getAttribute('y1')],[line.getAttribute('x2'),line.getAttribute('y2')]]){
  const radius=Math.hypot(Number(x)-180,Number(y)-180);
  assert.ok(radius>=74.9&&radius<=83.1,`inner tick radius ${radius}`);
 }
 assert.match(label.textContent,/До сна · 6 ч 00 мин/);
 now='2026-10-26T15:05:00Z';
 svg.dispatch('pointerleave');
 assert.equal(layer.getAttribute('hidden'),null);
 assert.equal(line.getAttribute('hidden'),'');
 assert.match(label.textContent,/До сна · 6 ч 55 мин/);
 section.dataset.orbitDate='2026-10-25';
 svg.dispatch('pointermove',{pointerType:'mouse',clientX,clientY});
 assert.equal(layer.getAttribute('hidden'),'');
 section.dataset.orbitDate='2026-10-26';
 svg.dispatch('pointermove',{pointerType:'touch',clientX,clientY});
 assert.equal(layer.getAttribute('hidden'),null);
 assert.equal(line.getAttribute('hidden'),'');
 assert.match(label.textContent,/До сна · 6 ч 55 мин/);
 cleanup();
 assert.equal(svg.children.length,0);
 assert.equal(svg.listeners.size,0);
});

test('mount keeps backward drag at now and resets unwrapped anchor on leave',()=>{
 const svg=new FakeNode('svg');
 svg.getScreenCTM=()=>({inverse:()=>({})});
 svg.createSVGPoint=()=>({x:0,y:0,matrixTransform(){return this;}});
 const section={dataset:{orbitDate:'2026-10-26'},matches:()=>true,querySelector:()=>svg};
 const cleanup=mountOrbitCountdown(section,{getNow:()=>new Date('2026-10-26T13:20:00Z')});
 const layer=svg.children[0],label=layer.children[1];
 for(const minute of [960,900,780,750]){
  const [clientX,clientY]=polar(minute);
  svg.dispatch('pointermove',{pointerType:'mouse',clientX,clientY});
  assert.match(label.textContent,/До сна · 8 ч 40 мин/);
 }
 svg.dispatch('pointerleave');
 assert.match(label.textContent,/До сна · 8 ч 40 мин/);
 const [clientX,clientY]=polar(750);
 svg.dispatch('pointermove',{pointerType:'mouse',clientX,clientY});
 assert.match(label.textContent,/До сна · 30 мин/);
 cleanup();
});
