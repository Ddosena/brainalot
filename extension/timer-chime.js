// End-of-timer chime, synthesized with Web Audio like the ignition sounds (no audio files, no licences).
// A small bell: a few inharmonic sine partials that ring out, struck three times as «дзинь-дзинь-дзинь».

export const TIMER_CHIME_KEY = "panel:timer-chime";

const PARTIALS = [[1, 1], [2.76, 0.42], [5.4, 0.18], [8.93, 0.07]];  // [frequency ratio, level]
const STRIKES = [[0, 880], [0.42, 880], [0.84, 1174.66]];            // [seconds after start, Hz]
const RING = 1.3;                                                    // seconds one strike rings

/** The chime is on unless the user turned it off. */
export function normalizeTimerChime(value) {
  return value !== false;
}

/** Schedule the whole chime into `destination` of any AudioContext (live or offline); returns its length in seconds. */
export function scheduleTimerChime(ctx, destination, {volume = 0.5, at = ctx.currentTime + 0.02} = {}) {
  const master = ctx.createGain();
  master.gain.value = Math.min(1, Math.max(0, volume)) * 0.5;
  master.connect(destination);
  for (const [offset, hz] of STRIKES) {
    const start = at + offset;
    for (const [ratio, level] of PARTIALS) {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = hz * ratio;
      // A 4 ms attack avoids a click; higher partials die faster, as in a real bell.
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.linearRampToValueAtTime(level, start + 0.004);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + RING / ratio ** 0.5);
      oscillator.connect(gain).connect(master);
      oscillator.start(start);
      oscillator.stop(start + RING + 0.05);
    }
  }
  return STRIKES.at(-1)[0] + RING + 0.05;
}

let context = null;
let idleTimer = 0;

/** Play the chime now. Chrome allows it after the user has touched the panel once (starting the timer does). */
export function playTimerChime(options = {}) {
  const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!AudioContextClass) return false;
  context ||= new AudioContextClass();
  context.resume().catch(() => {});
  const length = scheduleTimerChime(context, context.destination, options);
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (context.state === "running") void context.suspend(); }, (length + 1) * 1000);
  return true;
}
