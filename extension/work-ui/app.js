import {createWorkRuntime} from './runtime.js';
import {adaptModel,currentMonday} from './model-adapter.js';
import {createWorkTransport} from './transport.js';
import {createCommands} from './commands.js';
import {createThemeStore} from './theme.js';
import {installWorkViewLifecycle} from '../work-view-lifecycle.js';
import {initI18n,syncLocale,applyDocument,localeChange} from '../i18n.js';
import {WORK_ENABLED,WORK_PAUSED_MESSAGE} from '../work-availability.js';

export function createWorkApp({connection,screen,date,document:doc=globalThis.document,window:win=globalThis.window}={}) {
  const query=new URLSearchParams(win.location.search),baseDate=currentMonday();
  const initialScreen=screen||(query.get('mode')==='day'||win.location.pathname.endsWith('work-day.html')||win.location.hash==='#day'?'day':'week');
  let runtime,announced=false;
  const lifecycle=installWorkViewLifecycle({windowObject:win,origin:win.location.origin,prepareLeave:async()=>{
    const active=doc.activeElement;if(active?.dataset.field&&active.dataset.workDirty==='1')active.dispatchEvent(new Event('change',{bubbles:true}));
    await runtime.flush();return {date:runtime.iso(runtime.U.day)};
  }});
  const transport=createWorkTransport(connection,{document:doc,window:win,path:initialScreen==='day'?'/api/work/period/day':'/api/work/period/model',
isPaused:()=>runtime?.isPaused()||false,onPoll:()=>Promise.all([theme.refresh(),runtime?.timerPoll?.()]),onModel:model=>{
    runtime.acceptModel(adaptModel(model,{baseDate}));if(!announced){announced=true;lifecycle.ready();}
  },onError:error=>{runtime?.reportError(error);if(!announced)lifecycle.ready(error);}});
  const commands=createCommands(transport);
  const theme=createThemeStore(connection,{onChange:value=>runtime?.acceptTheme(value)});
  runtime=createWorkRuntime({commands,screen:initialScreen,date:date||query.get('date'),baseDate,document:doc,window:win,
    onIdle:()=>void transport.resume().catch(error=>runtime.reportError(error)),
    onNavigate:(mode,selectedDate)=>{
      if(query.get('embedded-work')==='1'&&query.get('work-instance')){win.parent.postMessage({type:'brainalot-work-navigate',mode,date:selectedDate,
instance:query.get('work-instance')},win.location.origin);return false;}
      else win.history.replaceState(null,'','#'+mode);
    },onTheme:value=>void theme.set(value).catch(error=>runtime.reportError(error)),
  });
  transport.start();
  const destroy=()=>{transport.stop();lifecycle.dispose();runtime.destroy();};
  win.addEventListener('pagehide',destroy,{once:true});
  return {runtime,transport,commands,theme,destroy,prepareLeave:async()=>{await runtime.flush();return {date:runtime.iso(runtime.U.day)};}};
}

if(typeof document!=='undefined'&&document.querySelector('#stage')) {
  await import('../desktop-platform.js');
  await initI18n();
  if(!await syncLocale()) {
    applyDocument();
    if(WORK_ENABLED){
      const {createConnection}=await import('../connection.js');
      const app=createWorkApp({connection:createConnection(globalThis.chrome)});
      globalThis.BrainalotWork=app;
      globalThis.chrome?.storage?.onChanged?.addListener(changes=>{if(localeChange(changes))globalThis.location.reload();});
    }else{document.querySelector('#stage').textContent=WORK_PAUSED_MESSAGE;}
  }
}
