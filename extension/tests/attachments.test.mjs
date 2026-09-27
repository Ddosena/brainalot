import test from "node:test";
import assert from "node:assert/strict";
import { base64Blob, checkAttachmentLimit, isRasterBlob, loadRasterPreview, previewUrl, rasterKey, resolveAttachments, safeFileName, safeRaster, stripManagedAttachments } from "../attachments.js";
import { editableNoteText } from "../panel-state.js";
import { normalizeDraft } from "../capture-window.js";

test("worker-compatible base64 encoding handles chunk boundaries", async () => {
  const bytes = Uint8Array.from({ length: 25003 }, (_, index) => index % 251);
  const encoded = await base64Blob(new Blob([bytes]));
  assert.deepEqual(Buffer.from(encoded, "base64"), Buffer.from(bytes));
});

test("upload cache survives a failed capture and reuses its descriptor on retry", async () => {
  const files = new Map([["local", { localId:"local", name:"drawing.png", size:3, blob:new Blob(["cat"]) }]]);
  let uploads = 0;
  const options = {
    load: async id => files.get(id),
    save: async item => files.set(item.localId, item),
  };
  const upload = async body => {
    uploads++;
    assert.equal(Buffer.from(body.content_base64, "base64").toString(), "cat");
    return {id:"a".repeat(64) + ".png", name:body.name, mime:"image/png", size:3};
  };
  const refs = [{localId:"local", name:"drawing.png", size:3}];
  assert.deepEqual(await resolveAttachments(refs, upload, options), await resolveAttachments(refs, upload, options));
  assert.equal(uploads, 1);
});

test("limits and generated Markdown block protect text editing", () => {
  assert.throws(() => checkAttachmentLimit([], [{size:10 * 1024 * 1024 + 1}]), /10 МиБ/);
  assert.throws(() => checkAttachmentLimit(Array(10).fill({size:1}), [{size:1}]), /10 файлов/);
  assert.throws(() => checkAttachmentLimit([{size:25 * 1024 * 1024}], [{size:6 * 1024 * 1024}]), /30 МиБ/);
  const body = "\n# Title\n\nText\n\n<!-- mmm:attachments -->\n[file](attachments/x)\n<!-- /mmm:attachments -->\n";
  assert.equal(editableNoteText(body), "Text");
  assert.equal(stripManagedAttachments(body).includes("mmm:attachments"), false);
  assert.equal(safeRaster("image/svg+xml"), false);
  assert.equal(safeRaster("text/html"), false);
  assert.equal(safeRaster("image/webp"), true);
});

test("filenames are accepted before queueing only when the server can store them", () => {
  assert.equal(safeFileName("Изображение.png"), "Изображение.png");
  for (const name of ["../x.txt", "bad\\x.txt", "line\nfeed.txt", "a".repeat(201), ".."])
    assert.throws(() => checkAttachmentLimit([], [{name, size:5}]), /имя файла/);
});

test("thumbnail preview requires raster file signatures", async () => {
  assert.equal(await isRasterBlob(new Blob([Uint8Array.of(137,80,78,71,13,10,26,10,0)])), true);
  assert.equal(await isRasterBlob(new Blob(["<svg><script>alert(1)</script></svg>"], {type:"image/png"})), false);
});

test("verified raster preview coalesces concurrent loads and reuses bytes after rerender", async () => {
  const item = {id:"preview-cache-test.png",mime:"image/png"};
  let loads = 0;
  const load = async () => { loads++; return new Blob([Uint8Array.of(137,80,78,71,13,10,26,10,0)]); };
  const [first,second] = await Promise.all([loadRasterPreview(item,{load}),loadRasterPreview(item,{load})]);
  assert.equal(first,second);
  assert.equal(await loadRasterPreview(item,{load}),first);
  assert.equal(loads,1);
  assert.equal(await loadRasterPreview({id:"unsafe.svg",mime:"image/svg+xml"},{load}),null);
  assert.equal(loads,1);
  assert.equal(await loadRasterPreview({id:"bad.png",mime:"image/png"},{load:async()=>new Blob(["<svg>bad</svg>"])}),null);
});

test("cached previews share one object URL so re-renders and the viewer reuse pixels", async () => {
  const item = {id:"shared-url-test.png",mime:"image/png"};
  const load = async () => new Blob([Uint8Array.of(137,80,78,71,13,10,26,10,0)]);
  const first = await previewUrl(item, {load});
  const second = await previewUrl(item, {load});
  assert.equal(first.shared, true);
  assert.equal(first.url, second.url);
  assert.match(first.url, /^blob:/);
  assert.equal(rasterKey(item), "server:shared-url-test.png");
  assert.equal(rasterKey({localId:"x"}), "local:x");
  assert.equal(await previewUrl({id:"unsafe.svg",mime:"image/svg+xml"}, {load}), null);
});

test("capture draft retains only file references", () => {
  const draft = normalizeDraft({attachments:[{localId:"local",name:"drawing.png",size:3,type:"image/png",blob:"unexpected"}]});
  assert.equal(draft.attachments.length, 1);
  assert.equal(draft.attachments[0].blob, undefined);
});

test("attachment-only note has no editable duplicate heading", () => {
  const body = "\n# clipboard.png\n\n<!-- mmm:attachments -->\n![clipboard.png](8%20%D0%92%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F/x.png)\n<!-- /mmm:attachments -->\n";
  assert.equal(editableNoteText(body), "");
  assert.equal(stripManagedAttachments(body), "\n# clipboard.png");
});
