import './desktop-platform.js';
import {createConnection} from './connection.js';
import {initI18n,syncLocale,applyDocument} from './i18n.js';
import {createWorkShellRouter} from './work-shell-router.js';
await initI18n();
if(!await syncLocale()){
  applyDocument();
  const frame=document.getElementById('work-view'),connection=createConnection(globalThis.chrome),query=new URLSearchParams(location.search);
  const router=createWorkShellRouter({frame,api:(...args)=>connection.api(...args),origin:location.origin,baseUrl:location.href,initialMode:query.get('mode')||'day',initialDate:query.get('date'),onState(state){
    frame.inert=state.busy||!state.ready;
    document.getElementById('work-shell-status').textContent=state.error;
    document.getElementById('work-shell-retry').hidden=state.ready||state.busy||!state.error;
  }});
  window.addEventListener('message',event=>{
    router.handleMessage(event);
    const data=event.data;
    if(event.source!==frame.contentWindow||event.origin!==location.origin||data?.instance!==router.state.instance)return;
    if(data.type==='brainalot-work-navigate'&&['day','week'].includes(data.mode))void router.navigate(data.mode,data.date);
    if(data.type==='brainalot-work-fullscreen-request'&&window.parent!==window)window.parent.postMessage(data,location.origin);
  });
  document.getElementById('work-shell-retry').addEventListener('click',()=>void router.retry());
  window.addEventListener('pagehide',()=>router.destroy(),{once:true});
  await router.start();
}
