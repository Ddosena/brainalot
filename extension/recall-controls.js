import { sharedApi } from "./time-client.js";
import { t } from "./i18n.js";
export function mountRecallControls(host, { currentId, refresh, notes }) {
  const root = document.createElement("div"); root.className = "recall-controls";
  const pause = document.createElement("button"); pause.type = "button";
  const filters = document.createElement("details"); filters.className = "recall-filters";
  const heading = document.createElement("summary"); heading.textContent = t("Что вспоминать"); filters.append(heading);
  const form = document.createElement("form"); filters.append(form);
  const info = document.createElement("p"); info.className = "recall-empty"; info.setAttribute("role", "status");
  root.append(pause, filters, info); host.append(root);
  let prefs = null;
  async function save(patch, reset = false) {
    if (!prefs) prefs = await sharedApi("/api/recall");
    try { prefs = await sharedApi("/api/recall", "POST", { request_id: crypto.randomUUID(), expected_revision: prefs.revision, patch, reset }); await refresh(); }
    catch (error) { info.textContent = error.message; if (error.status === 409) prefs = await sharedApi("/api/recall"); }
  }
  pause.onclick = () => save({ paused: !prefs?.paused, selected_id: currentId() });
  function fieldset(title, values, chosen, name) {
    const group = document.createElement("fieldset"); const legend = document.createElement("legend"); legend.textContent = title; group.append(legend);
    for (const [id, text] of values) {
      const label = document.createElement("label"), input = document.createElement("input"); input.type = "checkbox"; input.name = name; input.value = JSON.stringify(id); input.checked = chosen === null || chosen.includes(id);
      label.append(input, document.createTextNode(text)); group.append(label);
    } return group;
  }
  function buildForm() {
    if (!prefs) return;
    form.replaceChildren(fieldset(t("Разделы библиотеки"), [["task",t("Дела")],["purchase",t("Покупки")],["thought",t("Мысли")],["idea",t("Идеи")],["link",t("Ссылки")],["media",t("Медиа")],["person",t("Люди")]], prefs.kinds, "kind"));
    form.append(fieldset(t("Проекты и люди"), [[null,t("Без проекта")], ...notes().filter(n => ["project","person"].includes(n.kind)).map(n => [n.id,n.title])], prefs.context_ids, "context"));
    const apply = document.createElement("button"); apply.type = "submit"; apply.textContent = t("Применить");
    const reset = document.createElement("button"); reset.type = "button"; reset.textContent = t("Стандартные настройки"); reset.onclick = () => save({}, true).then(buildForm);
    form.append(apply, reset);
  }
  filters.addEventListener("toggle", () => { if (filters.open) buildForm(); });
  form.onsubmit = event => {
    event.preventDefault();
    const selected = name => [...form.querySelectorAll(`input[name=${name}]:checked`)].map(input => JSON.parse(input.value));
    void save({ kinds: selected("kind"), context_ids: selected("context") });
  };
  return {
    sync(value) {
      if (!value) return; prefs = value; pause.textContent = prefs.paused ? t("Возобновить листание") : t("Остановить листание"); pause.setAttribute("aria-pressed", String(prefs.paused));
      info.textContent = prefs.selection_missing ? t("Выбранная запись удалена или исключена. Пауза сохранена; выберите другую стрелками.") : prefs.candidate_count === 0 ? t("Нет подходящих записей. Проверьте разделы, проекты и индивидуальные исключения.") : t("Доступно записей: {count}", { count: prefs.candidate_count });
    },
    select(id) { if (prefs?.paused) { prefs.selected_id = id; return save({ selected_id: id }); } return Promise.resolve(); },
  };
}
