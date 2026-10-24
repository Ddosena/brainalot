import { timeClient, sharedApi, elapsedAt } from "./time-client.js";
import { t } from "./i18n.js";

export function cubeTimerRef(cube) {
  if (!cube?.id || !cube?.title) throw new Error(t("Не выбран кубик"));
  return {source: "work", id: `work-cube:${cube.id}`, title: String(cube.title).replace(/[\r\n\x00]/g, " ").slice(0, 512)};
}

export function createCubeTimer(client, api) {
  const related = (cube, state) => state?.task?.source === "work" && state.task.id === cubeTimerRef(cube).id;
  return {
    async start(cube) {
      if (!Number.isInteger(cube.minutes) || cube.minutes < 1 || cube.minutes > 240) throw new Error(t("Укажите длительность кубика от 1 до 240 минут"));
      const state = await client.refresh();
      if (related(cube, state) && state.mode === "pomodoro") {
        if (state.status === "running") return state;
        if (state.status === "paused") return client.command("resume");
      }
      return client.command("start", {task: cubeTimerRef(cube), mode: "pomodoro", duration_ms: cube.minutes * 60000});
    },
    async pause(cube) {
      const state = await client.refresh();
      return related(cube, state) && state.status === "running" ? client.command("pause") : state;
    },
    async stop(cube) {
      const state = await client.refresh();
      return related(cube, state) && ["running", "paused"].includes(state.status) ? client.command("stop") : state;
    },
    state(cube) {
      const state = client.state;
      if (!related(cube, state)) return {status: "idle", elapsed_ms: 0, remaining_ms: cube.minutes * 60000};
      const elapsed = elapsedAt(state);
      return {status: state.status, elapsed_ms: elapsed, remaining_ms: Math.max(0, state.duration_ms - elapsed)};
    },
    async recordedMinutes(cube) {
      const totals = await api("/api/time/totals");
      const row = totals.rows.find(row => related(cube, row));
      return Math.round((row?.total_ms || 0) / 60000);
    },
  };
}

const timer = createCubeTimer(timeClient, sharedApi);
export const startCubeTimer = cube => timer.start(cube);
export const pauseCubeTimer = cube => timer.pause(cube);
export const stopCubeTimer = cube => timer.stop(cube);
export const cubeTimerState = cube => timer.state(cube);
export const recordedCubeMinutes = cube => timer.recordedMinutes(cube);
