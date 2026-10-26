import test from 'node:test';
import assert from 'node:assert/strict';
import {slotLayout,createTraySlots,shouldUnstickPanel} from '../work-constructor-slots.js';

const size={width:89,height:62};
for(const width of [260,390,820]){
  for(const count of [0,1,8,10,24,40]){
    test(`slot layout ${width}px / ${count} cubes`,()=>{
      const layout=slotLayout(width,count,size);
      assert.equal(layout.positions.length,count);
      assert.ok(layout.rows>=1);
      assert.ok(layout.height>=2*(8+1)+size.height);
      for(let i=0;i<count;i++){
        const item=layout.positions[i];
        assert.equal(item.angle,0);
        assert.ok(item.x>=9&&item.y>=9);
        assert.ok(item.x+size.width<=width-7);
        assert.ok(item.y+size.height<=layout.height-8);
        if(i>0){const prior=layout.positions[i-1];assert.ok(item.y>=prior.y+size.height+8||item.x>=prior.x+size.width+8);}
      }
      if(count>layout.columns)assert.ok(layout.rows>1);
      assert.equal(layout.height,2*(8+1)+layout.rows*size.height+(layout.rows-1)*8);
    });
  }
}

test('slot adapter ignores legacy coordinates and never reports canonical positions',()=>{
  const items=Array.from({length:10},(_,index)=>({dataset:{ccube:'cube-'+index,minutes:'60'}}));
  const classes=new Set(),container={clientWidth:390,querySelectorAll:()=>items,classList:{add:name=>classes.add(name)},style:{},dataset:{}};
  let writes=0;
  const instance=createTraySlots(container,{positions:{'cube-0':{x:999,y:999,angle:42}},onSettled:()=>writes++});
  assert.ok(classes.has('cc-slot-tray'));
  assert.equal(instance.snapshot()['cube-0'].x,9);
  assert.equal(instance.snapshot()['cube-0'].angle,0);
  assert.equal(writes,0);
  assert.equal(Number.parseInt(container.style.height),instance.layout.height);
  instance.pause();instance.wake();instance.resize('cube-0',size);instance.destroy();
  assert.equal(writes,0);
});

test('sticky panel is released only when its full outer height exceeds available viewport',()=>{
  assert.equal(shouldUnstickPanel(520,900,160),false);
  assert.equal(shouldUnstickPanel(740,900,160),true);
  assert.equal(shouldUnstickPanel(740,900,Number.NaN),false);
});
