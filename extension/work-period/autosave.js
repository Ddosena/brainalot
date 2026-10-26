// One serialized write queue for ordinary editor fields. A failed version is
// retained until the user edits it again; leaving a mode never blindly retries it.
export function createAutosaveQueue({save,onError=()=>{},delay=350,setTimer=setTimeout,clearTimer=clearTimeout}){
  const records=new Map();let tail=Promise.resolve(),disposed=false,version=0;
  function enqueue(key){
    const operation=tail.catch(()=>{}).then(async()=>{
      const row=records.get(key);if(!row||row.error||disposed)return;
      if(row.timer){clearTimer(row.timer);row.timer=null;}
      const sent={...structuredClone(row.snapshot),expectedRevision:row.expectedRevision},sentVersion=row.version;
      row.saving=true;
      try{
        await save(sent);
        if(records.get(key)===row && row.version===sentVersion)records.delete(key);
      }catch(error){row.error=error instanceof Error?error:new Error(String(error));onError(row.error,sent);}
      finally{row.saving=false;}
    });
    tail=operation;return operation;
  }
  function schedule(snapshot,{immediate=false}={}){
    if(disposed)return;
    const key=snapshot.key,prior=records.get(key);
    if(prior?.timer)clearTimer(prior.timer);
    const row=prior||{};
    if(!prior||prior.error)row.expectedRevision=snapshot.expectedRevision;
    row.snapshot=structuredClone(snapshot);row.version=++version;row.error=null;
    records.set(key,row);
    if(immediate){row.timer=null;return enqueue(key);}
    row.timer=setTimer(()=>{row.timer=null;void enqueue(key);},delay);
  }
  async function flush(){
    if(disposed)throw new Error('Editor is closed');
    for(const row of records.values())if(row.timer){clearTimer(row.timer);row.timer=null;}
    await tail;
    while(records.size){
      if(disposed)throw new Error('Editor is closed');
      const failed=[...records.values()].find(row=>row.error);if(failed)throw failed.error;
      const keys=[...records.keys()];for(const key of keys)await enqueue(key);
    }
  }
  return {schedule,flush,get pending(){return records.size;},get failed(){return [...records.values()].some(row=>!!row.error);},dispose(){disposed=true;for(const row of records.values())if(row.timer)clearTimer(row.timer);}};
}
