import { sharedApi } from "./time-client.js";
import { t } from "./i18n.js";
export async function browserCommand(action, payload = {}) {
  const queued = await sharedApi("/api/browser/commands", "POST", { request_id: crypto.randomUUID(), action, payload });
  const deadline = Date.now() + 32000;
  while (Date.now() < deadline) {
    const command = await sharedApi(`/api/browser/commands/${encodeURIComponent(queued.id)}`);
    if (command.status === "done") return command.result;
    if (["error", "uncertain"].includes(command.status)) throw new Error(command.error || t("Браузер не подтвердил действие. Проверьте Chrome перед повтором."));
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(t("Браузер не подтвердил действие. Проверьте Chrome перед повтором."));
}

export function startBrowserBridge(api, execute, chromeApi = globalThis.chrome) {
  if (chromeApi.desktop) return;
  const client_id = crypto.randomUUID();
  let busy = false;
  async function poll() {
    if (busy) return; busy = true;
    try {
      const result = await api("/api/browser/poll", "POST", { client_id });
      for (const command of result.commands) {
        let reply;
        try { reply = { result: await execute(command.action, command.payload) }; }
        catch (error) { reply = { error: error.message }; }
        await api(`/api/browser/commands/${command.id}/result`, "POST", { client_id, ...reply });
      }
    } catch { /* Service may be offline. Never replay a leased browser action. */ }
    finally { busy = false; }
  }
  void poll(); setInterval(poll, 5000);
  chromeApi.alarms.create("brainalot-browser-bridge", { periodInMinutes: 0.5 });
  chromeApi.alarms.onAlarm.addListener(alarm => { if (alarm.name === "brainalot-browser-bridge") void poll(); });
}
