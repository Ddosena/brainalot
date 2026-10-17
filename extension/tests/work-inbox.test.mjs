import test from "node:test";
import assert from "node:assert/strict";
import { acceptance, applyAcceptance, snapshotOf, sourceNote } from "../work-inbox.js";
import { setLocale } from "../i18n.js";
import "../vk-parse.js";
import "../tg-parse.js";

setLocale("ru");
let counter = 0;
const makeId = () => `id${++counter}`;

function state() {
  return {
    projects: [{ id: "print", name: "3D-печать", about: "", color: "teal" }],
    items: [{ id: "i1", project: "print", kind: "task", title: "Преза", start: "2026-09-29", end: "2026-09-29", time: "", status: "doing",
      firstStep: "", resumeFrom: "", notes: "старое", checklist: [], groups: [{ id: "g", title: "ТЗ", color: "cyan", links: [{ id: "l", title: "ТЗ", url: "https://docs.example/tz" }] }] }],
    boards: [{ id: "general", view: { x: 0, y: 0, z: 1 }, items: [{ id: "n", type: "image", imageId: "blob" }] }],
    view: { board: "general", days: 7, calH: null },
  };
}

const source = { chat: "telegram:-1001", chat_title: "Модуль 2", message_id: "11", author: "Настя",
  date: "2026-09-29T12:05:00+03:00", link: "https://t.me/c/1/11", quote: "сдать до 4 октября" };

test("a deadline proposal becomes a calendar item with its source and links", () => {
  const proposal = { id: "p1", kind: "deadline", project_id: "print", title: "Сдать модуль 2", start: "2026-10-04", end: "2026-10-04",
    time: "", first_step: "", notes: "Три презентации", checklist: ["Преза 1"], links: [{ title: "ТЗ", url: "https://docs.example/tz2" }],
    files: [{ name: "Бриф.docx", path: "C:/data/chat-files/telegram/x/1-Бриф.docx" }], sources: [source] };
  const s = state();
  const result = acceptance(proposal, { project: "print", title: "Сдать модуль 2!", start: "2026-10-04", end: "" }, s, makeId);
  applyAcceptance(s, result, makeId);
  const item = s.items.at(-1);
  assert.equal(item.title, "Сдать модуль 2!");
  assert.equal(item.kind, "deadline");
  assert.equal(item.start, "2026-10-04");
  assert.equal(item.end, "2026-10-04");
  assert.equal(item.groups[0].title, "Из чатов");
  assert.equal(item.groups[0].links[0].url, "https://docs.example/tz2");
  assert.equal(item.checklist[0].text, "Преза 1");
  assert.match(item.notes, /Три презентации/);
  assert.match(item.notes, /Бриф\.docx — C:\/data/);
  assert.match(item.notes, /Из чата «Модуль 2», Настя, 29 сент 12:05: «сдать до 4 октября» https:\/\/t\.me\/c\/1\/11/);
  assert.equal(result.itemId, item.id);
});

test("a task keeps a range, and an agent's new project is created with its own board", () => {
  const s = state();
  const proposal = { id: "p2", kind: "task", project_id: null, project_name: "Скринкаст", title: "Сценарий", links: [], files: [], sources: [source] };
  assert.throws(() => acceptance(proposal, { project: "", start: "", end: "" }, s, makeId), /Выберите проект/);
  const result = acceptance(proposal, { project: "__new", title: "", start: "2026-09-30", end: "2026-10-02" }, s, makeId);
  applyAcceptance(s, result, makeId);
  const item = s.items.at(-1);
  assert.equal(s.projects.at(-1).name, "Скринкаст");
  assert.equal(s.projects.at(-1).color, "violet");
  assert.equal(s.boards.at(-1).project, s.projects.at(-1).id);
  assert.equal(item.project, s.projects.at(-1).id);
  assert.deepEqual([item.start, item.end, item.groups.length], ["2026-09-30", "2026-10-02", 0]);
  assert.equal(item.title, "Сценарий");
});

test("attaching adds only new links to the task's chat group and appends the source", () => {
  const s = state();
  const proposal = { id: "p3", kind: "attach", target_item_id: "i1", links: [{ title: "Бриф", url: "https://docs.example/brief" }],
    files: [], notes: "", sources: [source] };
  applyAcceptance(s, acceptance(proposal, {}, s, makeId), makeId);
  applyAcceptance(s, acceptance(proposal, {}, s, makeId), makeId);
  const item = s.items[0];
  const group = item.groups.find(g => g.title === "Из чатов");
  assert.equal(group.links.length, 1);
  assert.match(item.notes, /^старое\n\nИз чата «Модуль 2»/);
  assert.equal(s.items.length, 1);
  assert.throws(() => acceptance({ ...proposal, target_item_id: "gone" }, {}, s, makeId), /удалена/);
});

test("the snapshot for agents has no board and shortens notes when it is large", () => {
  const s = state();
  s.items[0].notes = "x".repeat(5000);
  const snapshot = snapshotOf(s);
  assert.deepEqual(Object.keys(snapshot), ["projects", "items"]);
  assert.equal(snapshot.items[0].notes.length, 1000);
  assert.equal(snapshot.items[0].groups[0].links[0].url, "https://docs.example/tz");
  s.items = Array.from({ length: 400 }, (_, i) => ({ ...s.items[0], id: `i${i}` }));
  assert.equal(snapshotOf(s).items[0].notes, "");
  assert.equal(sourceNote({ sources: [] }), "");
});

test("the VK reader recognizes the conversation from the address and unwraps VK links", () => {
  const vk = globalThis.BrainalotVk;
  assert.equal(vk.peer({ pathname: "/im/convo/2000000007", search: "?entrypoint=list_all" }), "2000000007");
  assert.equal(vk.peer({ pathname: "/im", search: "?sel=c7" }), "2000000007");
  assert.equal(vk.peer({ pathname: "/im", search: "?sel=123" }), "123");
  assert.equal(vk.peer({ pathname: "/feed", search: "" }), null);
  assert.equal(vk.realUrl("/away.php?to=https%3A%2F%2Fdocs.example%2Fa&cc_key=", "https://vk.com/im"), "https://docs.example/a");
  assert.equal(vk.realUrl("javascript:alert(1)", "https://vk.com/im"), "");
  assert.equal(vk.realUrl("https://docs.example/b", "https://vk.com/im"), "https://docs.example/b");
  assert.equal(vk.source, "vk");
});

test("the Telegram Web reader takes the chat id from the address", () => {
  const tg = globalThis.BrainalotTg;
  assert.equal(tg.source, "tgweb");
  assert.equal(tg.peer({ hash: "#-5230437713" }), "-5230437713");
  assert.equal(tg.peer({ hash: "#777000" }), "777000");
  assert.equal(tg.peer({ hash: "#-100123?thread=5" }), "-100123");
  assert.equal(tg.peer({ hash: "#@durov" }), null);
  assert.equal(tg.peer({ hash: "" }), null);
});
