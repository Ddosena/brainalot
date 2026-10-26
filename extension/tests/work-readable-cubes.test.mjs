import {test} from 'node:test';
import assert from 'node:assert/strict';
import {READABLE_LAYOUTS,normalizeOrbitHalf,orbitHalfAt,sourceImportanceKey,sortSourceImportance,prioritizeSourcePools,sourceImportanceControl,fullCubeInfo,sourcePools,sourceCardContents,orderSourceCubes,approvedMonthlyPlan,validPlanMonth,approvedMonthlyFuture,readableCardDimensions,setReadableLayout,readableCardContents,compactCardMinutes,compactCardVariant,readableSource} from '../work-readable-cubes.js';
import {renderAwardArt} from '../work-trophy-art.js';
import {cubeDimensions,trayCapacity} from '../work-constructor-model.js';

test('chosen tangent dial half defaults to afternoon and only an explicit morning selects00–12',()=>{
  assert.equal(normalizeOrbitHalf(undefined),'afternoon');assert.equal(normalizeOrbitHalf('inside'),'afternoon');assert.equal(normalizeOrbitHalf('afternoon'),'afternoon');assert.equal(normalizeOrbitHalf('morning'),'morning');
});

test('Now chooses the actual half at noon boundaries without inventing a time',()=>{
  assert.equal(orbitHalfAt(0),'morning');assert.equal(orbitHalfAt(719),'morning');assert.equal(orbitHalfAt(720),'afternoon');assert.equal(orbitHalfAt(1439),'afternoon');
  for(const invalid of [null,undefined,NaN,-1,1440,'60'])assert.equal(orbitHalfAt(invalid),null);
});

test('source control has all six canonical score options and unrelated sources remain read-only',()=>{
  const cube={id:'local',canonical_id:'canonical',minutes:45,progress_percent:35,actual_minutes:15};
  assert.equal(sourceImportanceKey(cube),'canonical');assert.equal(sourceImportanceKey({id:'x',blueprint_ref:'blueprint'}),'blueprint');
  const html=sourceImportanceControl(cube,0);assert.deepEqual([...html.matchAll(/<option value="(\d)"/g)].map(m=>m[1]),['0','1','2','3','4','5']);assert.ok(html.includes('data-field="source-importance"'));assert.ok(html.includes('data-focus-key="source-importance-canonical"'));assert.ok(!html.includes('data-ccube'));assert.ok(!html.includes('data-action'));
  const readonly=sourceImportanceControl({id:'calendar'},0,{editable:false});assert.ok(!readonly.includes('<select'));assert.ok(readonly.includes('Нет связанной задачи'));
  assert.ok(sourceImportanceControl(cube,5,{saving:true}).includes('disabled aria-busy="true"'));assert.ok(sourceImportanceControl(cube,5).includes('value="5" selected'));
});

test('lower pools sort descending and stable at ties while keeping zero cards, stage order and facts',()=>{
  const a={id:'a',progress_percent:35,actual_minutes:15},b={id:'b'},c={id:'c'},d={id:'d'},chain=[{id:'stage-first'},{id:'stage-second'}],pools={chains:[{task_id:'task',cubes:chain}],external:[a,b,c,d],future:[d,c,b,a]},values=new Map([['a',0],['b',5],['c',5],['d',1],['stage-second',5]]),before=structuredClone(pools);
  const result=prioritizeSourcePools(pools,cube=>values.get(cube.id)??0);
  assert.deepEqual(result.external.map(c=>c.id),['b','c','d','a']);assert.deepEqual(result.future.map(c=>c.id),['c','b','d','a']);assert.equal(result.external.length,4);assert.deepEqual(result.chains,pools.chains);assert.deepEqual(result.chains[0].cubes.map(c=>c.id),['stage-first','stage-second']);assert.deepEqual(pools,before);assert.equal(result.external.at(-1),a);
  assert.deepEqual(sortSourceImportance([a,b,c],()=>0),[a,b,c]);
});

