// Temporary release gate. Keep Work data and implementation intact for a later return.
import { t } from "./i18n.js";
export const WORK_ENABLED = false;
export const WORK_PAUSED_MESSAGE = t("Work временно отключён. Ваши задачи, доски и изображения сохранены.");

const WORK_COMMANDS = new Set([
  "work-snapshot", "work-proposals", "work-proposal-resolve",
  "chats-status", "chats-sync", "chats-remove", "chat-setup", "chat-connect",
]);

export const workCommandDisabled = type => !WORK_ENABLED && WORK_COMMANDS.has(type);
export const browserActionAllowed = action => WORK_ENABLED || action === "active-tab";
