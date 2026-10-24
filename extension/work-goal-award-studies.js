// Approved Work goal award geometry. No state changes or data calls.
import { extraStudies } from './work-goal-award-extra.js';
import { t } from "./i18n.js";
const gold = '#e9c270';
const light = '#ffe5a1';
const dark = '#966b35';
const blue = '#72b8ca';
const navy = '#183b4d';
const base = '#304d5c';

const studies = [
  {
    id: 'classic', name: t("Классика"), medalNote: t("Круглая медаль с короткой лентой"), cupNote: t("Прямая чаша и открытые ручки"),
    medal: `<path d="M43 4h14l8 23-15 9-15-9Z" fill="${blue}"/><path d="M39 7h9l2 25-11-7Zm13 0h9l-2 18-9 7Z" fill="${light}"/><circle cx="50" cy="65" r="31" fill="${dark}"/><circle cx="50" cy="62" r="31" fill="${gold}"/><circle cx="50" cy="62" r="24" fill="${light}"/><circle cx="50" cy="62" r="20" fill="${navy}"/><path d="M50 46 65 54v17L50 79 35 71V54Z" fill="${blue}"/><path d="M50 46v33L35 71V54Z" fill="#b9e0df"/>`,
    cup: `<path d="M27 21H12v17c0 13 10 20 23 19l-3-8c-8 0-12-4-12-12v-8h10m43-8h15v17c0 13-10 20-23 19l3-8c8 0 12-4 12-12v-8H70" fill="${dark}"/><path d="M26 19h48l-5 29c-2 13-9 20-19 20s-17-7-19-20Z" fill="${gold}"/><path d="M26 19h24v49c-10 0-17-7-19-20Z" fill="${light}"/><path d="M24 17h52v7H24Z" fill="${light}"/><path d="M44 67h12l-3 15H47Z" fill="${dark}"/><path d="M34 82h32l4 7H30Z" fill="${gold}"/><path d="M28 89h44v8H28Z" fill="${base}"/><path d="M50 35 60 40 57 53 50 58 43 53 40 40Z" fill="${blue}"/>`,
  },
  {
    id: 'facets', name: t("Грани"), medalNote: t("Восьмигранник в широкой оправе"), cupNote: t("Угловатая чаша и ломаные ручки"),
    medal: `<path d="m34 9 16-6 16 6-6 20H40Z" fill="${blue}"/><path d="m37 12 13-4 13 4-5 17H42Z" fill="${light}"/><path d="m31 29 19-8 19 8 11 21-5 26-25 17-25-17-5-26Z" fill="${dark}"/><path d="m33 33 17-7 17 7 8 19-4 21-21 15-21-15-4-21Z" fill="${gold}"/><path d="m36 40 14-6 14 6 6 14-5 16-15 10-15-10-5-16Z" fill="${navy}"/><path d="m50 34 14 6 6 14-20 26Z" fill="${blue}"/><path d="m30 54 20-20v46L35 70Z" fill="#acd7da"/>`,
    cup: `<path d="M29 24H12l2 24 17 8 4-8-13-6-2-10h12m39-8h17l-2 24-17 8-4-8 13-6 2-10H68" fill="${dark}"/><path d="m25 19 50 0-5 34-20 18-20-18Z" fill="${gold}"/><path d="m25 19 25 0v52L30 53Z" fill="${light}"/><path d="m50 19 25 0-5 34-20 18Z" fill="#c99d50"/><path d="M24 16h52v7H24Z" fill="${light}"/><path d="M44 68h12l-4 16h-4Z" fill="${dark}"/><path d="m37 82 26 0 6 8H31Z" fill="${gold}"/><path d="M26 90h48v8H26Z" fill="${base}"/><path d="m50 33 13 10-13 15-13-15Z" fill="${blue}"/>`,
  },
  {
    id: 'rays', name: t("Лучи"), medalNote: t("Орден со ступенчатыми лучами"), cupNote: t("Высокая чаша с крыловидными ручками"),
    medal: `<path d="M38 2h10l2 30-11 11-7-11Zm14 0h10l6 30-7 11-11-11Z" fill="${blue}"/><path d="m50 22 9 15 17-7-2 19 16 11-16 10 2 19-17-7-9 15-9-15-17 7 2-19-16-10 16-11-2-19 17 7Z" fill="${dark}"/><path d="m50 25 8 16 17-8-3 18 15 9-15 9 3 18-17-8-8 16-8-16-17 8 3-18-15-9 15-9-3-18 17 8Z" fill="${gold}"/><circle cx="50" cy="60" r="21" fill="${light}"/><circle cx="50" cy="60" r="16" fill="${navy}"/><path d="m50 45 4 11 11 4-11 4-4 11-4-11-11-4 11-4Z" fill="${blue}"/>`,
    cup: `<path d="M25 23 7 14l7 28 20 16 4-8-17-13-4-12 11 5m47-7 18-9-7 28-20 16-4-8 17-13 4-12-11 5" fill="${dark}"/><path d="M31 20h38l-4 34-15 20-15-20Z" fill="${gold}"/><path d="M31 20h19v54L35 54Z" fill="${light}"/><path d="M28 17h44v7H28Z" fill="${light}"/><path d="M45 73h10l-3 11h-4Z" fill="${dark}"/><path d="M34 84h32l4 7H30Z" fill="${gold}"/><path d="M27 91h46v7H27Z" fill="${base}"/><path d="M42 40h16l-3 14-5 6-5-6Z" fill="${blue}"/>`,
  },
  {
    id: 'petals', name: t("Лепестки"), medalNote: t("Медаль с волнистым краем"), cupNote: t("Раскрытая чаша с округлыми ручками"),
    medal: `<path d="m36 5 14 7 14-7-5 28H41Z" fill="${blue}"/><path d="m50 25 9 7 11-3 4 11 10 6-3 12 5 11-9 8-2 12-12 1-9 8-10-7-12 2-5-11-11-5 2-12-6-10 8-9 1-12 12-2 8-9Z" fill="${dark}"/><path d="m50 30 9 7 10-3 4 10 9 5-3 11 4 9-8 7-2 11-11 1-8 7-9-6-11 1-4-10-10-4 2-11-5-9 7-8 1-11 11-2 7-8Z" fill="${gold}"/><circle cx="50" cy="62" r="22" fill="${light}"/><circle cx="50" cy="62" r="18" fill="${blue}"/><path d="M50 48c8 8 13 14 0 28-13-14-8-20 0-28Z" fill="${navy}"/>`,
    cup: `<path d="M26 27C8 16 4 39 20 53l15 1-2-9-12-2c-6-5-5-13 4-9m49-7c18-11 22 12 6 26l-15 1 2-9 12-2c6-5 5-13-4-9" fill="${dark}"/><path d="M19 24Q50 8 81 24L70 48Q64 70 50 71 36 70 30 48Z" fill="${gold}"/><path d="M19 24Q35 16 50 16v55Q36 70 30 48Z" fill="${light}"/><path d="M20 23q30-13 60 0v6q-30-10-60 0Z" fill="${light}"/><path d="M46 70h8l-2 13h-4Z" fill="${dark}"/><path d="M35 83h30l5 8H30Z" fill="${gold}"/><path d="M27 91h46v7H27Z" fill="${base}"/><path d="M39 38q11-8 22 0l-4 17-7 6-7-6Z" fill="${blue}"/>`,
  },
  {
    id: 'oval', name: t("Овал"), medalNote: t("Вытянутая овальная медаль"), cupNote: t("Тонкий кубок с узкой высокой ножкой"),
    medal: `<path d="M37 6h26l-7 26H44Z" fill="${blue}"/><path d="M42 8h16l-5 23h-6Z" fill="${light}"/><ellipse cx="50" cy="62" rx="27" ry="36" fill="${dark}"/><ellipse cx="50" cy="59" rx="27" ry="36" fill="${gold}"/><ellipse cx="50" cy="59" rx="21" ry="30" fill="${light}"/><ellipse cx="50" cy="59" rx="17" ry="26" fill="${navy}"/><path d="M50 38 61 54l-11 24-11-24Z" fill="${blue}"/><path d="M50 38v40L39 54Z" fill="#bce1e0"/>`,
    cup: `<path d="M35 22H18v14c0 10 8 17 19 18l1-8c-8-1-12-5-12-11v-5h11m28-8h17v14c0 10-8 17-19 18l-1-8c8-1 12-5 12-11v-5H63" fill="${dark}"/><path d="M35 18h30l-3 30c-1 12-7 21-12 23-5-2-11-11-12-23Z" fill="${gold}"/><path d="M35 18h15v53c-5-2-11-11-12-23Z" fill="${light}"/><path d="M33 16h34v7H33Z" fill="${light}"/><path d="M47 70h6v17h-6Z" fill="${dark}"/><path d="M36 87h28l5 6H31Z" fill="${gold}"/><path d="M29 93h42v6H29Z" fill="${base}"/><path d="m50 33 8 11-8 13-8-13Z" fill="${blue}"/>`,
  },
  {
    id: 'hex', name: t("Шестигранник"), medalNote: t("Шестиугольный подвес"), cupNote: t("Шестиугольная чаша и прямые ручки"),
    medal: `<path d="M42 3h16l7 24-15 8-15-8Z" fill="${blue}"/><path d="M50 3v32l-15-8 7-24Z" fill="${light}"/><path d="m50 29 26 15v32L50 92 24 76V44Z" fill="${dark}"/><path d="m50 32 23 14v28L50 88 27 74V46Z" fill="${gold}"/><path d="m50 39 18 10v22L50 81 32 71V49Z" fill="${navy}"/><path d="m50 39 18 10-18 32Z" fill="${blue}"/><path d="m50 39-18 10v22l18 10Z" fill="#bce0e1"/>`,
    cup: `<path d="M29 23H13v25l19 10 5-8-15-7V31h10m39-8h16v25L68 58l-5-8 15-7V31H68" fill="${dark}"/><path d="m28 19 44 0 7 12-8 29-21 12-21-12-8-29Z" fill="${gold}"/><path d="m28 19 22 0v53L29 60l-8-29Z" fill="${light}"/><path d="M26 16h48v7H26Z" fill="${light}"/><path d="M44 71h12l-3 12h-6Z" fill="${dark}"/><path d="M35 83h30l5 8H30Z" fill="${gold}"/><path d="M27 91h46v7H27Z" fill="${base}"/><path d="m50 35 12 7-3 14-9 5-9-5-3-14Z" fill="${blue}"/>`,
  },
];

studies.push(...extraStudies);
export const AWARD_STUDIES = Object.freeze(studies.map(({id,name,medalNote,cupNote}) => Object.freeze({id,name,medalNote,cupNote})));
const byId = new Map(studies.map(study => [study.id, study]));

/** Trusted, hand-authored geometry only; unknown IDs render nothing. */
export function renderAward(id, kind) {
  const study = byId.get(id);
  if (!study || (kind !== 'medal' && kind !== 'cup')) return '';
  const title = `${study.name}: ${kind === 'medal' ? study.medalNote : study.cupNote}`;
  const safeTitle = title.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  return `<svg class="award-svg award-svg--${kind}" viewBox="0 0 100 104" role="img" aria-label="${safeTitle}" xmlns="http://www.w3.org/2000/svg">${study[kind]}</svg>`;
}
