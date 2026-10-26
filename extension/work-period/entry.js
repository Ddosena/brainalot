import {WORK_ENABLED} from '../work-availability.js';
import {applyDocument,syncLocale} from '../i18n.js';
document.documentElement.dataset.workEnabled=String(WORK_ENABLED);
const query=new URLSearchParams(location.search);
if(query.get('embedded-work')!=='1')location.replace(new URL('work.html?mode=week',location.href).href);
else if(WORK_ENABLED)await import('./bootstrap.js');
else if(!await syncLocale())applyDocument();
