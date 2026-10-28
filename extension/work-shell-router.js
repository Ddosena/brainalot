import {t} from './i18n.js';

export function validWorkDate(value){
  return typeof value==='string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value+'T12:00:00Z')) && new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
}
const monday=value=>{const date=new Date(value+'T12:00:00Z'),day=date.getUTCDay();date.setUTCDate(date.getUTCDate()-((day||7)-1));return date.toISOString().slice(0,10);};
export const workMode=value=>['day','week','history'].includes(value)?value:'day';

export function createWorkShellRouter({frame,api,parentWindow,origin,baseUrl=origin+'/desktop/work.html',onState=()=>{},initialMode='day',initialDate,requestId=()=>crypto.randomUUID(),setTimer=setTimeout,clearTimer=clearTimeout}){
  let current={mode:workMode(initialMode),date:validWorkDate(initialDate)?initialDate:null,instance:null,ready:false,busy:false,error:''};
  let pending=null,readyTimer=null,disposed=false;
  const publish=patch=>{current={...current,...patch};onState({...current});};
  function mount(mode,date){
    if(readyTimer)clearTimer(readyTimer);
    const instance=requestId(),url=new URL(mode==='week'?'work-period.html':'work-day.html',baseUrl);
    url.search=new URLSearchParams({'embedded-work':'1',mode,'work-instance':instance,...(validWorkDate(date)?{date}:{})}).toString();
    publish({mode,date,instance,ready:false,busy:false,error:''});
    frame.src=url.href;
    readyTimer=setTimer(()=>{readyTimer=null;if(!disposed&&!current.ready)publish({error:t('Не удалось открыть Work. Обновите окно и повторите попытку.')});},15000);
  }
  function handleMessage(event){
    if(disposed || event.source!==frame.contentWindow || event.origin!==origin)return false;
    const data=event.data;
    if(data?.instance!==current.instance)return false;
    if(data.type==='brainalot-work-ready'){
      if(readyTimer)clearTimer(readyTimer);readyTimer=null;
      publish({ready:data.ok!==false,error:data.ok===false?String(data.error||t('Не удалось открыть Work. Обновите окно и повторите попытку.')):''});return true;
    }
    if(data.type!=='brainalot-work-prepare-leave-result' || !pending || data.request_id!==pending.id || pending.instance!==current.instance)return false;
    const operation=pending;pending=null;clearTimer(operation.timer);operation.resolve(data);return true;
  }
  async function start(){
    publish({busy:true,error:''});
    try{const saved=await api('/api/work/period/context');if(disposed)return;mount(current.mode,current.date||(saved.planning_initialized!==false&&validWorkDate(saved.context?.date)?saved.context.date:null));return true;}
    catch(error){publish({busy:false,error:error.message});return false;}
  }
  async function navigate(value,requestedDate){
    const mode=workMode(value);
    if(disposed || current.busy || !current.ready || mode===current.mode)return false;
    publish({busy:true,error:''});
    try{
      const answer=await new Promise((resolve,reject)=>{
        const id=requestId(),instance=current.instance;
        const timer=setTimer(()=>{if(pending?.id===id)pending=null;reject(new Error(t('Work ещё сохраняет изменения. Попробуйте перейти ещё раз.')));},15000);
        pending={id,instance,timer,resolve};
        frame.contentWindow.postMessage({type:'brainalot-work-prepare-leave',request_id:id,instance},origin);
      });
      if(disposed)return false;
      if(answer.ok!==true)throw new Error(answer.error||t('Сохраните изменения перед переходом.'));
      const date=validWorkDate(requestedDate)?requestedDate:validWorkDate(answer.date)?answer.date:current.date;
      const saved=await api('/api/work/period/context');
      const patch={...(validWorkDate(date)?{date,week_start:monday(date),window_start:monday(date)}:{}),mode:'week'};
      // An empty Work has no plan to remember a view in; its start screen needs none.
      if(saved.planning_initialized!==false)await api('/api/work/period/context','POST',{request_id:requestId(),expected_revision:saved.revision,patch});
      if(disposed)return false;
      mount(mode,date);return true;
    }catch(error){if(!disposed)publish({busy:false,error:error.message||String(error)});return false;}
  }
  function retry(){if(disposed||current.ready||current.busy)return false;if(!current.instance)return start();mount(current.mode,current.date);return true;}
  return {start,navigate,retry,handleMessage,get state(){return {...current};},destroy(){disposed=true;if(readyTimer)clearTimer(readyTimer);if(pending){clearTimer(pending.timer);pending.resolve({ok:false,error:t('Переход отменён.')});pending=null;}}};
}
