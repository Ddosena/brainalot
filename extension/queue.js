import { t } from "./i18n.js";
export const QUEUE_PREFIX = "capture:";
/** A queued dictation goes to POST /api/dictation; other records are plain captures. */
export const DICTATION = "dictation";

export function queueStatus(records) {
  return {
    queue_count: records.length,
    queue: records.slice(0, 20).map(item => ({
      id: item.id,
      payload: { text: String(item.payload?.text ||
        (item.payload?.attachments?.length ? t("Вложение: {name}", {name: item.payload.attachments.map(file => file.name).join(", ")}) : "")).slice(0, 150) },
      last_error: item.last_error ? String(item.last_error).slice(0, 300) : null,
    })),
  };
}

/** Durable captures. Each UUID has its own key; HTTP never owns the storage lock. */
export class CaptureQueue {
  constructor({ storage, send, uuid = () => crypto.randomUUID(), now = () => new Date().toISOString() }) {
    this.storage = storage;
    this.send = send;
    this.uuid = uuid;
    this.now = now;
    this.tail = Promise.resolve();
    this.flushing = null;
  }

  serial(action) {
    const result = this.tail.then(action);
    this.tail = result.catch(() => {});
    return result;
  }

  enqueue(payload, id = this.uuid(), type = "capture") {
    return this.serial(async () => {
      // A capture window can retry after a worker restart without creating a second record.
      const existing = (await this.storage.get(QUEUE_PREFIX + id))[QUEUE_PREFIX + id];
      const expected = { ...payload, source: payload.source === "voice" ? "voice" : "chrome", external_id: id };
      if (existing?.id === id) {
        if ((existing.type || "capture") !== type || JSON.stringify(existing.payload) !== JSON.stringify(expected)) {
          throw new Error(t("Черновик изменился во время сохранения. Откройте окно записи заново и проверьте текст."));
        }
        return existing;
      }
      // captured_at also tells the service which «завтра» a delayed dictation meant.
      const record = {
        id, ...(type === "capture" ? {} : { type }), payload: expected,
        captured_at: this.now(), attempts: 0, last_error: null,
      };
      await this.storage.set({ [QUEUE_PREFIX + id]: record });
      return record;
    });
  }

  list() {
    return this.serial(async () => {
      const data = await this.storage.get(null);
      return Object.entries(data)
        .filter(([key, value]) => key.startsWith(QUEUE_PREFIX) && value?.id && value?.payload)
        .map(([, value]) => value)
        .sort((a, b) => a.captured_at.localeCompare(b.captured_at) || a.id.localeCompare(b.id));
    });
  }

  flush() {
    if (this.flushing) return this.flushing;
    this.flushing = this.flushBatch().finally(() => { this.flushing = null; });
    return this.flushing;
  }

  async flushBatch() {
    const records = await this.list();
    let sent = 0;
    let error = null;
    for (const record of records.slice(0, 20)) {
      try {
        const result = await this.send(record.payload, record);
        if (!result || typeof result.id !== "string") {
          throw new Error(t("Сервис не подтвердил сохранение записи."));
        }
        // A failed remove retains the UUID. The next attempt is safely deduplicated by the API.
        await this.serial(() => this.storage.remove(QUEUE_PREFIX + record.id));
        sent += 1;
      } catch (failure) {
        error = failure.message || t("Не удалось отправить запись.");
        await this.serial(() => this.storage.set({
          [QUEUE_PREFIX + record.id]: {
            ...record, attempts: record.attempts + 1, last_error: error,
          },
        }));
        // Validation/conflict errors must remain visible, but should not starve later captures.
        if (![400, 404, 409, 422].includes(failure.status)) break;
      }
    }
    return { sent, error, remaining: (await this.list()).length };
  }
}

/** The form is cleared only after durable storage confirms its write. */
export async function submitCapture(payload, enqueue, clearInput) {
  const result = await enqueue(payload);
  clearInput();
  return result;
}
