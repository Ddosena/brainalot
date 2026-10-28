import { t } from "../i18n.js";
import {actionAttrs,modalClose} from "./dialogs.js";
import {
	timeClient,
	sharedApi,
	elapsedAt,
	formatTime,
	taskKey,
	getTimePreset,
	saveTimePreset,
	subscribeTimePreset,
	stopMatchingTimeTask,
} from "../time-client.js";

export const cubeTimeTask = (cube) => ({
	source: cube.standalone && cube.source_ref ? "library" : "work",
	id: cube.standalone && cube.source_ref ? cube.source_ref : "work-cube:" + cube.id,
	title: cube.title,
	activity_kind: cube.activity_kind || (cube.goal ? "work" : "personal"),
});
const cubeTimeKeys = (cube) =>
	new Set([
		taskKey(cubeTimeTask(cube)),
		...(cube.standalone && cube.source_ref ? [taskKey({ source: "work", id: "work-cube:" + cube.id })] : []),
	]);
export const cubeTargetMs = (cube) => Math.max(1, Math.min(86400000, Math.round(Number(cube.min || cube.minutes || 0) * 60000)));
export function cubeElapsedMs(cube, snapshot, state, now = Date.now()) {
	const keys = cubeTimeKeys(cube);
	let total = (snapshot?.rows || []).reduce(
		(sum, row) => (keys.has(taskKey(row.task)) ? sum + Math.max(0, Number(row.total_ms) || 0) : sum),
		0,
	);
	const before = snapshot?.state;
	if (
		before?.status === "running" &&
		before.mode !== "break" &&
		state?.mode !== "break" &&
		keys.has(taskKey(before.task)) &&
		taskKey(before.task) === taskKey(state?.task) &&
		before.revision === state.revision
	) {
		total += Math.max(0, elapsedAt(state, now) - before.elapsed_ms);
	}
	return total;
}
export const cubeRemainingMs = (cube, snapshot, state, now) =>
	Math.max(0, Math.ceil(cubeTargetMs(cube) - cubeElapsedMs(cube, snapshot, state, now)));