test('bottom hover info preserves full meaning/source and actual metrics without changing cube facts',()=>{
  const cube={id:'cube',title:'Полное название сложного этапа без обрезки и без сокращённых слов',taskTitle:'Курс скульптинга и работа с силуэтом',minutes:45,progress_percent:35,actual_minutes:15,output:'Силуэт проверен в трёх ракурсах',comment:'Сохранить сравнение',steps:['Первый шаг','Второй шаг'],links:[{title:'Референс',url:'https://example.com/one'}],groups:[{title:'Материалы',links:[{title:'Модель',url:'https://example.com/two'}]}]},before=structuredClone(cube),text=fullCubeInfo(cube,{importance:4});
  for(const value of [cube.title,cube.taskTitle,'План: 45 мин','Готовность: 35%','Факт: 15 мин','Важность: 4/5',cube.output,cube.comment,'Шагов: 2','Материалов: 2'])assert.ok(text.includes(value));assert.deepEqual(cube,before);
  assert.ok(fullCubeInfo({...cube,progress_percent:100,actual_minutes:0},{importance:0}).includes('Факт: 0 мин'));assert.ok(fullCubeInfo(cube).includes('Важность: 0/5'));
});

test('hover info remains plain text, keeps long names and summarizes optional verbose details',()=>{
  const title='<img src=x onerror=alert(1)> '+('Длинное название '.repeat(50)),source='<script>source</script>',text=fullCubeInfo({title,taskTitle:source,minutes:60,progress_percent:0,actual_minutes:0,output:'x'.repeat(201),comment:'y'.repeat(201),resource_drafts:{links:[{title:'Материал',url:'https://example.com'}],groups:[]}});
  assert.ok(text.includes(title));assert.ok(text.includes(source));assert.ok(text.includes('Материалов: 1'));assert.ok(!text.includes('x'.repeat(201)));assert.ok(!text.includes('y'.repeat(201)));
});

test('responsive tray grows for rows and gives every card a distinct in-bounds slot',()=>{
  for(const layout of READABLE_LAYOUTS)for(const width of [350,900])for(const count of [2,5]){
    setReadableLayout(layout.id);const size=cubeDimensions(45),items=Array.from({length:count},()=>({minutes:45})),capacity=trayCapacity(width,items,170);
    const boxes=items.map((_,i)=>({x:8+i%capacity.columns*capacity.cellWidth,y:8+Math.floor(i/capacity.columns)*capacity.rowHeight,...size}));
    for(const box of boxes){assert.ok(box.x+box.width<=width);assert.ok(box.y+box.height<=capacity.height);}
    for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);}
  }
  setReadableLayout('cards');assert.equal(trayCapacity(900,[{minutes:45},{minutes:45}],170).height,170);
  assert.ok(trayCapacity(350,Array.from({length:8},()=>({minutes:45})),170).height>170);setReadableLayout('compact');
});

