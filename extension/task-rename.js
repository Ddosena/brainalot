import { t } from "./i18n.js";

/** contextmenu.detail is not a reliable click count, so track the two presses. */
export function createRightClickPair({now = () => performance.now(), interval = 500, distance = 12} = {}) {
  let previous = null;
  return event => {
    if (event.button !== 2 || event.ctrlKey || event.shiftKey || event.altKey || event.metaKey) {
      previous = null;
      return false;
    }
    const current = {time:now(), x:event.clientX, y:event.clientY};
    const paired = previous && current.time >= previous.time && current.time - previous.time <= interval
      && Math.hypot(current.x - previous.x, current.y - previous.y) <= distance;
    previous = paired ? null : current;
    return Boolean(paired);
  };
}

/** Experimental shortcut; the existing edit button remains the keyboard route. */
export function bindTaskRename(title, note, {save, onError, onStart = () => {}}) {
  const pair = createRightClickPair();
  let input = null;
  let saving = false;
  const restore = () => { if (!saving) { input = null; title.textContent = note.title; } };
  title.addEventListener("contextmenu", event => {
    if (input || event.target !== title || event.button !== 2) return;
    event.preventDefault();
    event.stopPropagation();
    if (!pair(event)) return;
    onStart();
    input = document.createElement("input");
    input.className = "task-rename-input";
    input.value = note.title;
    input.maxLength = 200;
    input.setAttribute("aria-label", t("Название задачи"));
    input.title = t("Enter — сохранить, Escape — отменить");
    for (const name of ["pointerdown", "click", "dblclick", "contextmenu"]) {
      input.addEventListener(name, event => event.stopPropagation());
    }
    input.addEventListener("blur", restore);
    input.addEventListener("keydown", async event => {
      event.stopPropagation();
      if (event.key === "Escape") { event.preventDefault(); restore(); return; }
      if (event.key !== "Enter" || event.isComposing || saving) return;
      event.preventDefault();
      const value = input.value.trim();
      if (!value) { input.setCustomValidity(t("Укажите название записи.")); input.reportValidity(); return; }
      if (value === note.title) { restore(); return; }
      input.setCustomValidity("");
      saving = true;
      input.disabled = true;
      try { await save(value); }
      catch (error) {
        saving = false;
        if (input?.isConnected) { input.disabled = false; input.focus(); }
        onError(error);
      }
    });
    title.replaceChildren(input);
    input.focus();
    input.select();
  });
}
