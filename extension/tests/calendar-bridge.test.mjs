import assert from "node:assert/strict";
import test from "node:test";

import { BRIDGE_MANIFEST, bridgeScript } from "../calendar-bridge/script.js";

const KEY = "k".repeat(43);

function load(routes) {
  const fetched = [];
  const ContentService = {MimeType: {JSON: "json"}, createTextOutput: text => ({text, setMimeType() { return this; }})};
  const UrlFetchApp = {fetch(url, options) {
    fetched.push({url, options});
    const [status, body] = routes(url);
    return {getResponseCode: () => status, getContentText: () => JSON.stringify(body)};
  }};
  const ScriptApp = {getOAuthToken: () => "token"};
  const doPost = new Function("ContentService", "UrlFetchApp", "ScriptApp", `${bridgeScript(KEY)}; return doPost;`)(
    ContentService, UrlFetchApp, ScriptApp);
  const post = body => JSON.parse(doPost({postData: {contents: JSON.stringify(body)}}).text);
  return {post, fetched};
}

const request = {action: "events", key: KEY, calendar: "owner@example.com",
  from: "2026-10-01T00:00:00Z", to: "2026-12-01T00:00:00Z"};

test("the bridge refuses a wrong key and answers a ping", () => {
  const {post, fetched} = load(() => [500, {}]);
  assert.deepEqual(post({action: "ping", key: "x"}), {ok: false, error: "key"});
  assert.deepEqual(post({action: "ping", key: KEY}), {ok: true, protocol: 1});
  assert.deepEqual(post({...request, from: "yesterday"}), {ok: false, error: "request"});
  assert.equal(fetched.length, 0);
});

test("events carry label colours and classic colorId through every page", () => {
  const {post, fetched} = load(url => {
    if (!url.includes("/events")) return [200, {labelProperties: {eventLabels: [{id: "m", backgroundColor: "#f09300", name: "Скульптинг"}]}}];
    return url.includes("pageToken=p2")
      ? [200, {items: [{iCalUID: "b", colorId: "5", description: "secret"}]}]
      : [200, {items: [{iCalUID: "a", eventLabelId: "m"}], nextPageToken: "p2"}];
  });
  const result = post(request);
  assert.equal(result.ok, true);
  assert.equal(result.labels, true);
  assert.deepEqual(result.events, [{iCalUID: "a", eventLabelId: "m", labelColor: "#f09300", labelName: "Скульптинг"},
    {iCalUID: "b", colorId: "5"}]);
  assert.ok(fetched.every(call => call.url.includes("eventLabelVersion=1") && call.options.headers.Authorization === "Bearer token"));
});

test("without label support the bridge retries once and keeps classic colours", () => {
  const {post, fetched} = load(url => url.includes("eventLabelVersion") ? [400, {error: {message: "bad"}}]
    : url.includes("/events") ? [200, {items: [{iCalUID: "a", colorId: "6"}]}] : [200, {}]);
  const result = post(request);
  assert.equal(result.labels, false);
  assert.deepEqual(result.events, [{iCalUID: "a", colorId: "6"}]);
  assert.equal(fetched.filter(call => call.url.includes("eventLabelVersion")).length, 1);
});

test("a missing calendar is reported, and the manifest asks only to read", () => {
  const {post} = load(() => [404, {error: {message: "Not Found"}}]);
  assert.deepEqual(post(request), {ok: false, error: "calendar", message: "Not Found"});
  const manifest = JSON.parse(BRIDGE_MANIFEST);
  assert.ok(manifest.oauthScopes.includes("https://www.googleapis.com/auth/calendar.readonly"));
  assert.ok(!manifest.oauthScopes.some(scope => /auth\/calendar(\.events)?$/.test(scope)));
});
