"""Shared Work/Library activity classification and optional Note cube metadata."""
import re
from urllib.parse import urlsplit


def activity_kind(value, error=ValueError):
    if value not in ("work", "personal"):
        raise error("Вид занятия: work или personal")
    return value


def default_activity(row):
    return row.get("activity_kind") or ("personal" if row.get("kind") in
        {"brainalot", "calendar", "calendar_optional"} else "work")


def validate_sources(sources, error=ValueError):
    if not isinstance(sources, list) or len(sources) > 100:
        raise error("Источники: нужен список до 100 записей")
    seen = set()
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {"id", "url", "text"}
                or not isinstance(source.get("id"), str) or not source["id"] or len(source["id"]) > 100
                or source["id"] in seen or not isinstance(source.get("text"), str)
                or len(source["text"]) > 1000 or not isinstance(source.get("url"), str)
                or len(source["url"]) > 2048 or not (source["text"].strip() or source["url"])):
            raise error("Неверный источник кубика")
        if source["url"]:
            try:
                parsed = urlsplit(source["url"])
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username or parsed.password or any(c.isspace() for c in source["url"])):
                    raise ValueError()
                parsed.port
            except ValueError as exc:
                raise error("Ссылка источника должна быть http(s)") from exc
        seen.add(source["id"])


def validate_note_activity(meta, error=ValueError):
    if meta.get("activity_kind") is not None:
        activity_kind(meta["activity_kind"], error)
    cube = meta.get("work_cube")
    if cube is None:
        return
    if meta.get("kind") != "task":
        raise error("Кубик Work доступен только для задачи")
    if (not isinstance(cube, dict) or not {"minutes", "progress_percent", "start"} <= set(cube)
            or set(cube)-{"minutes", "progress_percent", "start", "sources"}):
        raise error("Кубик Work: нужны minutes, progress_percent, start")
    if type(cube["minutes"]) is not int or not 5 <= cube["minutes"] <= 240 or cube["minutes"] % 5:
        raise error("Длительность кубика: 5–240 минут, шаг 5 минут")
    if type(cube["progress_percent"]) is not int or not 0 <= cube["progress_percent"] <= 100:
        raise error("Готовность кубика: целое число от 0 до 100")
    start = cube["start"]
    if start is not None:
        if not isinstance(start, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start):
            raise error("Требуется время ЧЧ:ММ")
        if not meta.get("due"):
            raise error("Для времени кубика требуется дата")
        if int(start[:2])*60+int(start[3:])+cube["minutes"] > 1440:
            raise error("Кубик должен завершиться в пределах дня")
    if 'sources' in cube:
        validate_sources(cube['sources'], error)
