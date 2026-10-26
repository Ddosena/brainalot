import {cubeDimensions} from './work-constructor-model.js';

export function slotLayout(width,count,size,{padding=8,gap=8,border=1}={}){
  const columns=Math.max(1,Math.floor((Math.max(0,width)-2*padding+gap)/(size.width+gap)));
  const rows=Math.max(1,Math.ceil(count/columns));
  const height=2*(padding+border)+rows*size.height+(rows-1)*gap;
  const positions=Array.from({length:count},(_,index)=>({x:border+padding+(index%columns)*(size.width+gap),y:border+padding+Math.floor(index/columns)*(size.height+gap),angle:0}));
  return {columns,rows,height,positions};
}

export function shouldUnstickPanel(panelHeight,viewportHeight,stickyTop,bottomGap=12){
  if(![panelHeight,viewportHeight,stickyTop].every(Number.isFinite))return false;
  return panelHeight>Math.max(0,viewportHeight-stickyTop-bottomGap);
}

export function createTraySlots(container,{variant='day'}={}){
  const elements=[...container.querySelectorAll('[data-ccube]')];
  const size=cubeDimensions(Number(elements[0]?.dataset.minutes)||0,variant);
  const width=container.clientWidth;
  const layout=slotLayout(width,elements.length,size);
  container.classList.add('cc-slot-tray');
  container.style.gridTemplateColumns=`repeat(${layout.columns}, ${size.width}px)`;
  container.style.height=layout.height+'px';
  container.dataset.slotRows=String(layout.rows);
  const positions=Object.fromEntries(elements.map((element,index)=>[element.dataset.ccube,layout.positions[index]]));
  return {width,layout,snapshot:()=>structuredClone(positions),resize(){},pause(){},wake(){},destroy(){}};
}
