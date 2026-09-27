import test from "node:test";
import assert from "node:assert/strict";
import { dossierCounts, dossierNotes, groupLibraryNotes, libraryCategoryCounts, resolveLibraryRoute, selectLibraryNotes } from "../library-view.js";

const notes = [
  {id:"project-a", kind:"project", title:"Одинаковое имя", status:"active", attachments:[{id:"project-file"}], body:""},
  {id:"project-b", kind:"project", title:"Одинаковое имя", status:"archived", body:""},
  {id:"idea-a", kind:"idea", title:"Идея", status:"active", context_id:"project-a", body:"Курсор"},
  {id:"task-a", kind:"task", title:"Готовое дело", status:"done", context_id:"project-a", body:""},
  {id:"material-a", kind:"media", title:"Ссылка и картинка", status:"archived", context_id:"project-a", attachments:[{id:"file"}], body:""},
  {id:"odd-a", kind:"person", title:"Редкая связь", status:"active", context_id:"project-a", body:""},
  {id:"idea-b", kind:"idea", title:"Чужая идея", status:"active", context_id:"project-b", body:""},
  {id:"inbox", kind:"inbox", title:"Входящее", status:"inbox", body:""},
];

test("Explorer categories include every record while archive remains directly reachable", () => {
  const counts = libraryCategoryCounts(notes);
  assert.equal(counts.all, notes.length);
  assert.equal(counts.projects, 2);
  assert.equal(counts.thoughts, 2);
  assert.equal(counts.archive, 2);
  assert.equal(counts.inbox, 1);
  assert.deepEqual(selectLibraryNotes(notes, {category:"archive"}).map(note => note.id), ["project-b", "material-a"]);
});

test("project dossier uses exact ID, retains completed and archived records, and exposes uncommon kinds", () => {
  assert.deepEqual(dossierNotes(notes, "project-a").map(note => note.id), ["idea-a", "task-a", "material-a", "odd-a"]);
  assert.deepEqual(dossierNotes(notes, "project-b").map(note => note.id), ["idea-b"]);
  assert.deepEqual(dossierNotes(notes, "project-a", "tasks").map(note => note.id), ["task-a"]);
  assert.deepEqual(dossierNotes(notes, "project-a", "materials").map(note => note.id), ["material-a"]);
  assert.deepEqual(dossierNotes(notes, "project-a", "attachments").map(note => note.id), ["project-a", "material-a"]);
  assert.equal(dossierCounts(notes, "project-a").all, 4);
  assert.equal(dossierCounts(notes, "project-a").attachments, 2);
});

test("search spans categories and project context names, then route recovers from deletion", () => {
  const contexts = notes.filter(note => note.kind === "project");
  assert.deepEqual(selectLibraryNotes(notes, {category:"tasks"}, "чужая", contexts).map(note => note.id), ["idea-b"]);
  assert.equal(selectLibraryNotes(notes, {category:"all"}, "Одинаковое имя", contexts).length, 7);
  assert.deepEqual(resolveLibraryRoute({category:"projects", projectId:"project-a", section:"attachments"}, notes), {category:"projects", projectId:"project-a", section:"attachments"});
  assert.deepEqual(resolveLibraryRoute({category:"projects", projectId:"project-a", section:"attachments"}, notes.filter(note => note.id !== "project-a")), {category:"projects", projectId:null, section:"all"});
  const groups = groupLibraryNotes([notes[2], notes[6]], contexts);
  assert.deepEqual(groups.map(group => [group.id, group.title, group.notes.length]), [["project-a", "Одинаковое имя", 1], ["project-b", "Одинаковое имя", 1]]);
});
