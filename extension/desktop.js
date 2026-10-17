import { native } from "./desktop-platform.js";
await import("./background.js");
const { applyDocument, syncLocale, localeChange } = await import("./i18n.js");
if (!await syncLocale()) applyDocument();
const saved = await chrome.storage.local.get(["panel:theme", "panel:design"]);
document.documentElement.dataset.theme = saved["panel:theme"] || "light";
document.documentElement.dataset.design = saved["panel:design"] || "stream";
function show(view) {
  if (view === "home") view = "day";
  const paths = { day: "panel.html", library: "panel.html?library-window=1", work: "work.html", capture: "capture.html" };
  document.getElementById("desktop-content").src = paths[view] || paths.day;
  document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
}
show(new URLSearchParams(location.search).get("view") || "day");
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => show(button.dataset.view));
document.querySelectorAll("[data-mode]").forEach(button => button.onclick = () => native.window_action(button.dataset.mode));
chrome.storage.onChanged.addListener(changes => {
  if (localeChange(changes)) void syncLocale();
  if (changes["panel:theme"]) document.documentElement.dataset.theme = changes["panel:theme"].newValue;
  if (changes["panel:design"]) document.documentElement.dataset.design = changes["panel:design"].newValue;
});
