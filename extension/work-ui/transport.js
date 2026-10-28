import { t } from "../i18n.js";
const defaultId = () => globalThis.crypto.randomUUID();
const uncertainError = (error) => error?.status === 0 || error?.status === 408 || (error?.status >= 500 && error?.status < 600);
const intention = (route, action, data) =>
	JSON.stringify({ route, action, data }, (key, value) => (key === "expected_note_version" ? undefined : value));
// connection.api caches ETags internally and turns 304 into the cached body.
// Comparing the projection avoids rendering that body again.
export function createWorkTransport(
	connection,
	{
		onModel = () => {},
		onError = () => {},
		isPaused = () => false,
		onPoll = () => {},
		interval = 750,
		setTimer = setInterval,
		clearTimer = clearInterval,
		requestId = defaultId,
		document: doc = globalThis.document,
		window: win = globalThis.window,
		path = "/api/work/period/model",
	} = {},
) {
	if (typeof connection?.api !== "function") throw new TypeError("Work connection is required");
	let model = null,
		fingerprint = null,
		timer = null,
		busy = 0,
		read = null,
		queue = Promise.resolve(),
		deferred = false,
		uncertain = null;
	let started = false,
		ancillary = null;
	const hidden = () => !!doc?.hidden;
	function poll() {
		if (!started || hidden()) return;
		if (!ancillary) {
			ancillary = Promise.resolve()
				.then(() => (!started || hidden() ? undefined : onPoll()))
				.catch(onError)
				.finally(() => {
					ancillary = null;
				});
		}
		void refresh().catch(() => {});
	}
	function suspend() {
		if (timer !== null) clearTimer(timer);
		timer = null;
	}
	function wake() {
		if (!started) return;
		if (hidden()) {
			suspend();
			return;
		}
		poll();
		if (timer === null) timer = setTimer(poll, interval);
	}
	function accept(value) {
		value = value?.view_model || value;
		if (!value || !Number.isInteger(value.revision)) return false;
		if (model && value.revision < model.revision) return false;
		const next = JSON.stringify(value);
		model = value;
		if (next === fingerprint) return false;
		if (isPaused()) {
			deferred = true;
			return false;
		}
		fingerprint = next;
		onModel(value);
		return true;
	}
	async function refresh({ force = false } = {}) {
		if (busy || hidden() || (!force && isPaused())) {
			deferred = true;
			return model;
		}
		if (read) return read;
		read = connection
			.api(path, "GET", undefined, { timeoutMs: 15000 })
			.then((value) => {
				if (busy || hidden() || (!force && isPaused())) {
					deferred = true;
					return model;
				}
				accept(value);
				return model;
			})
			.catch((error) => {
				onError(error);
				throw error;
			})
			.finally(() => {
				read = null;
			});
		return read;
	}
	async function sendReceipt(receipt) {
		const post = () => connection.api(receipt.route, "POST", receipt.envelope, { timeoutMs: 15000 });
		try {
			return await post();
		} catch (error) {
			if (!uncertainError(error)) {
				uncertain = null;
				throw error;
			}
			try {
				return await post();
			} catch (retryError) {
				if (!uncertainError(retryError)) uncertain = null;
				throw retryError;
			}
		}
	}
	function command(action, data, { route = "/api/work/period/command" } = {}) {
		const operation = async () => {
			busy++;
			try {
				if (read) await read;
				if (!model) await connection.api(path, "GET", undefined, { timeoutMs: 15000 }).then(accept);
				const key = intention(route, action, data),
					previous = uncertain;
				const receipt = previous || {
					route,
					key,
					envelope: { request_id: requestId(), expected_revision: model.revision, action, data: structuredClone(data) },
				};
				uncertain = receipt;
				const result = await sendReceipt(receipt);
				if (Array.isArray(result?.projects) && Array.isArray(result?.tasks)) accept(result);
				else {
					const latest = await connection.api(path, "GET", undefined, { timeoutMs: 15000 });
					accept(latest);
				}
				uncertain = null;
				if (previous && previous.key !== key) throw new Error(t("Предыдущее изменение подтверждено. Повторите новое действие."));
				return { model, result, warnings: result?.warnings || [] };
			} catch (error) {
				// Unknown write outcomes and conflicts are refreshed, never replayed with a new ID.
				try {
					const latest = await connection.api(path, "GET", undefined, { timeoutMs: 15000 });
					accept(latest);
				} catch {
					/* retain last known model */
				}
				onError(error);
				throw error;
			} finally {
				busy--;
			}
		};
		const pending = queue.then(operation, operation);
		queue = pending.catch(() => {});
		return pending;
	}
	return {
		get model() {
			return model;
		},
		refresh,
		command,
		async flush() {
			await queue;
			if (uncertain) throw new Error(t("Ответ на прошлую команду не получен. Повторите последнее действие."));
		},
		start() {
			if (started) return;
			started = true;
			doc?.addEventListener("visibilitychange", wake);
			win?.addEventListener("focus", wake);
			wake();
		},
		stop() {
			started = false;
			suspend();
			doc?.removeEventListener("visibilitychange", wake);
			win?.removeEventListener("focus", wake);
		},
		resume() {
			if (deferred && !isPaused()) {
				deferred = false;
				if (model) accept(model);
				return refresh();
			}
			return Promise.resolve(model);
		},
	};
}
