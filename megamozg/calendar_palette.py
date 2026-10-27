"""Google Calendar event colours shared by the calendar reader and Work.

Google keeps the 11 classic ``colorId`` values for older clients and, since
2026, custom labels (``eventLabelId``) with any background colour. Both are
normalised to the lowercase hex the Calendar UI shows, so one owner choice
("banana → 3D printing") covers either representation.
"""
from __future__ import annotations

import re

HEX = re.compile(r"#[0-9a-f]{6}")
# Classic colorId → the colour of the current Calendar UI (the API's Colors
# endpoint still reports the older, paler palette for the same IDs).
CLASSIC = {"1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73", "5": "#f6bf26",
           "6": "#f4511e", "7": "#039be5", "8": "#616161", "9": "#3f51b5", "10": "#0b8043",
           "11": "#d50000"}
# The 24 standard event and label colours, ordered by hue as in a colour wheel.
STANDARD = (("#d50000", "Томат"), ("#e67c73", "Фламинго"), ("#ad1457", "Радиккио"),
            ("#d81b60", "Цветок вишни"), ("#f4511e", "Мандарин"), ("#ef6c00", "Тыква"),
            ("#f09300", "Манго"), ("#f6bf26", "Банан"), ("#e4c441", "Цитрон"),
            ("#c0ca33", "Авокадо"), ("#7cb342", "Фисташка"), ("#33b679", "Шалфей"),
            ("#0b8043", "Базилик"), ("#009688", "Эвкалипт"), ("#039be5", "Павлин"),
            ("#4285f4", "Кобальт"), ("#3f51b5", "Черника"), ("#7986cb", "Лаванда"),
            ("#b39ddb", "Глициния"), ("#9e69af", "Аметист"), ("#8e24aa", "Виноград"),
            ("#795548", "Какао"), ("#a79b8e", "Берёза"), ("#616161", "Графит"))
NAMES = dict(STANDARD)


def event_color(raw: dict) -> tuple[str, str | None] | None:
    """Return (hex, label name) of a Calendar API event; None means the calendar's own colour.

    ``labelColor``/``labelName`` are added by the Brainalot bridge script, which
    resolves ``eventLabelId`` against the calendar's labels.
    """
    label = raw.get("labelColor")
    if isinstance(label, str) and HEX.fullmatch(label.lower()):
        name = raw.get("labelName")
        return label.lower(), name.strip()[:80] if isinstance(name, str) and name.strip() else None
    legacy = raw.get("colorId")
    color = CLASSIC.get(legacy) if isinstance(legacy, str) else None
    return (color, None) if color else None
