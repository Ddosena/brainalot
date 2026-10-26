import { WORK_ENABLED } from "./work-availability.js";
import { applyDocument, syncLocale } from "./i18n.js";

document.documentElement.dataset.workEnabled = String(WORK_ENABLED);
const embeddedWork=new URLSearchParams(location.search).get("embedded-work")==="1";
if(!embeddedWork){location.replace(new URL("work.html?mode="+(new URLSearchParams(location.search).get("mode")==="history"?"history":"day"),location.href).href);}
else if (WORK_ENABLED) {
  await import("./work-app.js?v=constructor-20261023-a");
} else if (!await syncLocale()) {
  applyDocument();
}