export function installTimer(
	ctx,
	{
		client = timeClient,
		api = sharedApi,
		presetGet = getTimePreset,
		presetSave = saveTimePreset,
		now = () => Date.now(),
		requestFrame = (fn) => requestAnimationFrame(fn),
		cancelFrame = (id) => cancelAnimationFrame(id),
	} = {},
) {
	const { S, U, esc } = ctx;
	let totals = null,
		summary = null,
		preset = { work_ms: 1500000, break_ms: 300000 },
		totalsPending = null,
		summaryPending = null,
		lastTotals = -Infinity,
		lastSummary = -Infinity,
		frame = null,
		lastPaint = 0,
		disposed = false,
		busy = false;
	const operations = new Set();
	const track = (fn) => {
		const pending = Promise.resolve().then(fn);
		operations.add(pending);
		void pending.finally(() => operations.delete(pending)).catch(() => {});
		return pending;
	};
	const active = () => client.state && ["running", "paused", "finished"].includes(client.state.status);
	const activeCube = () =>
		client.state?.task?.source === "work" && client.state.task.id.startsWith("work-cube:")
			? ctx.cube(client.state.task.id.slice("work-cube:".length))
			: client.state?.task?.source === "library"
				? S.cubes.find((k) => k.standalone && k.source_ref === client.state.task.id)
				: null;
	const sameCube = (k) => k && cubeTimeKeys(k).has(taskKey(client.state?.task));
	const zone = () =>
		(S.planning_initialized !== false && S.settings?.timezone) || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
	const setText = (el, value) => {
		if (el && el.textContent !== value) el.textContent = value;
	};
  function timerButton(k) {
    if(k.isRepeatableTemplate||(k.day==null&&!k.time_retained))return '';
    const binding=actionAttrs('timer-toggle',k.id,esc),label=t('Начать учёт времени');
    return `<button type="button" class="cube-play" ${binding} data-work-play="${esc(k.id)}"`+
      ` aria-label="${label}" title="${label}">▶</button>`;
  }
  function timerBar() {
    return '<button class="btn small active-timer" id="work-active-timer" data-act="timer-open">'+
      '<span aria-hidden="true">⏱</span><span data-active-title></span><output data-active-clock>00:00</output></button>';
  }
  function timerReading(k,id) {
    const target=k?' / '+formatTime(cubeTargetMs(k)):'';
    return `<div class="timer-reading"><span>${t('Учтено')} <output data-time-elapsed="${esc(id)}">00:00</output>${target}`+
      `</span><span>${t('Осталось')} <output data-time-remaining="${esc(id)}">00:00</output></span></div>`;
  }
  function timerAction(action,label,className,attributes,id) {
    const binding=id==null?`data-act="${action}"`:actionAttrs(action,id,esc);
    const extra=attributes?' '+attributes:'';
    return `<button type="button" class="${className}" ${binding}${extra}>${label}</button>`;
  }
  function timerActions(id) {
    const start=timerAction('timer-play','▶ '+t('Начать'),'btn primary',`data-time-start="${esc(id)}"`,id);
    const pause=timerAction('timer-pause',t('Пауза'),'btn','data-time-pause');
    const rest=timerAction('timer-rest',t('Отдых'),'btn','',id);
    const stop=timerAction('timer-stop',t('Завершить'),'btn','data-time-stop');
    const retry=timerAction('timer-retry',t('Проверить повтор'),'link','data-time-retry hidden');
    return `<div class="timer-actions">${start}${pause}${rest}${stop}${retry}</div>`;
  }
  function presetField(kind,label,ms) {
    const minutes=ms/60000;
    return `<label>${label}<input data-time-preset="${kind}" type="number" min="1" max="180" step="1" value="${minutes}"></label>`;
  }
  function timerSettings() {
    const work=presetField('work',t('Работа, мин'),preset.work_ms);
    const rest=presetField('break',t('Отдых, мин'),preset.break_ms);
    return `<details class="timer-settings"><summary>${t('Настройки таймера')}</summary>`+
      `<div class="fields">${work}${rest}</div>`+
      `<button type="button" class="btn small" data-act="timer-preset">${t('Сохранить настройки')}</button></details>`;
  }
  function timerControls(k) {
    const id=k?.id||'',reading=timerReading(k,id),actions=timerActions(id);
    return `<section class="work-timer" data-work-timer="${esc(id)}">${reading}\n      ${actions}\n      `+
      `<p class="muted">${t('Учтённое время не меняет готовность кубика.')}</p>${timerSettings()}</section>`;
  }
  function statisticRow([key,label]) {
    return `<span>${label}</span><output data-time-stat="${key}">00:00</output>`;
  }
  function timerModal() {
    const cube=activeCube(),title=esc(cube?.title||client.state?.task?.title||t('Учёт времени'));
    const rows=[
      ['work_ms',t('Работа')],['personal_ms',t('Дела')],
      ['rest_ms',t('Отдых')],['unclassified_ms',t('Без категории')],
    ];
    const statistics=rows.map(statisticRow).join('');
    return `<div class="modal"><div class="mh"><h2>${title}</h2>${modalClose()}</div>${timerControls(cube)}`+
      `<h3>${t('За день')} · ${ctx.ddmm(U.day)}</h3><div class="timer-stats">${statistics}</div></div>`;
  }
	function paintBar(doc, state, daySummary) {
		const bar = doc.querySelector("#work-active-timer");
		if (bar) {
			const work=formatTime(daySummary?.work_ms||0),personal=formatTime(daySummary?.personal_ms||0);
			bar.hidden = false;
			setText(
				bar.querySelector("[data-active-title]"),
				active()
					? state?.mode === "break"
						? t("Отдых")
						: activeCube()?.title || state?.task?.title || t("Без задачи")
					: `${t("Работа")} / ${t("Дела")}`,
			);
			setText(
				bar.querySelector("[data-active-clock]"),
				active()
					? formatTime(
							state?.mode === "stopwatch" ? elapsedAt(state, now()) : Math.max(0, (state?.duration_ms || 0) - elapsedAt(state, now())),
						)
					: daySummary
						? `${work} · ${personal}`
						: "— · —",
			);
			bar.title = `${t("За день")} · ${ctx.ddmm(U.day)}`;
		}
	}
	function paintCubes(doc, state, byId) {
		for (const node of doc.querySelectorAll("[data-cube]")) {
			const k = byId.get(node.dataset.cube);
			if (!k) continue;
			const value = Math.min(100, (cubeElapsedMs(k, totals, state, now()) / cubeTargetMs(k)) * 100).toFixed(2) + "%";
			if (node.style.getPropertyValue("--elapsed") !== value) node.style.setProperty("--elapsed", value);
			node.classList.toggle("time-running", !!(sameCube(k) && state?.status === "running" && state.mode !== "break"));
		}
		for (const button of doc.querySelectorAll("[data-work-play]")) {
			const k = byId.get(button.dataset.workPlay),
				running = sameCube(k) && state?.status === "running" && state.mode !== "break";
			setText(button, running ? "Ⅱ" : "▶");
			button.title = button.ariaLabel = running ? t("Пауза") : t("Начать учёт времени");
			button.disabled = busy;
		}
	}
	function paintControls(doc, state, byId) {
		for (const host of doc.querySelectorAll("[data-work-timer]")) {
			const k = byId.get(host.dataset.workTimer) || activeCube(),
				remaining = k ? cubeRemainingMs(k, totals, state, now()) : Math.max(0, (state?.duration_ms || 0) - elapsedAt(state, now()));
			setText(host.querySelector("[data-time-elapsed]"), formatTime(k ? cubeElapsedMs(k, totals, state, now()) : elapsedAt(state, now())));
			setText(host.querySelector("[data-time-remaining]"), formatTime(remaining));
			const start = host.querySelector("[data-time-start]");
			if (start) {
				const resume=sameCube(k)&&state?.status==='paused'&&state.mode!=='break';
				const label=resume?t('Продолжить'):t('Начать');
				setText(start, `▶ ${label}`);
				start.disabled =
					busy || (!k && state?.status !== "paused") || (sameCube(k) && state?.status === "running" && state.mode !== "break");
			}
			host.querySelector("[data-time-pause]").disabled = busy || state?.status !== "running";
			host.querySelector("[data-time-stop]").disabled = busy || !active();
			host.querySelector("[data-time-retry]").hidden = !client.pending;
		}
	}
	function paintSummary(doc, daySummary) {
		for (const output of doc.querySelectorAll("[data-time-stat]"))
			setText(output, daySummary ? formatTime(daySummary[output.dataset.timeStat] || 0) : "—");
		for (const input of doc.querySelectorAll("[data-time-preset]"))
			if (input.dataset.workDirty !== "1" && input !== doc.activeElement) {
				const value = String(preset[input.dataset.timePreset === "break" ? "break_ms" : "work_ms"] / 60000);
				if (input.value !== value) input.value = value;
			}
	}
	function paint() {
		if (disposed) return;
		const state = client.state,
			doc = globalThis.document;
		if (!doc) return;
		const daySummary = summary?.day === ctx.iso(U.day) && summary.timezone === zone() ? summary : null;
		const byId = new Map(S.cubes.map((k) => [k.id, k]));
		paintBar(doc, state, daySummary);
		paintCubes(doc, state, byId);
		paintControls(doc, state, byId);
		paintSummary(doc, daySummary);
	}
	function schedule() {
		if (disposed || frame != null || client.state?.status !== "running" || globalThis.document?.hidden) return;
		frame = requestFrame(() => {
			frame = null;
			if (now() - lastPaint >= 100) {
				lastPaint = now();
				paint();
			}
			schedule();
		});
	}
	async function refreshTotals(force = false) {
		if (totalsPending) return totalsPending;
		if (!force && now() - lastTotals < 1500) return totals;
		lastTotals = now();
		totalsPending = api("/api/time/totals")
			.then((value) => {
				totals = value;
				paint();
				return value;
			})
			.finally(() => (totalsPending = null));
		return totalsPending;
	}
	async function refreshSummary(force = false) {
		if (summaryPending) return summaryPending;
		if (!force && summary?.day === ctx.iso(U.day) && summary.timezone === zone() && now() - lastSummary < 5000) return summary;
		lastSummary = now();
		summaryPending = api(`/api/time/summary?day=${ctx.iso(U.day)}&timezone=${encodeURIComponent(zone())}`)
			.then((value) => {
				summary = value;
				paint();
				return value;
			})
			.finally(() => (summaryPending = null));
		return summaryPending;
	}
	async function poll() {
		await Promise.all([client.refresh(), refreshTotals(), refreshSummary()]);
		paint();
		schedule();
	}
	function reportError(error) {
		ctx.toast(error.message || String(error));
		return false;
	}
	async function command(action, fields = {}) {
		busy = true;
		paint();
		try {
			await client.command(action, fields);
			await refreshTotals(true);
			await refreshSummary(true);
			return true;
		} catch (error) {
			return reportError(error);
		} finally {
			busy = false;
			paint();
			schedule();
		}
	}
	async function play(id) {
		try {
			await client.refresh();
			const k = id ? ctx.cube(id) : activeCube();
			if (id && !k) {
				ctx.toast(t("Объект изменился или удалён; обновите план."));
				return false;
			}
			if (!k) {
				if (client.state?.status === "paused") return command("resume");
				return false;
			}
			await refreshTotals(true);
			const task = cubeTimeTask(k),
				duration_ms = cubeRemainingMs(k, totals, client.state, now());
			const paused =
				client.state?.status === "paused" &&
				client.state.mode === "pomodoro" &&
				taskKey(client.state.task) === taskKey(task) &&
				client.state.task.activity_kind === task.activity_kind;
			const intervalRemaining = Math.max(0, (client.state?.duration_ms || 0) - (client.state?.elapsed_ms || 0));
			if (paused && Math.abs(intervalRemaining - duration_ms) <= 1) return command("resume");
			if (!duration_ms) {
				ctx.toast(t("Время кубика уже учтено полностью."));
				return false;
			}
			return command("start", { mode: "pomodoro", duration_ms, task });
		} catch (error) {
			return reportError(error);
		}
	}
	async function categoryChanged(id) {
		try {
			await client.refresh();
			const k = ctx.cube(id);
			if (!k || !sameCube(k) || !["running", "paused"].includes(client.state.status)) return;
			const next = cubeTimeTask(k);
			if (next.activity_kind !== client.state.task.activity_kind) await command("switch_task", { task: next });
		} catch (error) {
			ctx.toast(error.message || String(error));
		}
	}
	async function beforeComplete(id) {
		busy = true;
		paint();
		try {
			const k = ctx.cube(id);
			if (!k) return false;
			const refs = [cubeTimeTask(k), ...(k.standalone && k.source_ref ? [{ source: "work", id: "work-cube:" + k.id }] : [])];
			await stopMatchingTimeTask(client, refs);
			await refreshTotals(true);
			await refreshSummary(true);
			return true;
		} catch (error) {
			return reportError(error);
		} finally {
			busy = false;
			paint();
			schedule();
		}
	}
	async function toggle(id) {
		try {
			const k = ctx.cube(id);
			await client.refresh();
			return sameCube(k) && client.state.status === "running" && client.state.mode !== "break" ? command("pause") : play(id);
		} catch (error) {
			return reportError(error);
		}
	}
	async function rest(id) {
		try {
			await client.refresh();
			preset = await presetGet();
			const k = ctx.cube(id) || activeCube();
			return command("start", { mode: "break", duration_ms: preset.break_ms, task: k ? cubeTimeTask(k) : client.state?.task || null });
		} catch (error) {
			return reportError(error);
		}
	}
	async function open() {
		U.modal = { type: "timer" };
		ctx.renderLayer();
		try {
			preset = await presetGet();
			await Promise.all([poll(), refreshSummary(true)]);
		} catch (error) {
			ctx.toast(error.message || String(error));
		}
		paint();
	}
	async function savePreset() {
		const doc = globalThis.document,
			w = +doc.querySelector("[data-time-preset=work]")?.value,
			b = +doc.querySelector("[data-time-preset=break]")?.value;
		if (![w, b].every((n) => Number.isInteger(n) && n >= 1 && n <= 180)) {
			ctx.toast(t("Укажите целое время от 1 до 180 минут."));
			return false;
		}
		try {
			preset = await presetSave({ work_ms: w * 60000, break_ms: b * 60000 });
			for (const input of doc.querySelectorAll("[data-time-preset]")) delete input.dataset.workDirty;
			ctx.toast(t("Сохранено"));
			return true;
		} catch (error) {
			return reportError(error);
		}
	}
	const unsubscribe = client.subscribe(() => {
		paint();
		schedule();
	});
	const unsubscribePreset = subscribeTimePreset((value) => {
		preset = value;
		paint();
	});
	void presetGet()
		.then((value) => {
			preset = value;
			paint();
		})
		.catch(() => {});
	Object.assign(ctx, {
		timerButton,
		timerBar,
		timerControls,
		timerModal,
		paintTimer: paint,
		timerPoll: poll,
		timerPlay: (id) => track(() => play(id)),
		timerToggle: (id) => track(() => toggle(id)),
		timerPause: () => track(() => command("pause")),
		timerStop: () => track(() => command("stop")),
		timerRest: (id) => track(() => rest(id)),
		timerOpen: open,
		timerSavePreset: () => track(savePreset),
		timerCategoryChanged: (id) => track(() => categoryChanged(id)),
		timerBeforeComplete: (id) => {
			const earlier = [...operations];
			return track(async () => {
				await Promise.allSettled(earlier);
				return beforeComplete(id);
			});
		},
		timerFlush: async () => {
			await Promise.allSettled([...operations]);
			if (client.pending) throw new Error(t("Дождитесь ответа сервиса."));
		},
		timerRetry: () =>
			track(async () => {
				try {
					await client.retry();
					await refreshTotals(true);
				} catch (error) {
					ctx.toast(error.message || String(error));
				}
			}),
		timerDestroy: () => {
			disposed = true;
			unsubscribe();
			unsubscribePreset();
			if (frame != null) cancelFrame(frame);
		},
	});
}