test('approved compact footprint is shared by the model and responsive cards',()=>{
  assert.deepEqual(READABLE_LAYOUTS,[{id:'compact',label:'Компактные блоки',width:89,height:62}]);
  for(const obsolete of ['cards','wide','rows',null]){assert.equal(setReadableLayout(obsolete),'compact');assert.deepEqual(cubeDimensions(45),{width:89,height:62});}
  assert.deepEqual(readableCardDimensions('compact','day',390),{width:86,height:61});assert.deepEqual(readableCardDimensions('compact','day',1260),{width:89,height:62});
});
test('three source pools coexist, retain selected chain places and move explicitly chosen future tasks without duplicates',()=>{
  const date='2026-10-24',planning={tasks:[{id:'a',title:'Скульптура'},{id:'b',title:'Печать'},{id:'future',title:'Будущий проект'}],task_segments:[{id:'a1',task_id:'a'},{id:'a2',task_id:'a'},{id:'b1',task_id:'b'}],monthly_plan:{id:'0b2bf8c5-26ab-4b22-a844-a45a619e6079',month:'2026-10',status:'approved',approved_at:'2026-10-24T07:00:00Z',task_ids:['future']}},cubes=[{id:'a2',kind:'task',task_id:'a',segment_id:'a2',progress_percent:0},{id:'note',kind:'brainalot',sourceLabel:'На неделе',progress_percent:0},{id:'future-cube',kind:'task',task_id:'future',planned_date:'2026-10-27',progress_percent:0},{id:'b1',kind:'task',task_id:'b',segment_id:'b1',progress_percent:0},{id:'a1',kind:'task',task_id:'a',segment_id:'a1',progress_percent:35,actual_minutes:15},{id:'alias',canonical_id:'a1',kind:'task',task_id:'a',segment_id:'a1',progress_percent:0},{id:'prep',kind:'prep',task_id:'a',planned_date:date},{id:'ordinary',kind:'task',task_id:'not-approved',planned_date:'2026-10-28',progress_percent:0}];
  const before=structuredClone(cubes),pools=sourcePools({planning,date,taskIds:['a','b'],cubes,selectedIds:['a1']});
  assert.deepEqual(Object.keys(pools),['chains','external','future']);assert.deepEqual(pools.chains.map(g=>g.title),['Скульптура','Печать']);assert.deepEqual(pools.chains[0].cubes.map(c=>c.id),['a1','a2']);assert.equal(pools.chains[0].cubes[0].actual_minutes,15);assert.equal(pools.chains[0].cubes[0].progress_percent,35);assert.deepEqual(pools.external.map(c=>c.id),['note','prep']);assert.deepEqual(pools.future.map(c=>c.id),['future-cube']);assert.deepEqual(cubes,before);
  const taken=sourcePools({planning,date,taskIds:['a','b'],cubes,selectedIds:['future-cube']});assert.deepEqual(taken.future.map(c=>c.id),['future-cube']);
  const chosen=sourcePools({planning,date,taskIds:['a','b','future'],cubes});assert.deepEqual(chosen.future,[]);assert.deepEqual(chosen.chains[2].cubes.map(c=>c.id),['future-cube']);
});
test('future provenance requires the actual server-approved monthly shape and known task membership',()=>{
  const planning={tasks:[{id:'yes'}],monthly_plan:{id:'0b2bf8c5-26ab-4b22-a844-a45a619e6079',month:'2026-10',status:'approved',approved_at:'2026-10-24T10:00:00Z',task_ids:['yes']}},cube={task_id:'yes',planned_date:'2026-10-27'};
  assert.equal(approvedMonthlyPlan(planning),planning.monthly_plan);assert.equal(approvedMonthlyFuture(cube,planning,'2026-10-24'),true);
  for(const invalid of [{...cube,task_id:'no'},{...cube,planned_date:null},{...cube,planned_date:'2026-10-24'},{...cube,planned_date:'2026-11-01'},{...cube,planned_date:'2026-10-99'}])assert.equal(approvedMonthlyFuture(invalid,planning,'2026-10-24'),false);
  for(const plan of [{...planning.monthly_plan,status:'draft'},{...planning.monthly_plan,synthetic_only:true},{...planning.monthly_plan,id:'fixture-month'},{...planning.monthly_plan,task_ids:['yes','yes']},{...planning.monthly_plan,task_ids:['unknown']},{...planning.monthly_plan,approved_at:'2026-10-24T10:00:00+03:00'},{...planning.monthly_plan,approved_at:null}]){
    assert.equal(approvedMonthlyPlan({...planning,monthly_plan:plan}),null);assert.equal(approvedMonthlyFuture(cube,{...planning,monthly_plan:plan},'2026-10-24'),false);
  }
  assert.equal(approvedMonthlyFuture(cube,{},'2026-10-24'),false);
  for(const value of ['2026-01','2026-12'])assert.equal(validPlanMonth(value),true);
  for(const value of ['2026-00','2026-13','2026-1','0000-01',null])assert.equal(validPlanMonth(value),false);
});

