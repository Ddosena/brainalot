// The Apps Script the owner pastes into script.google.com: a read-only bridge
// that returns Calendar events with their colours to the local Brainalot.
// The panel fills in the local key; tests run this same text with fakes of
// Google's services. Instructions: docs/calendar-bridge.md.

export const KEY_PLACEHOLDER = "__BRAINALOT_KEY__";

const SCRIPT = String.raw`// Brainalot: мост Google Календаря, только чтение.
// Brainalot присылает сюда ключ и получает события вместе с цветами.
// Скрипт ничего не меняет в календаре. Не публикуйте этот текст: в нём ключ.
var BRAINALOT_KEY = '__BRAINALOT_KEY__';
var PROTOCOL = 1;
var API = 'https://www.googleapis.com/calendar/v3/calendars/';
var MAX_PAGES = 20;
var labelVersion_ = true;

function doPost(e) {
  var request;
  try {
    request = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (error) {
    return reply_({ok: false, error: 'request'});
  }
  if (!sameKey_(String(request.key || ''))) return reply_({ok: false, error: 'key'});
  if (request.action === 'ping') return reply_({ok: true, protocol: PROTOCOL});
  if (request.action !== 'events' || typeof request.calendar !== 'string' || !request.calendar ||
      !isTime_(request.from) || !isTime_(request.to)) {
    return reply_({ok: false, error: 'request'});
  }
  try {
    return reply_(readEvents_(request.calendar, request.from, request.to));
  } catch (error) {
    return reply_({ok: false, error: error.code || 'api', message: String(error.message || error).slice(0, 300)});
  }
}

// Ключ никогда не передаётся в адресе, поэтому GET ничего не отдаёт.
function doGet() {
  return reply_({ok: false, error: 'method'});
}

function reply_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function sameKey_(key) {
  if (key.length !== BRAINALOT_KEY.length) return false;
  var difference = 0;
  for (var index = 0; index < key.length; index++) {
    difference |= key.charCodeAt(index) ^ BRAINALOT_KEY.charCodeAt(index);
  }
  return difference === 0;
}

function isTime_(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/.test(value);
}

function query_(values) {
  return Object.keys(values).filter(function (key) {
    return values[key] !== undefined && values[key] !== null;
  }).map(function (key) {
    return encodeURIComponent(key) + '=' + encodeURIComponent(values[key]);
  }).join('&');
}

// Метки цветов Google отдаёт только с eventLabelVersion=1. Если API его не
// принимает, мост читает классические цвета без меток.
function get_(url, values) {
  while (true) {
    var withLabels = labelVersion_;
    var response = UrlFetchApp.fetch(url + '?' + query_(withLabels ? Object.assign({eventLabelVersion: '1'}, values) : values), {
      headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()}, muteHttpExceptions: true});
    var status = response.getResponseCode();
    if (status === 200) return JSON.parse(response.getContentText());
    if (status === 400 && withLabels) {
      labelVersion_ = false;
      continue;
    }
    var message = 'HTTP ' + status;
    try {
      message = JSON.parse(response.getContentText()).error.message || message;
    } catch (ignored) {}
    var error = new Error(message);
    error.code = status === 404 ? 'calendar' : 'api';
    throw error;
  }
}

function readEvents_(calendarId, from, to) {
  var base = API + encodeURIComponent(calendarId);
  var labels = {};
  var calendar = get_(base, {});
  ((calendar.labelProperties || {}).eventLabels || []).forEach(function (label) {
    if (label && label.id) labels[label.id] = label;
  });
  var events = [];
  var pageToken = null;
  var pages = 0;
  do {
    var page = get_(base + '/events', {singleEvents: 'true', showDeleted: 'false', orderBy: 'startTime',
      timeMin: from, timeMax: to, maxResults: '2500', pageToken: pageToken});
    (page.items || []).forEach(function (item) {
      var label = item.eventLabelId ? labels[item.eventLabelId] : null;
      events.push({id: item.id, iCalUID: item.iCalUID, recurringEventId: item.recurringEventId,
        originalStartTime: item.originalStartTime, start: item.start, end: item.end,
        summary: item.summary, status: item.status, eventType: item.eventType,
        colorId: item.colorId, eventLabelId: item.eventLabelId,
        labelColor: label ? label.backgroundColor : undefined, labelName: label ? label.name : undefined});
    });
    pageToken = page.nextPageToken || null;
    pages++;
  } while (pageToken && pages < MAX_PAGES);
  return {ok: true, protocol: PROTOCOL, labels: labelVersion_, events: events, truncated: Boolean(pageToken)};
}
`;

// Read-only Calendar access plus the right to call the Calendar API from the
// script. Enabling the advanced service turns the API on in the script's
// Google-managed project; the script itself calls it with UrlFetchApp.
export const BRIDGE_MANIFEST = JSON.stringify({
  timeZone: "Etc/UTC",
  runtimeVersion: "V8",
  dependencies: {enabledAdvancedServices: [{userSymbol: "Calendar", serviceId: "calendar", version: "v3"}]},
  oauthScopes: ["https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/script.external_request"],
  webapp: {executeAs: "USER_DEPLOYING", access: "ANYONE_ANONYMOUS"},
}, null, 2);

export function bridgeScript(key) {
  if (!/^[A-Za-z0-9_-]{20,100}$/.test(String(key || ""))) throw new Error("bridge key");
  return SCRIPT.replace(KEY_PLACEHOLDER, key);
}
