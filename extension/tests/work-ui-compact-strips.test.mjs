import test from 'node:test';
import assert from 'node:assert/strict';
import {installDrag} from '../work-ui/drag.js';
import {installRepeatables} from '../work-ui/repeatables.js';
function harness(){
 const events={},strip={scrollLeft:0,scrollWidth:1030,clientWidth:309,getBoundingClientRect:()=>({top:100,bottom:180})};
 globalThis.document={elementFromPoint:()=>null,createElement:()=>({classList:{toggle(){}},style:{}}),body:{append(){}}};globalThis.window={};
 const ctx={U:{},D:{},DAY_FROM:480,DAY_SPAN:960,CUBE_MAX:240,HALF:720,PPD_MIN:5,PPD_MAX:40,clamp:(v,a,b)=>Math.max(a,Math.min(b,v)),listen:(_target,event,fn)=>events[event]=fn,cube:()=>({id:'x',title:'X',day:null,min:30}),paintDrop(){},idle(){},render(){},$:()=>null};
 installDrag(ctx);return {ctx,events,strip};
}
test('strip wheel consumes movement only while it can scroll, otherwise chains to page',()=>{
 const {events,strip}=harness();const wheel=delta=>{let prevented=false;events.wheel({target:{closest:s=>s==='[data-scroll-strip]'?strip:null},deltaY:delta,deltaX:0,ctrlKey:false,preventDefault(){prevented=true;}});return prevented;};
 assert.equal(wheel(-20),false);assert.equal(wheel(100),true);assert.equal(strip.scrollLeft,100);strip.scrollLeft=721;assert.equal(wheel(20),false);assert.equal(wheel(-20),true);
});
test('horizontal held drag scrolls and vertical exit switches to cube drag',()=>{
 const {ctx,strip}=harness();ctx.D.pdrag={id:'x',x:100,y:130,canDrag:true,src:{classList:{add(){}}},strip,scrollLeft:100,gesture:null};
 ctx.moveCube({clientX:70,clientY:130});assert.equal(strip.scrollLeft,130);assert.equal(ctx.D.pdrag.gesture,'scroll');
 ctx.moveCube({clientX:70,clientY:190});assert.equal(ctx.D.pdrag.gesture,'drag');assert.equal(ctx.D.pdrag.moved,true);
});

test('repeatable compact scrolling is confined to Day, while Week retains its wrapped shelf',()=>{
 const ctx={S:{repeatables:Array.from({length:5},(_,i)=>({id:String(i),title:'x',minutes:30})),cubes:[]},U:{screen:'day'},esc:String,keycap:k=>`<div data-cube="${k.id}"></div>`};installRepeatables(ctx);
 assert.match(ctx.repeatablesPanel(),/data-scroll-strip/);assert.match(ctx.repeatablesPanel(),/strip-more/);ctx.U.screen='week';assert.doesNotMatch(ctx.repeatablesPanel(),/data-scroll-strip|strip-more|tabindex/);delete ctx.U;assert.doesNotMatch(ctx.repeatablesPanel(),/data-scroll-strip/);
});
