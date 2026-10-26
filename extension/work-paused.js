import { WORK_ENABLED } from "./work-availability.js";
import { applyDocument, syncLocale } from "./i18n.js";

document.documentElement.dataset.workEnabled = String(WORK_ENABLED);
if (WORK_ENABLED) {
  await import("./work-period/bootstrap.js");
} else if (!await syncLocale()) {
  applyDocument();
}
