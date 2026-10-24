import { clamp, cubeDimensions, trayCapacity } from "./work-constructor-model.js?v=constructor-20261023-a";
export function physicsWorld(items,width,height,Matter=globalThis.Matter){
  if(!Matter)throw new Error("Matter.js is unavailable");
  const {Engine,Bodies,Composite,Body,Sleeping}=Matter,engine=Engine.create({enableSleeping:true,positionIterations:8,velocityIterations:6});
  engine.gravity.y=1;engine.gravity.scale=.001;
  const walls=[Bodies.rectangle(width/2,height+22,width+100,48,{isStatic:true,friction:.8}),Bodies.rectangle(-24,height/2,48,height+100,{isStatic:true}),Bodies.rectangle(width+24,height/2,48,height+100,{isStatic:true}),Bodies.rectangle(width/2,-40,width+100,40,{isStatic:true})],bodies=new Map();
  Composite.add(engine.world,walls);
  for(const [index,item] of items.entries()){
    const w=item.width,h=item.height,p=item.position,inside=p&&p.x>=2&&p.x+w<=width-2&&p.y>=2&&p.y+h<=height-1.9;
    const x=Number.isFinite(p?.x)?p.x:clamp(8+(index%Math.max(1,Math.floor((width-8)/(w+8))))*(w+8),3,width-w-3),y=Number.isFinite(p?.y)?p.y:clamp(8+Math.floor(index/Math.max(1,Math.floor((width-8)/(w+8))))*(h+8),3,height-h-8);
    const body=Bodies.rectangle(x+w/2,y+h/2,w,h,{label:item.id,friction:.65,frictionStatic:.9,frictionAir:.025,restitution:.08,sleepThreshold:40,chamfer:{radius:2}});
    body.ccWidth=w;body.ccHeight=h;Body.setAngle(body,0);Body.setInertia(body,Infinity);
    const rotatedInside=body.bounds.min.x>=2&&body.bounds.max.x<=width-2&&body.bounds.min.y>=1&&body.bounds.max.y<=height-1.9;const dx=body.bounds.min.x<2?2-body.bounds.min.x:body.bounds.max.x>width-2?width-2-body.bounds.max.x:0,dy=body.bounds.min.y<1?1-body.bounds.min.y:body.bounds.max.y>height-2?height-2-body.bounds.max.y:0;if(dx||dy)Body.translate(body,{x:dx,y:dy});if(p&&rotatedInside&&!item.awake)Sleeping.set(body,true);else Body.setAngularVelocity(body,0);
    bodies.set(item.id,body);Composite.add(engine.world,body);
  }
  return {engine,bodies,width,height,Matter};
}
export function stepPhysics(world,delta=1000/60){
  const {Engine,Body}=world.Matter;Engine.update(world.engine,Math.min(1000/60,Math.max(1,delta)));
  for(const body of world.bodies.values()){
    const dx=body.bounds.min.x<2?2-body.bounds.min.x:body.bounds.max.x>world.width-2?world.width-2-body.bounds.max.x:0;
    const dy=body.bounds.min.y<1?1-body.bounds.min.y:body.bounds.max.y>world.height-1.9?world.height-2-body.bounds.max.y:0;
    if(dx||dy)Body.translate(body,{x:dx,y:dy});
  }
}
export function physicsPositions(world){return Object.fromEntries([...world.bodies].map(([id,b])=>[id,{x:Math.round((b.position.x-b.ccWidth/2)*100)/100,y:Math.round((b.position.y-b.ccHeight/2)*100)/100,angle:Math.round((((b.angle*180/Math.PI+180)%360+360)%360-180)*100)/100}]));}
export function createTrayPhysics(container,options={}){
  const Matter=options.Matter||globalThis.Matter,elements=[...container.querySelectorAll("[data-ccube]")],variant=options.variant||"day",items=elements.map(el=>{const id=el.dataset.ccube,size=cubeDimensions(Number(el.dataset.minutes),variant);return {id,...size,position:options.positions?.[id],awake:options.awakeIds?.includes(id)};});
  const width=container.clientWidth,height=container.clientHeight,reduced=globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  let world,frame=null,disposed=false,visible=true,started=0,steps=0,lastPersisted="",observer;
  const apply=()=>{if(!world)return;for(const el of elements){const b=world.bodies.get(el.dataset.ccube);if(!b)continue;el.style.left="0px";el.style.top="0px";el.style.transform="translate("+(b.position.x-b.ccWidth/2)+"px,"+(b.position.y-b.ccHeight/2)+"px) rotate("+b.angle+"rad)";}};
  const save=()=>{if(!world)return;const positions=physicsPositions(world),serialized=JSON.stringify(positions);if(serialized!==lastPersisted){lastPersisted=serialized;options.onSettled?.(positions);}};
  function stop(){if(frame!==null)cancelAnimationFrame(frame);frame=null;}
  function tick(now){
    frame=null;if(disposed||!visible||globalThis.document?.hidden)return;
    if(!started)started=now;stepPhysics(world);steps++;apply();
    const asleep=[...world.bodies.values()].every(b=>b.isSleeping);
    if(asleep||steps>=480||now-started>=8500){for(const body of world.bodies.values())Matter.Sleeping.set(body,true);save();return;}
    frame=requestAnimationFrame(tick);
  }
  function wake(){if(disposed||!visible||globalThis.document?.hidden)return;stop();started=0;steps=0;if([...world.bodies.values()].every(b=>b.isSleeping)){apply();return;}frame=requestAnimationFrame(tick);}
  if(!Matter||reduced){
    const capacity=trayCapacity(width,items,height,variant),positions={};
    elements.forEach((el,i)=>{const item=items[i],x=8+i%capacity.columns*capacity.cellWidth,y=height-item.height-10-Math.floor(i/capacity.columns)*capacity.rowHeight;el.style.left=x+"px";el.style.top=y+"px";el.style.transform="";positions[item.id]={x,y,angle:0};});
    options.onSettled?.(positions);
    return {width,destroy(){},pause(){},wake(){},resize(){},snapshot:()=>positions};
  }
  world=physicsWorld(items,width,height,Matter);apply();
  if(typeof IntersectionObserver!=="undefined"){observer=new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting!==false;if(visible)wake();else stop();},{threshold:0});observer.observe(container);}
  const visibility=()=>{if(document.hidden)stop();else wake();};document.addEventListener("visibilitychange",visibility);wake();
  return {
    width,pause:stop,wake,snapshot:()=>physicsPositions(world),
    resize(id,size){const b=world.bodies.get(id);if(!b)return;Matter.Body.scale(b,size.width/b.ccWidth,size.height/b.ccHeight);b.ccWidth=size.width;b.ccHeight=size.height;for(const body of world.bodies.values())Matter.Sleeping.set(body,false);wake();},
    destroy(){if(disposed)return;stop();disposed=true;observer?.disconnect();document.removeEventListener("visibilitychange",visibility);Matter.Composite.clear(world.engine.world,false);Matter.Engine.clear(world.engine);}
  };
}
