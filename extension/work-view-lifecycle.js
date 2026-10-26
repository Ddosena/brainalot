export function installWorkViewLifecycle({prepareLeave,windowObject=window,origin=location.origin}){
  const query=new URLSearchParams(windowObject.location.search),instance=query.get('work-instance');
  if(query.get('embedded-work')!=='1' || !instance)return {ready(){},dispose(){}};
  const receipts=new Map();
  const send=data=>windowObject.parent.postMessage({...data,instance},origin);
  async function handle(event){
    const data=event.data;
    if(event.source!==windowObject.parent || event.origin!==origin || data?.type!=='brainalot-work-prepare-leave' || data.instance!==instance || typeof data.request_id!=='string')return;
    if(!receipts.has(data.request_id))receipts.set(data.request_id,Promise.resolve().then(prepareLeave).then(value=>({ok:true,...value}),error=>({ok:false,error:error.message||String(error)})));
    const result=await receipts.get(data.request_id);
    send({type:'brainalot-work-prepare-leave-result',request_id:data.request_id,...result});
  }
  windowObject.addEventListener('message',handle);
  return {ready(error){send({type:'brainalot-work-ready',ok:!error,...(error?{error:error.message||String(error)}:{})});},dispose(){windowObject.removeEventListener('message',handle);}};
}
