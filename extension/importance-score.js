import { t } from "./i18n.js";
/** Numeric task importance, with the legacy labels retained for older clients. */
export const validImportanceScore = value => Number.isInteger(value) && value >= 0 && value <= 5;
export const scoreForLegacy = level => ({low:1,normal:2,high:4,critical:5}[level] ?? 0);
export function importanceScore(record, fallback = 0) {
  return validImportanceScore(record?.importance_score) ? record.importance_score
    : typeof record?.importance === "string" ? scoreForLegacy(record.importance) : fallback;
}
export function legacyForScore(score, kind = "task") {
  if (!validImportanceScore(score)) throw new RangeError(t("Важность: целое число от 0 до 5"));
  return score < 2 ? "low" : score === 2 ? "normal"
    : score === 5 && ["task", "purchase"].includes(kind) ? "critical" : "high";
}
