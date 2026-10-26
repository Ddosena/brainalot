const {native}=await import('./desktop-platform.js');
const [{createConnection},{initI18n,syncLocale,applyDocument,t},{createWorkShellRouter}]=await Promise.all([
  import('./connection.js'),import('./i18n.js'),import('./work-shell-router.js'),
]);
await initI18n();
if(!await syncLocale()){
  applyDocument();
  const frame=document.getElementById('work-view'),connection=createConnection(globalThis.chrome),query=new URLSearchParams(location.search);
  const router=createWorkShellRouter({frame,api:(...args)=>connection.api(...args),parentWindow:window.parent,origin:location.origin,baseUrl:location.href,initialMode:query.get('mode')||'day',initialDate:query.get('date'),onState(state){
    frame.inert=state.busy||!state.ready;document.getElementById('work-shell-fullscreen').disabled=state.busy;
    document.getElementById('work-shell-status').textContent=state.error;
    document.getElementById('work-shell-retry').hidden=state.ready||state.busy||!state.error;
    for(const button of document.querySelectorAll('[data-work-mode]')){button.disabled=state.busy||!state.ready;button.setAttribute('aria-pressed',String(button.dataset.workMode===state.mode || button.dataset.workMode==='day'&&state.mode==='history'));}
    document.getElementById('day-tools').hidden=state.mode==='week';
    document.title='Brainalot · Work';
  }});
  let expanded=false,fullscreenPending=null;
  try{expanded=!!(await native?.bootstrap?.())?.work_expanded;}catch{}
  const fullscreen=document.getElementById('work-shell-fullscreen');
  const updateFullscreen=()=>{const label=expanded?t('Свернуть Work'):t('Развернуть Work');fullscreen.setAttribute('aria-label',label);fullscreen.title=label;};
  async function requestFullscreen(){
    if(fullscreenPending)return;
    if(window===window.parent){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();expanded=!!document.fullscreenElement;updateFullscreen();}catch(error){document.getElementById('work-shell-status').textContent=error.message;}return;}
    const id=crypto.randomUUID();fullscreenPending={id,instance:router.state.instance};
    window.parent.postMessage({type:'brainalot-work-fullscreen-request',expanded:!expanded,request_id:id},location.origin);
  }
  window.addEventListener('message',event=>{
    router.handleMessage(event);
    const data=event.data;
    if(event.origin!==location.origin)return;
    if(event.source!==window.parent || data?.type!=='brainalot-work-fullscreen-result' || data.request_id!==fullscreenPending?.id)return;
    const pending=fullscreenPending;fullscreenPending=null;
    if(pending.instance!==router.state.instance)return;
    if(data.ok===false)document.getElementById('work-shell-status').textContent=data.error||t('Не удалось изменить размер Work.');
    else expanded=!!data.expanded;
    updateFullscreen();
  });
  for(const button of document.querySelectorAll('[data-work-mode]'))button.addEventListener('click',()=>{if(fullscreenPending)return;void router.navigate(button.dataset.workMode);});
  fullscreen.addEventListener('click',()=>void requestFullscreen());updateFullscreen();
  document.getElementById('work-shell-retry').addEventListener('click',()=>void router.retry());
  window.addEventListener('pagehide',()=>router.destroy(),{once:true});
  await router.start();
}
