import test from 'node:test';
import assert from 'node:assert/strict';
import {readableCardDimensions,sourceCardContents,readableCardContents,hoverCubeContents,hoverCubePosition} from '../work-readable-cubes.js';
import {cubeDimensions,trayCapacity} from '../work-constructor-model.js';

const cube={id:'sample',title:'Подготовить подробный план занятия',minutes:60,progress_percent:0,kind:'task',output:'Согласованный конспект'};

test('execution geometry feeds the same footprint to tray capacity',()=>{
  assert.deepEqual(readableCardDimensions('compact','day',1200),{width:89,height:62});
  assert.deepEqual(readableCardDimensions('compact','day',390),{width:86,height:61});
  assert.deepEqual(cubeDimensions(60),readableCardDimensions());
  assert.equal(trayCapacity(260,[cube,cube]).cellWidth,97);
});

test('source face carries the action and duration without duplicate source label',()=>{
  const html=sourceCardContents(cube,{source:'Course A'});
  assert.match(html,/Подготовить подробный план занятия/);
  assert.match(html,/60 мин/);
  assert.doesNotMatch(html,/Course A/);
});

test('visible completion mark appears only at full completion',()=>{
  assert.doesNotMatch(readableCardContents(cube),/rc-done-check/);
  assert.match(readableCardContents({...cube,progress_percent:100}),/rc-done-check/);
  assert.doesNotMatch(sourceCardContents(cube),/rc-done-check/);
  assert.match(sourceCardContents({...cube,progress_percent:100}),/rc-done-check/);
  assert.doesNotMatch(readableCardContents({...cube,progress_percent:100,status:'cancelled'}),/rc-done-check/);
});

test('expanded cube shows title, context and short result inside viewport',()=>{
  const html=hoverCubeContents(cube,{typeLabel:'Задача'});
  assert.match(html,/Подготовить подробный план занятия/);
  assert.match(html,/Согласованный конспект/);
  assert.equal((hoverCubeContents({...cube,taskTitle:cube.title}).match(/Подготовить подробный план занятия/g)||[]).length,1);
  const pos=hoverCubePosition({left:375,top:580,width:123,height:92},{width:390,height:640},{height:180});
  assert.equal(pos.width,236);
  assert.ok(pos.left>=8&&pos.left+pos.width<=382);
  assert.ok(pos.top>=8&&pos.top+180<=632);
});