test('source ordering follows phase/sequence facts and stable input fallback without guessing from titles',()=>{
  const cubes=[{id:'z',title:'Я',segment_id:'s2'},{id:'y',title:'Я',segment_id:'s1',sequence:2},{id:'x',title:'А',segment_id:'s1',sequence:1},{id:'free-b',title:'Б'},{id:'free-a',title:'А'}];
  assert.deepEqual(orderSourceCubes(cubes,[{id:'s1'},{id:'s2'}]).map(c=>c.id),['x','y','z','free-b','free-a']);
  assert.deepEqual(orderSourceCubes([{id:'second',phase_order:2},{id:'first',phase_order:1}]).map(c=>c.id),['first','second']);
});
test('excluded protected/returned task cubes remain discoverable and explicitly hidden cubes stay hidden',()=>{
  const cubes=[{id:'started',kind:'task',task_id:'removed',progress_percent:35,actual_minutes:15},{id:'returned',kind:'task',task_id:'removed',progress_percent:0},{id:'untouched',kind:'task',task_id:'removed',progress_percent:0},{id:'hidden',kind:'task',task_id:'removed',progress_percent:100}];
  const pools=sourcePools({date:'2026-10-24',taskIds:[],cubes,restoredIds:['returned'],hiddenIds:['hidden']});assert.equal(pools.chains[0].retained,true);assert.deepEqual(pools.chains[0].cubes.map(c=>c.id),['started','returned']);
});
test('source cards keep action and duration without duplicate source or percentage labels',()=>{
  const cube={title:'Собрать референсы персонажа в трёх ракурсах',taskTitle:'Первая скульптура',minutes:60,progress_percent:35,type:'research'},html=sourceCardContents(cube,{selected:true});
  assert.ok(html.includes(cube.title));assert.ok(!html.includes(cube.taskTitle));assert.ok(html.includes('В дне'));assert.ok(html.includes('width:35%'));assert.ok(html.includes('60 мин'));assert.ok(!html.includes('token-meta'));assert.ok(!html.includes('35%</'));assert.ok(!html.includes('<time'));
});
test('compact card reuses exact existing award SVG and source structure, with fill instead of percent text',()=>{
  const cube={id:'opaque-id',title:'Уточнить силуэт персонажа',taskTitle:'Первая скульптура',type:'practice',minutes:45,actual_minutes:15,progress_percent:35,award_variant:'order'};
  const html=readableCardContents(cube,{typeLabel:'Практика',duration:'45 мин'});assert.ok(html.includes(cube.title));assert.ok(html.includes('token small-award-token'));assert.ok(html.includes('compact-award-top'));assert.ok(html.includes('tiny-title'));assert.ok(html.includes('token-meta'));assert.ok(html.includes('45 мин'));assert.ok(html.includes('width:35%'));assert.ok(!html.includes('35%</'));assert.ok(!html.includes('rc-source'));assert.ok(!html.includes('rc-state'));assert.ok(!html.includes('class="ic"'));
  assert.ok(html.includes(renderAwardArt('order',{compact:true,categoryColor:'#70bdb4'})));assert.equal(compactCardVariant(cube),'order');assert.equal(compactCardVariant({...cube,progress_percent:100}),'order');
  assert.equal(compactCardMinutes({progress_percent:100,minutes:60,actual_minutes:53}),53);assert.equal(compactCardMinutes({progress_percent:100,minutes:45,actual_minutes:0}),0);assert.equal(compactCardMinutes(cube),45);
  assert.equal(readableSource(cube),'Первая скульптура');assert.equal(readableSource({kind:'calendar',title:'Урок'}),'Календарь');assert.equal(readableSource({kind:'brainalot',sourceLabel:'На неделе'}),'Brainalot · На неделе');
});
