// Work mode dates are local calendar days kept as "YYYY-MM-DD" strings.
import { formatDate, t } from "./i18n.js";

const DAY = 86400000;

export function parse(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d, 12);
}

export function fmt(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export const today = () => fmt(new Date());

export function addDays(s, n) {
  const d = parse(s);
  d.setDate(d.getDate() + n);
  return fmt(d);
}

/** Days from a to b; positive when b is later. */
export const diff = (a, b) => Math.round((parse(b) - parse(a)) / DAY);

const dow = s => (parse(s).getDay() + 6) % 7;

export const mondayOf = s => addDays(s, -dow(s));
export const isWeekend = s => dow(s) >= 5;
export const weekday = s => formatDate(parse(s), { weekday: "short" }).replace(/\.$/, "");
export const dayMonth = s => formatDate(parse(s), { day: "numeric", month: "short" }).replace(/\.$/, "");
export const longDay = s => formatDate(parse(s), { weekday: "long", day: "numeric", month: "long" });

export function relDays(n) {
  if (n === 0) return t("сегодня");
  if (n === 1) return t("завтра");
  if (n === -1) return t("вчера");
  if (n === 2) return t("послезавтра");
  return n > 0 ? t("через {n} дн.", { n }) : t("{n} дн. назад", { n: -n });
}
