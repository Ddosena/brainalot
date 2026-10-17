"""Explicit, isolated WebView2 diagnostic for the packaged Brainalot.exe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
import uuid

from .desktop import DesktopHost
from .runtime import Service, port_free


_ERROR_SCRIPT = r"""(() => {
  window.__brainalotQaErrors = [];
  const add = (kind, message) => window.__brainalotQaErrors.push({kind, message:String(message)});
  window.addEventListener('error', e => add('error', e.message || e.target?.src || 'resource error'), true);
  window.addEventListener('unhandledrejection', e => add('unhandledrejection', e.reason?.stack || e.reason || 'unknown'));
  const previous = console.error;
  console.error = (...args) => { add('console.error', args.map(String).join(' ')); previous.apply(console,args); };
})()"""


def _until(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:
            error = exc
        time.sleep(0.15)
    raise TimeoutError(f"WebView2 did not become ready: {error}")


def _js(window, expression):
    # Window.evaluate_js can wait on an unset pywebview event in the second
    # WebView2 window. ExecuteScriptAsync directly checks the actual renderer.
    from System import Func, Object
    task = window.native.Invoke(Func[Object](lambda: window.native.webview.CoreWebView2.
                                           ExecuteScriptAsync(expression)))
    _until(lambda: task.IsCompleted, 6)
    return json.loads(str(task.GetAwaiter().GetResult()))


def _screenshot(window, destination):
    from System import Func, Object
    from System.IO import MemoryStream
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    stream = MemoryStream()
    task = window.native.Invoke(Func[Object](lambda: window.native.webview.CoreWebView2.
        CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream)))
    _until(lambda: task.IsCompleted, 6)
    task.GetAwaiter().GetResult()
    destination.write_bytes(bytes(stream.ToArray()))


def _install_collector(window):
    from System import Func, Object
    task = window.native.Invoke(Func[Object](lambda: window.native.webview.CoreWebView2.
        AddScriptToExecuteOnDocumentCreatedAsync(_ERROR_SCRIPT)))
    _until(lambda: task.IsCompleted, 6)
    task.GetAwaiter().GetResult()


def _request(port, token, route, body=None):
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"http://127.0.0.1:{port}{route}",
                      data=None if body is None else json.dumps(body).encode(), headers=headers,
                      method="POST" if body is not None else "GET")
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def _marker(root, name, payload):
    target = root / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)


def _peer_work(window):
    _js(window, """(() => {
      const f = document.getElementById('desktop-content').contentWindow;
      f.__brainalotPeerWork = {pending:true};
      f.eval(`import('/desktop/work-store.js').then(async m => {
        await m.load();
        const before = m.get();
        const item = before.items.find(x => x.id === 'peer-task');
        const image = await m.imgGet('peer-image');
        if (!item || !image || !JSON.stringify(before).includes('Peer project'))
          throw new Error('Peer Work task, image or project is missing');
        const beforeTitle = item.title;
        m.update(state => { state.items.find(x => x.id === 'peer-task').title = 'Updated in desktop'; });
        await m.flush();
        window.__brainalotPeerWork = {pending:false, title:beforeTitle,
          imageSize:image.size, updated:m.get().items.find(x => x.id === 'peer-task').title};
      }).catch(error => { window.__brainalotPeerWork = {pending:false,error:String(error)}; });`);
      return true;
    })()""")
    result = _until(lambda: (s if (s := _js(window,
        "document.getElementById('desktop-content').contentWindow.__brainalotPeerWork"))
        and not s.get("pending") else None), 20)
    if result.get("error") or result.get("updated") != "Updated in desktop" or not result.get("imageSize"):
        raise RuntimeError(f"Peer Work inspection failed: {result}")
    return result


def _persistence_probe(window):
    payload = {"run": str(uuid.uuid4()), "text": "Brainalot WebView2 profile persistence"}
    script = """(() => {
      window.__brainalotPersistence = {pending:true};
      (async () => {
        const open = indexedDB.open('brainalot-smoke-persistence-v1', 1);
        open.onupgradeneeded = () => open.result.createObjectStore('checks');
        const db = await new Promise((resolve, reject) => {
          open.onsuccess = () => resolve(open.result);
          open.onerror = () => reject(open.error);
        });
        const read = key => new Promise((resolve, reject) => {
          const req = db.transaction('checks').objectStore('checks').get(key);
          req.onsuccess = () => resolve(req.result);
          req.onerror = () => reject(req.error);
        });
        const previous = await read('payload');
        const blob = await read('blob');
        const tx = db.transaction('checks', 'readwrite');
        tx.objectStore('checks').put(PAYLOAD, 'payload');
        tx.objectStore('checks').put(new Blob(['Brainalot persistent Blob'], {type:'text/plain'}), 'blob');
        await new Promise((resolve, reject) => {
          tx.oncomplete = resolve;
          tx.onerror = () => reject(tx.error);
          tx.onabort = () => reject(tx.error);
        });
        db.close();
        return {previous:previous ? {payload:previous, blobsize:blob?.size || 0} : null,
                current:PAYLOAD};
      })().then(result => {window.__brainalotPersistence = {pending:false, result};})
        .catch(error => {window.__brainalotPersistence = {pending:false,error:String(error)};});
      return true;
    })()""".replace("PAYLOAD", json.dumps(payload))
    _js(window, script)
    outcome = _until(lambda: (s if (s := _js(window, "window.__brainalotPersistence"))
                              and not s.get("pending") else None), 15)
    if outcome.get("error"):
        raise RuntimeError(f"IndexedDB persistence probe failed: {outcome['error']}")
    return outcome["result"]


def run(root: Path) -> int:
    """Only app.main may call this, after checking an explicit BRAINALOT_HOME."""
    root = Path(root)
    output = root / "state" / "desktop-smoke"
    output.mkdir(parents=True, exist_ok=True)
    report = {"root": str(root), "output": str(output), "views": {}, "widget": {},
              "screenshots": {}, "errors": []}
    service = None
    try:
        peer = os.environ.get("BRAINALOT_SMOKE_PEER") == "1"
        report["peer"] = peer
        if peer:
            for name in ("peer-ready.json", "peer-started.json", "peer-paused.json", "peer-verified.json"):
                (root / name).unlink(missing_ok=True)
        port = int(os.environ.get("BRAINALOT_SMOKE_PORT", "8794"))
        if port == 8765 or not 1024 <= port <= 65535 or not port_free(port):
            raise RuntimeError(f"Smoke port {port} is occupied or forbidden")
        report["port"] = port
        service = Service(root, port)
        service.start()
        token = (root / "state" / "api-token").read_text(encoding="utf-8").strip()
        if peer:
            _marker(root, "peer-ready.json", {"port": port})
            _until(lambda: (root / "peer-started.json").is_file(), 60)
            report["peer_started"] = True
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        host = DesktopHost(root, port, token, lambda: True)
        host.create_windows(webview)

        def inspect():
            try:
                host._restore_geometry()
                host.window_action("full")
                _until(lambda: host.full.events._pywebviewready.is_set(), 20)
                _until(lambda: _js(host.full, "document.readyState === 'complete'"), 20)
                _until(lambda: "panel.html" in (_js(host.full,
                    "document.getElementById('desktop-content')?.src || ''") or ""), 20)
                persistence = _persistence_probe(host.full)
                report["persistence_previous"] = persistence["previous"]
                report["persistence_current"] = persistence["current"]
                _install_collector(host.full)
                _install_collector(host.widget)
                pages = {"day": "panel.html", "library": "panel.html?library-window=1",
                         "capture": "capture.html", "work": "work.html"}
                for name, page in pages.items():
                    host.open_page(page)
                    def snapshot():
                        return _js(host.full, """(() => {const f=document.getElementById('desktop-content');
                          const d=f?.contentDocument;return {url:location.href,iframe:f?.src,
                          ready:d?.readyState,title:d?.title,text:d?.body?.innerText?.slice(0,400),
                          captureError:d?.getElementById('capture-error')?.hidden?null:
                            d?.getElementById('capture-error')?.textContent,
                          voiceCapabilities:d?.getElementById('capture-form')?{
                            secure:f.contentWindow.isSecureContext,
                            getUserMedia:typeof f.contentWindow.navigator.mediaDevices?.getUserMedia,
                            mediaRecorder:typeof f.contentWindow.MediaRecorder,
                            webm:!!f.contentWindow.MediaRecorder?.isTypeSupported?.('audio/webm')}:null,
                          errors:[...(window.__brainalotQaErrors||[]),
                            ...(f?.contentWindow?.__brainalotQaErrors||[])]};})()""")
                    try:
                        expected = {"day": "BRAINALOT", "library": "БИБЛИОТЕКА",
                                    "capture": "ЗАПИСАТЬ", "work": "Work"}[name]
                        data = _until(lambda: (s if (s := snapshot()).get("ready") == "complete"
                                               and page.split("?")[0] in (s.get("iframe") or "")
                                               and expected in (s.get("text") or "")
                                               and (not peer or name != "work" or "Peer project" in (s.get("text") or ""))
                                               else None), 25)
                    except Exception:
                        report["last_view"] = {"name": name, "snapshot": snapshot()}
                        raise
                    report["views"][name] = data
                    report["errors"].extend(data.get("errors") or [])
                    if name in {"day", "work"}:
                        target = output / f"{name}.png"
                        _screenshot(host.full, target)
                        report["screenshots"][name] = str(target)
                    if peer and name == "work":
                        report["peer_work"] = _peer_work(host.full)
                state = _request(port, token, "/api/time/state")
                if peer:
                    if state["status"] != "running":
                        raise RuntimeError("Chrome peer did not start the timer")
                    report["time_start"] = state["status"]
                else:
                    running = _request(port, token, "/api/time/command", {
                        "request_id": str(uuid.uuid4()), "expected_revision": state["revision"],
                        "action": "start", "mode": "stopwatch", "task": None})
                    report["time_start"] = running["status"]
                for mode in ("bar", "card"):
                    host.window_action(mode)
                    if mode == "bar":
                        host.widget.load_url(host._url("widget.html?mode=bar"))
                    def snapshot_widget():
                        return _js(host.widget, """(() => ({mode:document.body.dataset.widgetMode,
                          time:document.getElementById('widget-time')?.textContent,
                          run:document.getElementById('widget-run')?.textContent,
                          burning:document.getElementById('widget-burning')?.textContent,
                          current:document.getElementById('widget-current')?.textContent,
                          currentHidden:document.getElementById('widget-current')?.hidden,
                          error:document.getElementById('widget-error')?.hidden?null:
                            document.getElementById('widget-error')?.textContent,
                          errors:window.__brainalotQaErrors||[]}))()""")
                    data = _until(lambda: (s if (s := snapshot_widget()).get("mode") == mode
                                           and s.get("run") == "Пауза"
                                           and (not peer or mode != "card" or
                                                (s.get("burning") == "Burning peer task"
                                                 and "Peer task" in (s.get("current") or "")
                                                 and not s.get("currentHidden")))
                                           else None))
                    report["widget"][mode] = data
                    report["errors"].extend(data.get("errors") or [])
                    target = output / f"{mode}.png"
                    _screenshot(host.widget, target)
                    report["screenshots"][mode] = str(target)
                _js(host.widget, "document.getElementById('widget-run').click(); true")
                paused = _until(lambda: (s if (s := _request(port, token, "/api/time/state"))
                                         ["status"] == "paused" else None))
                report["time_pause"] = paused["status"]
                if peer:
                    _marker(root, "peer-paused.json", {"status": paused["status"],
                                                        "revision": paused["revision"]})
                    _until(lambda: (root / "peer-verified.json").is_file(), 60)
                    report["peer_verified"] = True
                    notes = _request(port, token, "/api/notes")["notes"]
                    burning = next(note for note in notes if note["title"] == "Burning peer task")
                    switched = _request(port, token, "/api/time/command", {
                        "request_id": str(uuid.uuid4()), "expected_revision": paused["revision"],
                        "action": "switch_task", "task": {"source": "library", "id": burning["id"],
                                                           "title": burning["title"]}})
                    if switched["task"]["id"] != burning["id"]:
                        raise RuntimeError("Native timer task switch did not persist")
                    report["widget_same_task"] = _until(lambda: (s if (s := snapshot_widget()).get("currentHidden")
                        and s.get("burning") == "Burning peer task" else None), 15)
                    target = output / "card-same.png"
                    _screenshot(host.widget, target)
                    report["screenshots"]["card_same"] = str(target)
            except Exception as exc:
                report["fatal"] = repr(exc)
            finally:
                host.destroy()

        webview.start(inspect, gui="edgechromium", private_mode=False,
                      storage_path=str(root / "state" / "desktop-webview"))
    except Exception as exc:
        report["fatal"] = repr(exc)
    finally:
        if service:
            service.stop()
        report["ok"] = (not report.get("fatal") and len(report["views"]) == 4
                        and len(report["widget"]) == 2
                        and not report["errors"]
                        and report.get("time_start") == "running"
                        and report.get("time_pause") == "paused"
                        and not report["views"]["capture"].get("captureError")
                        and all(not w.get("error") for w in report["widget"].values()))
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
