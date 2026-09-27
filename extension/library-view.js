import { matchesLibrary } from "./capture-window.js";
import { t } from "./i18n.js";

export const LIBRARY_CATEGORIES = [
  {id:"all", label:t("Все")},
  {id:"thoughts", label:t("Мысли")},
  {id:"tasks", label:t("Дела")},
  {id:"projects", label:t("Проекты")},
  {id:"materials", label:t("Материалы")},
  {id:"people", label:t("Люди")},
  {id:"inbox", label:t("Входящие")},
  {id:"archive", label:t("Архив")},
];

export const DOSSIER_SECTIONS = [
  {id:"all", label:t("Обзор")},
  {id:"thoughts", label:t("Мысли и идеи")},
  {id:"tasks", label:t("Дела")},
  {id:"materials", label:t("Материалы")},
  {id:"attachments", label:t("Вложения")},
];

const kinds = {
  thoughts:new Set(["thought", "idea"]),
  tasks:new Set(["task", "purchase"]),
  materials:new Set(["media", "link"]),
};

export function inLibraryCategory(note, category) {
  if (category === "all") return true;
  if (category === "archive") return note.status === "archived";
  if (category === "projects") return note.kind === "project";
  if (note.status === "archived") return false;
  if (category === "inbox") return note.kind === "inbox" || note.status === "inbox";
  if (category === "people") return note.kind === "person";
  return Boolean(kinds[category]?.has(note.kind));
}

export function libraryCategoryCounts(notes) {
  return Object.fromEntries(LIBRARY_CATEGORIES.map(category => [
    category.id, notes.filter(note => inLibraryCategory(note, category.id)).length,
  ]));
}

export function linkedProjectNotes(notes, projectId) {
  return notes.filter(note => note.id !== projectId && note.context_id === projectId);
}

export function dossierNotes(notes, projectId, section = "all") {
  const linked = linkedProjectNotes(notes, projectId);
  if (section === "all") return linked;
  if (section === "attachments") {
    const project = notes.find(note => note.id === projectId && note.kind === "project");
    return [project, ...linked].filter(note => note && Array.isArray(note.attachments) && note.attachments.length > 0);
  }
  return linked.filter(note => kinds[section]?.has(note.kind));
}

export function dossierCounts(notes, projectId) {
  return Object.fromEntries(DOSSIER_SECTIONS.map(section => [
    section.id, dossierNotes(notes, projectId, section.id).length,
  ]));
}

/** Fall back to the project index when a dossier's project disappears. */
export function resolveLibraryRoute(route, notes) {
  const category = LIBRARY_CATEGORIES.some(item => item.id === route?.category) ? route.category : "all";
  const project = route?.projectId ? notes.find(note => note.id === route.projectId && note.kind === "project") : null;
  if (!project) return {category:route?.projectId ? "projects" : category, projectId:null, section:"all"};
  const section = DOSSIER_SECTIONS.some(item => item.id === route.section) ? route.section : "all";
  return {category:"projects", projectId:project.id, section};
}

export function selectLibraryNotes(notes, route, query = "", contexts = []) {
  const source = Array.isArray(notes) ? notes : [];
  if (String(query).trim()) return source.filter(note => matchesLibrary(note, query, contexts));
  if (route?.projectId) return dossierNotes(source, route.projectId, route.section);
  return source.filter(note => inLibraryCategory(note, route?.category || "all"));
}

/** Group by exact context ID; same-titled projects are never merged. */
export function groupLibraryNotes(notes, contexts) {
  const byId = new Map((Array.isArray(contexts) ? contexts : []).map(note => [note.id, note]));
  const groups = new Map();
  for (const note of notes) {
    const context = byId.get(note.context_id);
    const key = context?.id || "";
    if (!groups.has(key)) groups.set(key, {id:key, title:context?.title || t("Без связи"), notes:[]});
    groups.get(key).notes.push(note);
  }
  return [...groups.values()];
}
