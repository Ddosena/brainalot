// Project a pointer on the visible circular dial to its 12:00–24:00 scale.
// Rect coordinates are used only for a transient drag target, never persisted.
export function orbitDropMinute(rect,x,y,{innerRadius=80,outerRadius=150}={}){
  if(!rect||![rect.left,rect.top,rect.width,rect.height,x,y].every(Number.isFinite)||rect.width<=0||rect.height<=0)return null;
  const scale=Math.min(rect.width,rect.height)/360;
  const localX=(x-rect.left-(rect.width-360*scale)/2)/scale-180;
  const localY=(y-rect.top-(rect.height-360*scale)/2)/scale-180;
  const radius=Math.hypot(localX,localY);
  if(radius<innerRadius||radius>outerRadius)return null;
  const angle=(Math.atan2(localX,-localY)+2*Math.PI)%(2*Math.PI);
  return 720+angle/(2*Math.PI)*720;
}
