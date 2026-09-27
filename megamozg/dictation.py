"""Deterministic parsing of dictated Russian and basic English captures.

The parser never calls a model or the network.  It turns a phrase such as
«поставь мне на сегодня вынести мусор с 15 до 16» into Brainalot fields: kind,
title, date or planning basket, importance, project context and, when an
exact time or a repeat is spoken, a calendar event.  Every recognised span is
reported, so the capture window can show what was understood and let the user
correct it before saving.
"""
from __future__ import annotations

import calendar as _calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_EVENT_MINUTES = 60
MAX_TITLE = 200


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


# --------------------------------------------------------------------------
# Tokens

_TOKEN_RE = re.compile(r"""
    (?P<url>(?:https?://|www\.)[^\s<>"«»]+)
  | (?P<tag>\#[^\W_][\w/-]*)
  | (?P<date>\d{1,2}[./]\d{1,2}[./](?:\d{4}|\d{2})(?![\d./]))
  | (?P<slash>\d{1,2}/\d{1,2}(?![\d/]))
  | (?P<clock>\d{1,2}:\d{2}(?!\d))
  | (?P<dotted>\d{1,2}\.\d{2}(?![\d.]))
  | (?P<num>\d+)(?:-?(?P<suffix>(?:ого|го|ому|му|ое|е|ом|м|ти|ми|х|й|я|ю)(?![^\W\d_])))?
  | (?P<word>[^\W\d_]+(?:-[^\W\d_]+)*)
  | (?P<punct>[^\w\s])
""", re.X)

_ORDINAL_SUFFIXES = {"ого", "го", "ому", "му", "ое", "е", "ом", "м", "й", "я", "ю"}


@dataclass
class _Token:
    kind: str
    text: str
    start: int
    end: int
    norm: str
    value: int | None = None
    ordinal: bool = False
    spelled: bool = False
    parts: tuple[int, ...] = ()


_UNITS = {
    "ноль": 0, "нуля": 0, "один": 1, "одна": 1, "одного": 1, "одной": 1, "одному": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2, "три": 3, "трех": 3, "трем": 3,
    "четыре": 4, "четырех": 4, "четырем": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6,
    "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
}
_TEENS = {
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12,
    "двенадцати": 12, "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14,
    "пятнадцать": 15, "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16,
    "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18, "восемнадцати": 18,
    "девятнадцать": 19, "девятнадцати": 19,
}
_TENS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30,
         "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50}
_ORDINAL_STEMS = {
    1: "перв", 2: "втор", 3: "трет", 4: "четверт", 5: "пят", 6: "шест", 7: "седьм", 8: "восьм",
    9: "девят", 10: "десят", 11: "одиннадцат", 12: "двенадцат", 13: "тринадцат",
    14: "четырнадцат", 15: "пятнадцат", 16: "шестнадцат", 17: "семнадцат",
    18: "восемнадцат", 19: "девятнадцат", 20: "двадцат", 30: "тридцат",
}
_ORDINALS: dict[str, int] = {}
for _value, _stem in _ORDINAL_STEMS.items():
    endings = ("ье", "ьего", "ьему", "ий", "ьем", "ья", "ью") if _value == 3 else (
        "ое", "ого", "ому", "ый", "ой", "ом", "ая", "ую")
    for _ending in endings:
        _ORDINALS[_stem + _ending] = _value
# The genitive forms that name the next hour: «половина пятого» is 4:30.
_HOUR_GENITIVE = {"первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5,
                  "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10,
                  "одиннадцатого": 11, "двенадцатого": 12}


def _number_word(tokens: list[_Token], i: int) -> tuple[int, bool, int] | None:
    """Return (value, ordinal, tokens used) for a spelled number at ``i``."""
    word = tokens[i].norm
    following = tokens[i + 1].norm if i + 1 < len(tokens) and tokens[i + 1].kind == "word" else None
    if word in _TENS:
        if following in _UNITS and _UNITS[following] > 0:
            return _TENS[word] + _UNITS[following], False, 2
        if following in _ORDINALS and _ORDINALS[following] < 10:
            return _TENS[word] + _ORDINALS[following], True, 2
        return _TENS[word], False, 1
    if word in _TEENS:
        return _TEENS[word], False, 1
    if word in _UNITS:
        return _UNITS[word], False, 1
    if word in _ORDINALS:
        return _ORDINALS[word], True, 1
    return None


def _tokenize(text: str) -> list[_Token]:
    raw: list[_Token] = []
    for match in _TOKEN_RE.finditer(text):
        value = match.group(0)
        if match.group("num") is not None:
            suffix = match.group("suffix")
            raw.append(_Token("num", value, match.start(), match.end(), _norm(value), int(match.group("num")),
                              ordinal=bool(suffix and suffix in _ORDINAL_SUFFIXES)))
            continue
        kind = match.lastgroup
        token = _Token(kind, value, match.start(), match.end(), _norm(value))
        if kind in {"clock", "dotted", "date", "slash"}:
            token.parts = tuple(int(part) for part in re.split(r"[:./]", value))
        if kind == "url":
            stripped = value.rstrip(".,;:!?)»")
            token.text, token.end, token.norm = stripped, match.start() + len(stripped), _norm(stripped)
        raw.append(token)
    merged: list[_Token] = []
    i = 0
    while i < len(raw):
        token = raw[i]
        if token.kind == "word":
            spelled = _number_word(raw, i)
            if spelled:
                value, ordinal, used = spelled
                last = raw[i + used - 1]
                merged.append(_Token("num", text[token.start:last.end], token.start, last.end,
                                     _norm(text[token.start:last.end]), value, ordinal, spelled=True))
                i += used
                continue
        merged.append(token)
        i += 1
    return merged


# --------------------------------------------------------------------------
# Lexicon

_MONTHS = {}
for _number, _forms in enumerate((
        ("январь", "января", "январе"), ("февраль", "февраля", "феврале"), ("март", "марта", "марте"),
        ("апрель", "апреля", "апреле"), ("май", "мая", "мае"), ("июнь", "июня", "июне"),
        ("июль", "июля", "июле"), ("август", "августа", "августе"),
        ("сентябрь", "сентября", "сентябре"), ("октябрь", "октября", "октябре"),
        ("ноябрь", "ноября", "ноябре"), ("декабрь", "декабря", "декабре")), start=1):
    for _form in _forms:
        _MONTHS[_form] = _number

_WEEKDAYS = {}
for _number, _forms in enumerate((
        ("понедельник", "понедельника", "понедельнику"),
        ("вторник", "вторника", "вторнику"),
        ("среда", "среду", "среды", "среде"),
        ("четверг", "четверга", "четвергу"),
        ("пятница", "пятницу", "пятницы", "пятнице"),
        ("суббота", "субботу", "субботы", "субботе"),
        ("воскресенье", "воскресенья", "воскресенью"))):
    for _form in _forms:
        _WEEKDAYS[_form] = _number
_WEEKDAYS_PLURAL = {"понедельникам": 0, "вторникам": 1, "средам": 2, "четвергам": 3,
                    "пятницам": 4, "субботам": 5, "воскресеньям": 6}
_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

_PARTS_AFTER_TIME = {"утра": "morning", "дня": "day", "вечера": "evening", "ночи": "night"}
_PART_ADVERBS = {"утром": "morning", "днем": "day", "вечером": "evening", "ночью": "night",
                 "поутру": "morning", "вечерком": "evening"}
_HOUR_UNITS = {"час", "часа", "часов", "ч"}
_MINUTE_UNITS = {"минута", "минуту", "минуты", "минут", "мин"}
_DAY_UNITS = {"день", "дня", "дней", "сутки", "суток"}
_WEEK_UNITS = {"неделю", "недели", "недель", "неделя"}
_MONTH_UNITS = {"месяц", "месяца", "месяцев"}
_YEAR_UNITS = {"год", "года", "лет"}

# Words after «в 3» that confirm a time of day rather than «в 3 банка».
_TIME_FOLLOWERS = {
    "с", "со", "у", "на", "в", "во", "по", "для", "и", "а", "но", "или", "к", "ко", "о", "об",
    "до", "после", "про", "за", "перед", "возле", "около", "чтобы", "что", "надо", "нужно",
    "сегодня", "завтра", "послезавтра", "мне", "нам", "будет", "уже", "обязательно",
    "срочно", "важно", "пожалуйста", "не", "ровно", "встреча", "встречу", "созвон", "звонок",
    "тренировка", "тренировку", "обед", "ужин", "завтрак", "прием", "собрание", "планерка",
    "урок", "занятие", "лекция", "вебинар", "репетиция", "массаж", "стрижка", "врач",
    "доктор", "зал", "бассейн", "совещание", "митинг", "стендап", "дедлайн", "поезд",
    "самолет", "автобус", "электричка", "кино", "концерт", "спектакль", "матч", "игра",
    "свидание", "вечеринка", "день", "запись", "интервью", "собеседование", "экзамен",
}
_NON_TIME_FOLLOWERS = _MINUTE_UNITS | {"секунд", "секунды", "раз", "раза", "процентов", "процента",
                                       "рублей", "рубля", "штук", "штуки", "человек", "дней", "дня",
                                       "недель", "недели", "лет", "года", "месяцев", "месяца"}

_IMPERATIVES = {
    "позвони", "позвоните", "напиши", "сделай", "отправь", "забери", "оплати", "проверь",
    "подготовь", "вынеси", "убери", "помой", "приготовь", "найди", "сходи", "съезди",
    "запишись", "закончи", "начни", "почини", "распечатай", "отнеси", "принеси", "погладь",
    "постирай", "полей", "покорми", "выгуляй", "узнай", "спроси", "ответь", "согласуй",
    "обнови", "установи", "удали", "перенеси", "собери", "возьми", "верни", "отдай",
    "передай", "зайди", "заедь", "поздравь", "отмени", "подпиши", "отвези", "привези",
    "встреть", "поговори", "договорись", "уточни", "почисти", "замени", "продли", "сдай",
    "получи", "запусти", "проведи", "посмотри", "прочитай", "послушай", "выучи", "повтори",
}
_NOT_VERBS = {"часть", "власть", "память", "сеть", "мать", "путь", "суть", "треть", "честь",
              "тетрадь", "площадь", "лошадь", "дети", "сети", "пути", "части", "гости", "кости",
              "ночь", "дочь", "речь", "смерть", "плоть", "нить", "грудь", "кровать", "печать",
              "скатерть", "сласти", "страсти"}

_PURCHASE_WORDS = {"купить", "купи", "куплю", "купим", "купите", "прикупить", "докупить",
                   "закупить", "закупиться", "заказать", "закажи", "закажу", "приобрести",
                   "покупка", "покупку", "покупки"}
_MEDIA_NOUNS = {
    "фильм": "movie", "фильмы": "movie", "фильма": "movie", "кино": "movie", "мультфильм": "movie",
    "мультик": "movie", "сериал": "series", "сериала": "series", "сериалы": "series",
    "аниме": "series", "книга": "book", "книгу": "book", "книги": "book", "книжку": "book",
    "книжка": "book", "аудиокнигу": "book", "аудиокнига": "book", "роман": "book",
    "игра": "game", "игру": "game", "подкаст": "podcast", "подкаста": "podcast",
    "подкасты": "podcast", "выпуск": "podcast", "статья": "article", "статью": "article",
    "статьи": "article", "видео": "article", "ролик": "article", "видос": "article",
    "лекцию": "article", "доклад": "article",
}
_MEDIA_NOMINATIVE = {"книгу": "книга", "книжку": "книжка", "аудиокнигу": "аудиокнига", "игру": "игра",
                     "статью": "статья", "лекцию": "лекция", "статьи": "статья", "фильма": "фильм",
                     "сериала": "сериал", "подкаста": "подкаст"}
_ATTRIBUTION = ("посоветовал", "посоветовала", "посоветовали", "советует", "советуют", "советовал",
                "советовала", "рекомендовал", "рекомендовала", "рекомендовали", "рекомендует", "говорят",
                "говорил", "говорила", "сказал", "сказала", "скинул", "скинула", "прислал", "прислала")
_CONSUME_VERBS = {"посмотреть", "глянуть", "смотреть", "пересмотреть", "досмотреть", "прочитать",
                  "прочесть", "почитать", "дочитать", "читать", "послушать", "слушать",
                  "дослушать", "поиграть", "пройти", "сыграть", "заценить", "посмотри",
                  "прочитай", "послушай", "глянь", "советую", "посоветовали", "посоветовал",
                  "посоветовала", "интересный", "интересная", "хороший", "хорошая", "классный"}
_LINK_FILLERS = {"потом", "позже", "позднее", "later", "ссылка", "ссылку", "это", "вот", "статью", "видео",
                 "сохранить", "почитать", "посмотреть", "глянуть", "на", "будущее", "когда", "будет", "время"}
_IDEA_PHRASES = ("а что если", "что если", "было бы круто", "было бы здорово", "было бы классно",
                 "было бы удобно", "можно было бы", "придумал", "придумала", "есть идея", "идея")
_THOUGHT_PHRASES = ("подумал", "подумала", "мысль", "понял что", "поняла что", "мне кажется",
                    "заметил что", "заметила что", "интересно что", "заметка")

_IMPORTANCE_PHRASES = (
    (("очень", "срочно"), "critical"), (("очень", "важно"), "critical"), (("очень", "важное"), "critical"),
    (("супер", "срочно"), "critical"), (("критически", "важно"), "critical"), (("критично",), "critical"),
    (("не", "срочно"), "low"), (("не", "важно"), "low"), (("несрочно",), "low"), (("неважно",), "low"),
    (("не", "горит"), "low"), (("без", "спешки"), "low"), (("когда", "будет", "время"), "low"),
    (("когда-нибудь",), "low"), (("срочно",), "high"), (("срочное",), "high"), (("важно",), "high"),
    (("важное",), "high"), (("приоритетно",), "high"), (("в", "приоритете"), "high"),
    (("с", "высоким", "приоритетом"), "high"),
)

_HORIZONS = (
    (("на", "этой", "неделе"), "week"), (("на", "неделе"), "week"), (("в", "течение", "недели"), "week"),
    (("до", "конца", "недели"), "week"), (("на", "эту", "неделю"), "week"), (("за", "неделю"), "week"),
    (("в", "этом", "месяце"), "month"), (("на", "этот", "месяц"), "month"), (("на", "месяц"), "month"),
    (("в", "течение", "месяца"), "month"), (("до", "конца", "месяца"), "month"),
    (("в", "конце", "месяца"), "month"),
    (("на", "следующей", "неделе"), "next_week"), (("на", "следующую", "неделю"), "next_week"),
    (("на", "будущей", "неделе"), "next_week"), (("в", "следующем", "месяце"), "next_month"),
    (("на", "выходных"), "weekend"), (("в", "выходные"), "weekend"), (("на", "выходные"), "weekend"),
    (("на", "этих", "выходных"), "weekend"), (("в", "эти", "выходные"), "weekend"),
    (("в", "конце", "недели"), "week_end"),
)

_PREFIXES = (
    (re.compile(r"(?:ну|так|итак|короче|слушай|значит|давай|пожалуйста|э+|эм+)\b[\s,.]*", re.I), None),
    (re.compile(r"(?:поставь|добавь|запиши|внеси|создай|заведи|закинь|кинь|сохрани|запланируй)\b"
                r"(?:\s+(?:мне|себе|нам|пожалуйста|еще|ещё))*[\s,:.]*", re.I), None),
    (re.compile(r"(?:в\s+)?(?:список\s+покупок|покупки)\b[\s,:.–—-]*", re.I), "purchase"),
    (re.compile(r"(?:в\s+)?(?:календарь|список\s+дел|список\s+задач|дела|задачи|планы?)\b[\s,:.–—-]*",
                re.I), "task"),
    (re.compile(r"(?:напомни|напомнить)\b(?:\s+(?:мне|нам|пожалуйста))*(?:\s+(?:о\s+том|про\s+то))?"
                r"[\s,]*(?:(?:что|чтобы)\b\s*)?", re.I), "task"),
    (re.compile(r"(?:мне\s+)?(?:надо|нужно|необходимо|следует|стоит)\b(?:\s+(?:будет|бы))*\s*", re.I), "task?"),
    (re.compile(r"(?:не\s+забыть|не\s+забудь)\b(?:\s+бы)?[\s,:]*", re.I), "task"),
    (re.compile(r"(?:(?:новое|новую|новая|одно)\s+)?(?:дело\b(?!\s+в\s+том)|задача\b|задачу\b|задание\b)"
                r"\s*(?:[:,.–—-]\s*)?", re.I), "task"),
    (re.compile(r"(?:(?:новая|новую)\s+)?(?:покупка|покупку)\b\s*(?:[:,.–—-]\s*)?", re.I), "purchase"),
    (re.compile(r"(?:(?:есть|новая|новую|такая)\s+)?(?:идея|идею)\b\s*(?:[:,.–—-]\s*)?"
                r"(?:(?:о\s+том,?\s+)?что\s+)?", re.I), "idea"),
    (re.compile(r"(?:(?:новая|новую|такая)\s+)?(?:мысль|заметка|заметку)\b\s*(?:[:,.–—-]\s*)?"
                r"(?:(?:о\s+том,?\s+)?что\s+)?", re.I), "thought"),
    (re.compile(r"(?:(?:новый|создай|создать|завести|заведи)\s+)?проект\b\s*(?:[:,.–—-]\s*)?", re.I),
     "project"),
)
_TARGETS = (
    (("в", "список", "покупок"), "purchase"), (("в", "покупки"), "purchase"), (("к", "покупкам"), "purchase"),
    (("в", "список", "дел"), "task"), (("в", "список", "задач"), "task"), (("в", "дела"), "task"),
    (("в", "задачи"), "task"), (("в", "календарь"), "task"), (("в", "мои", "дела"), "task"),
    (("в", "идеи"), "idea"), (("в", "мысли"), "thought"), (("в", "заметки"), "thought"),
)
_MORNING_HINTS = {"будильник", "будильника", "подъем", "проснуться", "встать", "разбудить", "разбуди", "завтрак"}
_PREPOSITIONS = {"в", "во", "на", "с", "со", "к", "ко", "до", "по", "у", "о", "об"}
_ACCUSATIVE_EVENTS = {
    "встречу": "встреча", "пробежку": "пробежка", "тренировку": "тренировка", "уборку": "уборка",
    "стрижку": "стрижка", "прогулку": "прогулка", "презентацию": "презентация", "консультацию": "консультация",
    "планерку": "планерка", "лекцию": "лекция", "репетицию": "репетиция", "экскурсию": "экскурсия",
    "поездку": "поездка", "вечеринку": "вечеринка", "свадьбу": "свадьба", "игру": "игра", "поездочку": "поездка",
    "процедуру": "процедура", "операцию": "операция", "примерку": "примерка", "доставку": "доставка",
    "бронь": "бронь", "запись": "запись", "созвоны": "созвоны", "зарядку": "зарядка", "йогу": "йога",
}
_DANGLING = {"и", "а", "но", "или", "в", "во", "на", "с", "со", "к", "ко", "до", "по", "у", "о", "об",
             "что", "чтобы", "мне", "нам", "пожалуйста", "спасибо", "это", "бы", "же", "ну", "там"}


def _looks_like_verb(word: str | None) -> bool:
    if not word:
        return False
    if word in _IMPERATIVES:
        return True
    if word in _NOT_VERBS or len(word) < 4 or word.endswith(("ость", "ости", "асти")):
        return False
    return bool(re.search(r"(?:ть|ти|чь|ться|тись|чься)$", word))


def _stem(word: str) -> str:
    word = _norm(word)
    for suffix in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ой", "ей", "ом", "ем", "ах",
                   "ях", "ам", "ям", "ую", "юю", "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ов",
                   "ев", "а", "я", "у", "ю", "е", "ы", "и", "о"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, _calendar.monthrange(year, month)[1]))


@dataclass
class _Time:
    hour: int
    minute: int
    explicit: bool
    part: str | None
    used: int
    strong: bool
    midnight: bool = False


@dataclass
class _Context:
    id: str
    title: str
    kind: str
    stems: tuple[str, ...]


def _contexts(values: Iterable[Mapping[str, Any]]) -> list[_Context]:
    result = []
    for value in values:
        if not isinstance(value, Mapping) or value.get("kind") not in {"project", "person"}:
            continue
        if value.get("status") in {"archived", "cancelled", "done"}:
            continue
        title, identifier = value.get("title"), value.get("id")
        if not isinstance(title, str) or not isinstance(identifier, str):
            continue
        stems = tuple(_stem(word) for word in re.findall(r"[^\W\d_]+", title))
        if stems:
            result.append(_Context(identifier, title, value["kind"], stems))
    return result


# --------------------------------------------------------------------------
# Parser

class _Parser:
    def __init__(self, text: str, now: datetime, zone: ZoneInfo, contexts: list[_Context],
                 event_minutes: int):
        self.text = text
        self.zone = zone
        self.now = now.astimezone(zone).replace(second=0, microsecond=0)
        self.today = self.now.date()
        self.contexts = contexts
        self.event_minutes = event_minutes
        self.tokens = _tokenize(text)
        self.marks: list[str | None] = [None] * len(self.tokens)
        self.spans: list[dict] = []
        self.warnings: list[str] = []
        self.date: date | None = None
        self.date_source: str | None = None
        self.weekday: int | None = None
        self.horizon: str | None = None
        self.start: _Time | None = None
        self.end: _Time | None = None
        self.deadline: _Time | None = None
        self.offset: datetime | None = None
        self.duration: timedelta | None = None
        self.part: str | None = None
        self.recurrence: dict | None = None
        self.importance: str | None = None
        self.url: str | None = None
        self.tags: list[str] = []
        self.context: _Context | None = None
        self.label: str | None = None

    # -- token helpers ------------------------------------------------------
    def tok(self, i: int) -> _Token | None:
        return self.tokens[i] if 0 <= i < len(self.tokens) else None

    def word(self, i: int) -> str | None:
        token = self.tok(i)
        return token.norm if token and token.kind == "word" else None

    def number(self, i: int, *, ordinal: bool | None = False) -> int | None:
        token = self.tok(i)
        if not token or token.kind != "num":
            return None
        if ordinal is not None and token.ordinal != ordinal:
            return None
        return token.value

    def words_at(self, i: int, sequence: tuple[str, ...]) -> bool:
        return all(self.word(i + k) == part for k, part in enumerate(sequence))

    def take(self, i: int, count: int, field: str) -> int:
        for k in range(i, i + count):
            self.marks[k] = field
        first, last = self.tokens[i], self.tokens[i + count - 1]
        self.spans.append({"field": field, "text": self.text[first.start:last.end],
                           "start": first.start, "end": last.end})
        return count

    def release(self, field: str) -> None:
        for k, mark in enumerate(self.marks):
            if mark == field:
                self.marks[k] = None
        self.spans = [span for span in self.spans if span["field"] != field]

    def first_content(self) -> int:
        for k, token in enumerate(self.tokens):
            if token.kind != "punct":
                return k
        return 0

    # -- facts --------------------------------------------------------------
    def set_date(self, day: date, source: str) -> None:
        if self.date is None:
            self.date, self.date_source = day, source
        elif source == "next_week" and self.date_source == "weekday" and self.weekday is not None:
            # «в среду на следующей неделе»: the weekday moves into next week.
            monday = self.today - timedelta(days=self.today.weekday()) + timedelta(days=7)
            self.date = monday + timedelta(days=self.weekday)
        elif self.date != day:
            self.warnings.append("В диктовке несколько дат; использована первая")

    # -- time values --------------------------------------------------------
    def time_value(self, j: int) -> _Time | None:
        token = self.tok(j)
        if token is None:
            return None
        word = self.word(j)
        result: _Time | None = None
        if token.kind in {"clock", "dotted"} and len(token.parts) == 2:
            hour, minute = token.parts
            if hour <= 24 and minute <= 59:
                explicit = hour >= 13 or hour == 0 or token.text.startswith("0")
                result = _Time(hour % 24, minute, explicit, None, 1, True)
        elif token.kind == "num" and not token.ordinal and token.value is not None and token.value <= 24:
            hour, used, strong, minute = token.value, 1, False, 0
            if self.word(j + used) in _HOUR_UNITS:
                used, strong = used + 1, True
            following = self.tok(j + used)
            if (following and following.kind == "num" and not following.ordinal and following.value is not None
                    and following.value <= 59 and (following.spelled or len(following.text) == 2)
                    and not (self.word(j + used + 1) in _HOUR_UNITS)):
                minute, used, strong = following.value, used + 1, True
                if following.value == 0 and self.number(j + used) == 0:
                    used += 1
                if self.word(j + used) in _MINUTE_UNITS:
                    used += 1
            result = _Time(hour % 24, minute, hour >= 13 or hour == 0 and token.value == 0 and strong,
                           None, used, strong)
        elif word == "час":
            result = _Time(1, 0, False, None, 1, False)
        elif word == "полдень":
            result = _Time(12, 0, True, None, 1, True)
        elif word == "полночь":
            result = _Time(0, 0, True, None, 1, True, midnight=True)
        elif word in {"половине", "половина", "пол"} and self.hour_genitive(j + 1):
            result = _Time(self.hour_genitive(j + 1) - 1, 30, False, None, 2, True)
        elif word and word.startswith("пол") and word[3:].lstrip("-") in _HOUR_GENITIVE:
            result = _Time(_HOUR_GENITIVE[word[3:].lstrip("-")] - 1, 30, False, None, 1, True)
        elif word == "четверть" and self.hour_genitive(j + 1):
            result = _Time(self.hour_genitive(j + 1) - 1, 15, False, None, 2, True)
        elif word == "без":
            if self.word(j + 1) == "четверти":
                before, used = 15, 2
            else:
                before, used = self.number(j + 1), 2
                if self.word(j + 2) in _MINUTE_UNITS:
                    used += 1
            hour = self.number(j + used)
            if before is not None and 0 < before < 60 and hour is not None and 1 <= hour <= 12:
                result = _Time((hour - 1) % 24, 60 - before, False, None, used + 1, True)
        if result is None:
            return None
        part = self.word(j + result.used)
        if part in _PARTS_AFTER_TIME:
            result.part, result.used, result.strong = _PARTS_AFTER_TIME[part], result.used + 1, True
        return result

    def hour_genitive(self, k: int) -> int | None:
        token = self.tok(k)
        return _HOUR_GENITIVE.get(token.norm) if token and token.kind in {"word", "num"} else None

    def time_follow_ok(self, k: int) -> bool:
        token = self.tok(k)
        if token is None or token.kind in {"punct", "url", "tag"}:
            return True
        if token.kind != "word":
            return False
        if token.norm in _NON_TIME_FOLLOWERS:
            return False
        return (token.norm in _TIME_FOLLOWERS or token.norm in _WEEKDAYS or token.norm in _PART_ADVERBS
                or _looks_like_verb(token.norm))

    def temporal_context_before(self, i: int) -> bool:
        return any(mark in {"date", "horizon", "part", "recurrence", "time"} for mark in self.marks[max(0, i - 3):i])

    def resolve(self, value: _Time, fallback_part: str | None = None) -> tuple[int, int]:
        hour, part = value.hour, value.part or fallback_part
        if part == "morning":
            hour = 0 if hour == 12 else hour
        elif part == "day":
            hour = hour + 12 if 1 <= hour <= 6 else hour
        elif part == "evening":
            hour = hour + 12 if 1 <= hour <= 11 else (0 if hour == 12 else hour)
        elif part == "night":
            hour = 0 if hour == 12 else (hour + 12 if 9 <= hour <= 11 else hour)
        elif not value.explicit and 1 <= hour <= 7:
            hour += 12
        return hour % 24, value.minute

    # -- matchers -----------------------------------------------------------
    def m_url_tag(self, i: int) -> int:
        token = self.tokens[i]
        if token.kind == "url":
            if self.url is None:
                self.url = token.text if token.text.startswith("http") else "https://" + token.text
            return self.take(i, 1, "url")
        if token.kind == "tag":
            tag = token.text[1:]
            if re.fullmatch(r"[\w/-]{1,64}", tag) and tag not in self.tags:
                self.tags.append(tag)
            return self.take(i, 1, "tag")
        word = self.word(i)
        offset = 1 if word == "с" and self.word(i + 1) == "тегом" else 0
        if self.word(i + offset) in {"тег", "тегом"} and self.word(i + offset + 1):
            tag = self.tokens[i + offset + 1].text
            if re.fullmatch(r"[\w/-]{1,64}", tag) and tag not in self.tags:
                self.tags.append(tag)
            return self.take(i, offset + 2, "tag")
        return 0

    def weekday_chain(self, j: int, plural: bool) -> tuple[list[int], int]:
        table = _WEEKDAYS_PLURAL if plural else _WEEKDAYS
        days, used = [], 0
        while True:
            word = self.word(j + used)
            if word in table:
                days.append(table[word])
                used += 1
            elif word in {"и", "по"} or (self.tok(j + used) and self.tok(j + used).text == ","):
                if self.word(j + used + 1) in table or (
                        self.word(j + used + 1) == "по" and self.word(j + used + 2) in table):
                    used += 1
                    continue
                break
            else:
                break
        return days, used

    def set_recurrence(self, i: int, count: int, freq: str, interval: int = 1,
                       byday: list[int] | None = None, part: str | None = None) -> int:
        if self.recurrence is None:
            self.recurrence = {"freq": freq, "interval": interval, "byday": sorted(set(byday or []))}
        elif byday and not self.recurrence["byday"] and self.recurrence["freq"] in {"WEEKLY", "DAILY"}:
            self.recurrence.update(freq="WEEKLY", byday=sorted(set(byday)))
        if part and self.part is None:
            self.part = part
        return self.take(i, count, "recurrence")

    def m_recurrence(self, i: int) -> int:
        word = self.word(i)
        if word in {"каждый", "каждую", "каждое", "каждые", "каждого"}:
            j, interval = i + 1, 1
            value = self.number(j)
            if value is not None and 1 <= value <= 30:
                interval, j = value, j + 1
            unit = self.word(j)
            if unit in _DAY_UNITS:
                return self.set_recurrence(i, j - i + 1, "DAILY", interval)
            if unit in {"утро", "вечер", "ночь"}:
                part = {"утро": "morning", "вечер": "evening", "ночь": "night"}[unit]
                return self.set_recurrence(i, j - i + 1, "DAILY", interval, part=part)
            if unit in _WEEK_UNITS:
                return self.set_recurrence(i, j - i + 1, "WEEKLY", interval)
            if unit in _MONTH_UNITS:
                return self.set_recurrence(i, j - i + 1, "MONTHLY", interval)
            if unit in _YEAR_UNITS:
                return self.set_recurrence(i, j - i + 1, "YEARLY", interval)
            if unit == "выходные":
                return self.set_recurrence(i, j - i + 1, "WEEKLY", interval, [5, 6])
            if unit in {"будний", "будние"} and self.word(j + 1) in {"день", "дни"}:
                return self.set_recurrence(i, j - i + 2, "WEEKLY", interval, [0, 1, 2, 3, 4])
            order = {"первый": 1, "первую": 1, "второй": 2, "вторую": 2, "третий": 3, "третью": 3,
                     "четвертый": 4, "четвертую": 4, "последний": -1, "последнюю": -1}.get(self.word(i + 1) or "")
            if order is None:
                token = self.tok(i + 1)
                order = token.value if token and token.kind == "num" and token.ordinal and token.value in {1, 2, 3, 4} else None
            if order is not None and self.word(i + 2) in _WEEKDAYS and self.word(i + 3) in {"месяца", "месяц"}:
                weekday = _WEEKDAYS[self.word(i + 2)]
                if self.recurrence is None:
                    self.recurrence = {"freq": "MONTHLY", "interval": 1, "byday": [weekday], "nth": order}
                return self.take(i, 4, "recurrence")
            days, used = self.weekday_chain(j, plural=False)
            if days:
                return self.set_recurrence(i, j - i + used, "WEEKLY", interval, days)
            return 0
        if word == "по":
            if self.word(i + 1) in {"будням"}:
                return self.set_recurrence(i, 2, "WEEKLY", 1, [0, 1, 2, 3, 4])
            if self.word(i + 1) == "будним" and self.word(i + 2) == "дням":
                return self.set_recurrence(i, 3, "WEEKLY", 1, [0, 1, 2, 3, 4])
            if self.word(i + 1) == "выходным":
                return self.set_recurrence(i, 2, "WEEKLY", 1, [5, 6])
            days, used = self.weekday_chain(i + 1, plural=True)
            if days:
                return self.set_recurrence(i, used + 1, "WEEKLY", 1, days)
            return 0
        simple = {"ежедневно": "DAILY", "еженедельно": "WEEKLY", "ежемесячно": "MONTHLY", "ежегодно": "YEARLY"}
        if word in simple:
            return self.set_recurrence(i, 1, simple[word])
        if word == "раз" and self.word(i + 1) == "в":
            unit = self.word(i + 2)
            freq = ("DAILY" if unit == "день" else "WEEKLY" if unit == "неделю" else
                    "MONTHLY" if unit == "месяц" else "YEARLY" if unit == "год" else None)
            if freq:
                return self.set_recurrence(i, 3, freq)
        return 0

    def m_offset(self, i: int) -> int:
        if self.word(i) != "через":
            return 0
        j, amount = i + 1, 1
        value = self.number(j)
        if value is not None:
            amount, j = value, j + 1
        elif self.word(j) in {"пару", "пара"}:
            value, amount, j = 2, 2, j + 1
        unit = self.word(j)
        minutes = None
        if unit == "полчаса" and value is None:
            minutes = 30
        elif unit == "полтора" and self.word(j + 1) in {"часа", "час"} and value is None:
            minutes, j = 90, j + 1
        elif unit in _MINUTE_UNITS:
            minutes = amount
        elif unit in _HOUR_UNITS - {"ч"}:
            minutes = amount * 60
            extra = self.number(j + 1)
            if extra is not None and self.word(j + 2) in _MINUTE_UNITS:
                minutes, j = minutes + extra, j + 2
        if minutes is not None:
            if 0 < minutes <= 24 * 60:
                self.offset = self.now + timedelta(minutes=minutes)
                return self.take(i, j - i + 1, "time")
            return 0
        if unit in _DAY_UNITS:
            day = self.today + timedelta(days=amount)
        elif unit in _WEEK_UNITS:
            day = self.today + timedelta(days=7 * amount)
        elif unit in _MONTH_UNITS:
            day = _add_months(self.today, amount)
        elif unit in _YEAR_UNITS:
            day = _add_months(self.today, 12 * amount)
        else:
            return 0
        if amount < 1 or amount > 3660:
            return 0
        self.set_date(day, "offset")
        return self.take(i, j - i + 1, "date")

    def month_after(self, k: int) -> bool:
        return self.word(k) in _MONTHS

    def m_range(self, i: int) -> int:
        word = self.word(i)
        token = self.tokens[i]
        if word in {"с", "со", "от", "в", "во"}:
            first = self.time_value(i + 1)
            if first is None:
                return 0
            j = i + 1 + first.used
            separator = self.word(j) or (self.tok(j).text if self.tok(j) and self.tok(j).kind == "punct" else None)
            if separator not in ({"до", "по", "-", "–", "—"} if word not in {"в", "во"} else {"до", "-", "–", "—"}):
                return 0
            second = self.time_value(j + 1)
            if second is None:
                return 0
            k = j + 1 + second.used
            if self.month_after(k) or self.month_after(i + 2) or self.word(k) in _NON_TIME_FOLLOWERS:
                return 0
            if not (first.strong or second.strong or first.explicit or second.explicit or self.time_follow_ok(k)
                    or i == self.first_content() or self.temporal_context_before(i)):
                return 0
            self.start, self.end = first, second
            return self.take(i, k - i, "time")
        if token.kind in {"clock"} and self.tok(i + 1) and self.tok(i + 1).text in {"-", "–", "—"}:
            first, second = self.time_value(i), self.time_value(i + 2)
            if first and second and self.tok(i + 2).kind == "clock":
                self.start, self.end = first, second
                return self.take(i, 2 + second.used, "time")
        return 0

    def m_explicit_date(self, i: int) -> int:
        word = self.word(i)
        j, prep = i, None
        if word in {"на", "до", "к", "ко", "с", "со", "по", "в"} and self.tok(i + 1) and self.tok(i + 1).kind in {
                "num", "date", "slash", "dotted"}:
            prep, j = word, i + 1
        token = self.tok(j)
        if token is None:
            return 0
        day = month = year = None
        used = 1
        if token.kind == "date":
            day, month, year = token.parts
            year = year + 2000 if year < 100 else year
        elif token.kind == "slash":
            day, month = token.parts
        elif token.kind == "dotted":
            first, second = token.parts
            if prep in {"в", "во", "с", "со", "к", "ко", "до"} and first <= 23 and second <= 59 and not self.month_after(j + 1):
                previous_time = self.word(j - 1) in {"в", "во", "с", "со", "к", "ко"} or (
                    prep == "до" and self.start is not None)
                if previous_time:
                    return 0
            if prep is None and self.word(i - 1) in {"в", "во", "с", "со", "к", "ко", "около", "до"} and first <= 23 and second <= 59:
                return 0
            day, month = first, second
        elif token.kind == "num" and token.value is not None and 1 <= token.value <= 31:
            if self.month_after(j + 1):
                day, month, used = token.value, _MONTHS[self.word(j + 1)], 2
                year_token = self.tok(j + 2)
                if year_token and year_token.kind == "num" and year_token.value and 2000 <= year_token.value <= 2100:
                    year, used = year_token.value, 3
                    if self.word(j + 3) in {"года", "год", "г"}:
                        used = 4
            elif token.ordinal or self.word(j + 1) == "числа":
                if prep in {"в", "во"} and not self.word(j + 1) == "числа":
                    return 0
                if token.spelled and self.word(j + 1) != "числа" and (
                        prep is None or not re.search(r"(?:ое|ого|ье|ьего|ому|ьему)$", token.norm)):
                    return 0
                day = token.value
                used = 2 if self.word(j + 1) == "числа" else 1
                month = self.today.month
                candidate_year = self.today.year
                if day < self.today.day:
                    month = month % 12 + 1
                    candidate_year += 1 if month == 1 else 0
                year = candidate_year
            else:
                return 0
        else:
            return 0
        if prep in {"в", "во"} and token.kind != "num":
            return 0
        if prep == "в" and token.kind == "num" and not self.month_after(j + 1):
            return 0
        try:
            if year is None:
                result = date(self.today.year, month, day)
                if result < self.today:
                    result = date(self.today.year + 1, month, day)
            else:
                result = date(year, month, day)
        except (TypeError, ValueError):
            self.warnings.append("Такой даты нет: " + self.text[token.start:self.tokens[j + used - 1].end])
            return 0
        if result < self.today:
            self.warnings.append("Дата в прошлом")
        self.set_date(result, "explicit")
        return self.take(i, j - i + used, "date")

    def m_relative_day(self, i: int) -> int:
        j, word = i, self.word(i)
        if word in {"на", "до", "к", "ко", "с", "со"} and self.word(i + 1) in {"сегодня", "завтра", "послезавтра"}:
            j, word = i + 1, self.word(i + 1)
        if word == "сегодня" or (word == "сегодняшний" and self.word(j + 1) == "день"):
            day, used = self.today, 1 if word == "сегодня" else 2
        elif word == "завтра":
            day, used = self.today + timedelta(days=1), 1
        elif word == "послезавтра":
            day, used = self.today + timedelta(days=2), 1
        elif word == "после" and self.word(j + 1) == "завтра":
            day, used = self.today + timedelta(days=2), 2
        elif word in {"этим", "этой"} and self.word(j + 1) in {"утром", "вечером", "ночью"}:
            day, used = self.today, 1
        else:
            return 0
        self.set_date(day, "relative")
        return self.take(i, j - i + used, "date")

    def m_horizon(self, i: int) -> int:
        for sequence, meaning in _HORIZONS:
            if not self.words_at(i, sequence):
                continue
            count = len(sequence)
            if meaning in {"week", "month"}:
                if self.horizon is None:
                    self.horizon = meaning
                return self.take(i, count, "horizon")
            monday = self.today - timedelta(days=self.today.weekday())
            if meaning == "next_week":
                self.set_date(monday + timedelta(days=7), "next_week")
            elif meaning == "next_month":
                self.set_date(_add_months(self.today.replace(day=1), 1), "horizon")
            elif meaning == "weekend":
                saturday = monday + timedelta(days=5)
                self.set_date(self.today if self.today.weekday() >= 5 else saturday, "horizon")
            elif meaning == "week_end":
                friday = monday + timedelta(days=4)
                self.set_date(max(friday, self.today), "horizon")
            return self.take(i, count, "date")
        return 0

    def m_weekday(self, i: int) -> int:
        j, prep, modifier = i, None, None
        if self.word(j) in {"в", "во", "на", "до", "к", "ко"}:
            prep, j = self.word(j), j + 1
        if self.word(j) in {"этот", "эту", "это", "эта"}:
            modifier, j = "this", j + 1
        elif self.word(j) in {"следующий", "следующую", "следующее", "следующая", "будущий", "будущую",
                              "будущее"}:
            modifier, j = "next", j + 1
        elif self.word(j) in {"ближайший", "ближайшую", "ближайшее"}:
            modifier, j = "nearest", j + 1
        weekday = _WEEKDAYS.get(self.word(j) or "")
        if weekday is None:
            return 0
        if prep == "на" and modifier is None and self.word(j) not in {"понедельник", "вторник", "среду", "четверг",
                                                                      "пятницу", "субботу", "воскресенье"}:
            return 0
        delta = (weekday - self.today.weekday()) % 7
        monday = self.today - timedelta(days=self.today.weekday())
        if modifier == "next":
            day = monday + timedelta(days=7 + weekday)
        elif modifier in {"this", "nearest"} or prep in {"до", "к", "ко"}:
            day = self.today + timedelta(days=delta)
        else:
            day = self.today + timedelta(days=delta or 7)
        self.weekday = weekday
        if self.date_source == "next_week":
            self.date = monday + timedelta(days=7 + weekday)
            self.date_source = "weekday"
        else:
            self.set_date(day, "weekday")
        return self.take(i, j - i + 1, "date")

    def m_time(self, i: int) -> int:
        word = self.word(i)
        token = self.tokens[i]
        if word in {"без", "четверть"} or (word and word.startswith("пол") and word[3:].lstrip("-") in _HOUR_GENITIVE):
            value = self.time_value(i)
            if value is not None and self.start is None:
                self.start = value
                return self.take(i, value.used, "time")
            return 0
        if word == "на":
            value = self.time_value(i + 1)
            token = self.tok(i + 1)
            if (value is None or self.start is not None or token is None
                    or not (token.kind == "clock" or value.part is not None or token.norm in {"полдень", "полночь"})):
                return 0
            if self.month_after(i + 1 + value.used) or self.word(i + 1 + value.used) in _NON_TIME_FOLLOWERS:
                return 0
            self.start = value
            return self.take(i, 1 + value.used, "time")
        if word in {"в", "во", "к", "ко", "около", "примерно", "ровно", "до", "после", "часов", "часиков"}:
            j = i + 1
            if word in {"примерно", "ровно"} and self.word(j) in {"в", "во", "к"}:
                j += 1
            value = self.time_value(j)
            if value is None:
                return 0
            k = j + value.used
            if word in {"часов", "часиков"} and not value.strong:
                return 0
            if self.month_after(k) or self.word(k) in _NON_TIME_FOLLOWERS:
                return 0
            if word in {"до", "после"} and not value.strong:
                return 0
            if not value.strong and not (self.time_follow_ok(k) or i == self.first_content()
                                         or self.temporal_context_before(i)):
                return 0
            if word == "час" and not value.strong:
                return 0
            if word in {"к", "ко", "до"} and self.start is None:
                self.deadline = value
            elif self.start is None:
                self.start = value
            elif self.end is None and word == "до":
                self.end = value
            else:
                self.warnings.append("В диктовке несколько времён; использовано первое")
            return self.take(i, k - i, "time")
        if token.kind == "clock" or (token.kind == "dotted" and token.parts[0] <= 23 and token.parts[1] <= 59
                                     and self.word(i - 1) not in {"на", "до", "к", "по"}
                                     and not self.month_after(i + 1)):
            if token.kind == "dotted" and not (token.parts[1] > 12 or token.parts[0] > 31):
                return 0
            value = self.time_value(i)
            if value is None:
                return 0
            if self.start is None:
                self.start = value
            return self.take(i, value.used, "time")
        return 0

    def m_duration(self, i: int) -> int:
        if self.word(i) not in {"на", "в", "длительностью"}:
            return 0
        if self.word(i) == "в" and self.word(i + 1) != "течение":
            return 0
        j = i + 1 if self.word(i) != "в" else i + 2
        minutes, used = None, 0
        unit = self.word(j)
        if unit == "полчаса":
            minutes, used = 30, 1
        elif unit == "полтора" and self.word(j + 1) in {"часа", "час"}:
            minutes, used = 90, 2
        elif unit == "час" or (unit == "часик"):
            minutes, used = 60, 1
        else:
            value = self.number(j)
            if value is not None and self.word(j + 1) in _HOUR_UNITS:
                minutes, used = value * 60, 2
                extra = self.number(j + 2)
                if extra is not None and self.word(j + 3) in _MINUTE_UNITS:
                    minutes, used = minutes + extra, 4
            elif value is not None and self.word(j + 1) in _MINUTE_UNITS:
                minutes, used = value, 2
        if not minutes or minutes > 24 * 60:
            return 0
        if self.duration is None:
            self.duration = timedelta(minutes=minutes)
        return self.take(i, j - i + used, "duration")

    def m_part_of_day(self, i: int) -> int:
        word = self.word(i)
        if word == "днем" and (self.word(i - 1) == "с" or self.word(i + 1) in {"рождения", "варенья", "свадьбы"}):
            return 0
        if word in _PART_ADVERBS:
            self.part = self.part or _PART_ADVERBS[word]
            count = 1
            if self.word(i - 1) in {"рано", "поздно"} and self.marks[i - 1] is None:
                return self.take(i - 1, 2, "part") - 1
            return self.take(i, count, "part")
        if word == "с" and self.word(i + 1) == "утра":
            self.part = self.part or "morning"
            return self.take(i, 2, "part")
        if word in {"в", "во"} and self.word(i + 1) == "обед":
            self.part = self.part or "day"
            if self.start is None:
                self.start = _Time(13, 0, True, "day", 0, True)
            return self.take(i, 2, "time")
        if (word, self.word(i + 1)) in {("до", "обеда"), ("к", "обеду")}:
            if self.deadline is None and self.start is None:
                self.deadline = _Time(13, 0, True, "day", 0, True)
            return self.take(i, 2, "time")
        if word == "после" and self.word(i + 1) == "обеда":
            self.part = self.part or "day"
            return self.take(i, 2, "part")
        return 0

    def m_target(self, i: int) -> int:
        for sequence, label in _TARGETS:
            if self.words_at(i, sequence):
                self.label = self.label or label
                return self.take(i, len(sequence), "target")
        return 0

    def m_importance(self, i: int) -> int:
        for sequence, level in _IMPORTANCE_PHRASES:
            if self.words_at(i, sequence):
                if self.importance is None:
                    self.importance = level
                return self.take(i, len(sequence), "importance")
        return 0

    def match_context_title(self, j: int, *, kinds: set[str]) -> tuple[_Context, int] | None:
        best: tuple[_Context, int] | None = None
        for context in self.contexts:
            if context.kind not in kinds:
                continue
            for stems in ((context.stems, context.stems[:1]) if context.kind == "person" else (context.stems,)):
                size = len(stems)
                words = [self.word(j + k) for k in range(size)]
                if None in words:
                    continue
                if all(_stem(word) == stem for word, stem in zip(words, stems)):
                    if best is None or size > best[1]:
                        best = (context, size)
                    break
        return best

    def m_context(self, i: int) -> int:
        word, following = self.word(i), self.word(i + 1)
        markers = {("по", "проекту"), ("для", "проекта"), ("в", "проект"), ("в", "проекте"), ("к", "проекту"),
                   ("из", "проекта"), ("по", "проекта")}
        if (word, following) in markers:
            found = self.match_context_title(i + 2, kinds={"project"})
            if found:
                self.context = self.context or found[0]
                return self.take(i, 2 + found[1], "context")
            return 0
        if word == "проект" and i == self.first_content():
            found = self.match_context_title(i + 1, kinds={"project"})
            if found:
                after = self.tok(i + 1 + found[1])
                if after and after.text in {":", ",", "-", "–", "—"}:
                    self.context = self.context or found[0]
                    return self.take(i, 2 + found[1], "context")
            return 0
        if word in {"для", "по"}:
            found = self.match_context_title(i + 1, kinds={"project"})
            if found and (len(found[0].stems) > 1 or len(found[0].title) >= 6):
                self.context = self.context or found[0]
                return self.take(i, 1 + found[1], "context")
            return 0
        if i == self.first_content():
            found = self.match_context_title(i, kinds={"project"})
            if found:
                after = self.tok(i + found[1])
                if after and after.text in {":", "-", "–", "—"}:
                    self.context = self.context or found[0]
                    return self.take(i, found[1] + 1, "context")
        return 0

    # -- run ----------------------------------------------------------------
    def run(self) -> dict:
        matchers = (self.m_url_tag, self.m_recurrence, self.m_offset, self.m_horizon, self.m_range,
                    self.m_explicit_date, self.m_relative_day, self.m_weekday, self.m_time,
                    self.m_duration, self.m_part_of_day, self.m_target, self.m_importance, self.m_context)
        i = 0
        while i < len(self.tokens):
            if self.marks[i] is not None:
                i += 1
                continue
            for matcher in matchers:
                used = matcher(i)
                if used:
                    i += used
                    break
            else:
                i += 1
        if self.part is None and self.start is not None and self.start.part is None and any(
                token.norm in _MORNING_HINTS for token in self.tokens):
            self.part = "morning"
        if self.duration is not None and self.start is None and self.offset is None:
            self.release("duration")
            self.duration = None
        if self.context is None:
            self.find_person()
        return self.build()

    def find_person(self) -> None:
        found = []
        for i, token in enumerate(self.tokens):
            if token.kind != "word" or self.marks[i] is not None:
                continue
            match = self.match_context_title(i, kinds={"person"})
            if match and match[0] not in found and len(match[0].stems[0]) >= 3:
                found.append(match[0])
        if len(found) == 1:
            self.context = found[0]
        elif len(found) > 1:
            self.warnings.append("Упомянуто несколько людей; контекст не выбран")

    # -- title and kind -----------------------------------------------------
    def remaining_text(self) -> str:
        marks = list(self.marks)
        changed = True
        while changed:
            changed = False
            kept = [k for k, mark in enumerate(marks) if mark is None and self.tokens[k].kind != "punct"]
            for position, k in ((0, kept[0]), (1, kept[-1])) if len(kept) > 1 else ():
                norm = self.tokens[k].norm
                recognised = [n for n in (k - 1, k + 1) if 0 <= n < len(marks) and marks[n] not in {None, "filler"}]
                if not recognised or norm not in _DANGLING:
                    continue
                if norm in _PREPOSITIONS and position == 0 and (k + 1) not in recognised:
                    continue
                marks[k], changed = "filler", True
        chars = list(self.text)
        for token, mark in zip(self.tokens, marks):
            if mark is not None:
                for k in range(token.start, token.end):
                    chars[k] = "\x00"
        remaining = re.sub("\x00+", " ", "".join(chars))
        return _tidy(remaining)

    def build(self) -> dict:
        remaining = self.remaining_text()
        stripped, label = _strip_prefixes(remaining)
        if self.label and label in {None, "task?"}:
            label = self.label
        kind, media_type, confident = self.classify(stripped, label)
        title = _title_for(kind, stripped, media_type, self.url)
        if not title:
            title = _capitalize(_tidy(self.text)) or "Запись"
        event = self.event(title)
        warnings = list(self.warnings)
        due = None
        horizon = None
        if event is not None:
            kind = "task" if kind in {"inbox", "idea", "thought", "link", "person", "project"} else kind
            if kind == "media":
                kind = "task"
                title = _title_for("task", stripped, None, self.url) or title
                event["title"] = title
            if not event["recurrence"]:
                due = event["start"][:10]
        else:
            if self.date is None and self.part is not None and self.horizon is None and self.deadline is None:
                ends = {"morning": 12, "day": 17, "evening": 23, "night": 24}[self.part]
                self.date = self.today if self.now.hour < ends else self.today + timedelta(days=1)
            if kind == "media" and (self.date is not None or self.horizon is not None):
                kind, media_type = "task", None
                title = _title_for("task", stripped, None, self.url) or title
            if self.date is not None:
                if kind in {"inbox"}:
                    kind, confident = "task", True
                if kind in {"task", "purchase"}:
                    due = self.date.isoformat()
                else:
                    warnings.append("Дата сохраняется только у дел и покупок")
            elif self.horizon is not None:
                if kind == "inbox":
                    kind, confident = "task", True
                if kind in {"task", "purchase"}:
                    horizon = self.horizon
            if self.deadline is not None:
                hour, minute = self.resolve(self.deadline, self.part)
                if due is None and kind in {"inbox", "task", "purchase"}:
                    kind = "task" if kind == "inbox" else kind
                    now_minutes = self.now.hour * 60 + self.now.minute
                    day = self.today if hour * 60 + minute > now_minutes else self.today + timedelta(days=1)
                    due = day.isoformat()
                    confident = True
                title = _trim_title(f"{title} (до {hour:02d}:{minute:02d})")
        importance = self.importance or "normal"
        if importance == "critical" and kind not in {"task", "purchase"}:
            importance = "high"
        context_id = self.context.id if self.context else None
        if self.context is not None and self.context.kind == "project" and kind == "project":
            context_id = None
        spans = sorted(self.spans, key=lambda span: span["start"])
        return {
            "text": self.text, "title": title, "kind": kind, "confident": confident,
            "due": due, "planning_horizon": horizon,
            "media_type": media_type if kind == "media" else None,
            "importance": importance, "context_id": context_id,
            "context_title": self.context.title if self.context and context_id else None,
            "tags": self.tags, "url": self.url,
            "route": "calendar" if event else "note", "event": event,
            "deadline": None if self.deadline is None else "{:02d}:{:02d}".format(*self.resolve(self.deadline, self.part)),
            "spans": spans, "warnings": warnings,
        }

    def classify(self, stripped: str, label: str | None) -> tuple[str, str | None, bool]:
        words = [_norm(word) for word in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", stripped)]
        lowered = " ".join(words)
        first = words[0] if words else None
        media_index = next((index for index, word in enumerate(words) if word in _MEDIA_NOUNS), None)
        temporal = (self.date is not None or self.horizon is not None or self.start is not None
                    or self.offset is not None or self.recurrence is not None or self.deadline is not None)
        if label in {"task", "purchase", "idea", "thought", "project"}:
            if label == "task" and media_index is not None and first in _CONSUME_VERBS:
                return "media", _MEDIA_NOUNS[words[media_index]], True
            return label, None, True
        if first in _PURCHASE_WORDS or (first == "бы" and len(words) > 1 and words[1] in _PURCHASE_WORDS):
            return "purchase", None, True
        if media_index is not None and (media_index == 0 or first in _CONSUME_VERBS
                                        or (media_index <= 2 and not _looks_like_verb(first))):
            return "media", _MEDIA_NOUNS[words[media_index]], True
        if self.url is not None and (first is None or first in _CONSUME_VERBS or not _looks_like_verb(first)) \
                and first not in _PURCHASE_WORDS:
            return "link", None, True
        if any(re.search(r"(?:^| )" + phrase + r"(?: |$)", lowered) for phrase in _IDEA_PHRASES):
            return "idea", None, True
        if any(re.search(r"(?:^| )" + phrase + r"(?: |$)", lowered) for phrase in _THOUGHT_PHRASES):
            return "thought", None, True
        verb = next((word for word in words[:5] if word in _PURCHASE_WORDS or _looks_like_verb(word)), None)
        if verb in _PURCHASE_WORDS:
            return "purchase", None, True
        if label == "task?" or verb is not None:
            return "task", None, True
        if temporal:
            return "task", None, True
        return "inbox", None, False

    def event(self, title: str) -> dict | None:
        if self.start is None and self.offset is None and self.recurrence is None:
            return None
        rrule = None
        if self.recurrence is not None:
            parts = ["FREQ=" + self.recurrence["freq"]]
            if self.recurrence["interval"] > 1:
                parts.append("INTERVAL=" + str(self.recurrence["interval"]))
            if self.recurrence.get("nth"):
                parts.append("BYDAY=" + str(self.recurrence["nth"]) + _BYDAY[self.recurrence["byday"][0]])
            elif self.recurrence["byday"]:
                parts.append("BYDAY=" + ",".join(_BYDAY[day] for day in self.recurrence["byday"]))
            rrule = "RRULE:" + ";".join(parts)
        if self.start is None and self.offset is not None:
            start = self.offset
            end = start + (self.duration or timedelta(minutes=self.event_minutes))
            return self.event_payload(title, start, end, False, rrule)
        day = self.date
        if self.start is None:
            day = self.first_occurrence(day or self.today, None)
            return {"title": title, "start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat(),
                    "all_day": True, "recurrence": [rrule] if rrule else None, "time_zone": self.zone.key}
        hour, minute = self.resolve(self.start, self.part or (self.end.part if self.end is not None else None))
        if self.start.midnight:
            day = (day or self.today) + timedelta(days=1)
        elif day is None:
            candidate = datetime.combine(self.today, time(hour, minute), self.zone)
            day = self.today if candidate > self.now or self.recurrence is not None else self.today + timedelta(days=1)
        elif day == self.today and datetime.combine(day, time(hour, minute), self.zone) < self.now:
            self.warnings.append("Время уже прошло")
        if self.recurrence is not None:
            day = self.first_occurrence(day, (hour, minute))
        start = datetime.combine(day, time(hour, minute), self.zone)
        if self.end is not None:
            if self.end.part or self.end.explicit:
                candidates = [self.resolve(self.end, self.start.part or self.part)]
            else:
                raw = self.end.hour % 24
                candidates = [(raw, self.end.minute)] + ([(raw + 12, self.end.minute)] if 1 <= raw <= 11 else [])
            begin = hour * 60 + minute
            end_hour, end_minute = min(candidates, key=lambda value: (value[0] * 60 + value[1] - begin) % 1440 or 1440)
            end = datetime.combine(day, time(end_hour % 24, end_minute), self.zone)
            if end <= start:
                end += timedelta(days=1)
        else:
            end = start + (self.duration or timedelta(minutes=self.event_minutes))
        return self.event_payload(title, start, end, False, rrule)

    def first_occurrence(self, day: date, clock: tuple[int, int] | None) -> date:
        if self.recurrence is not None and self.recurrence.get("nth"):
            month_start = day.replace(day=1)
            for months in range(0, 14):
                first = _add_months(month_start, months)
                matches = [first + timedelta(days=offset) for offset in range(_calendar.monthrange(first.year, first.month)[1])
                           if (first + timedelta(days=offset)).weekday() == self.recurrence["byday"][0]]
                candidate = matches[self.recurrence["nth"] - 1] if self.recurrence["nth"] > 0 else matches[-1]
                moment_passed = clock is not None and datetime.combine(candidate, time(*clock), self.zone) <= self.now
                if candidate >= day and not moment_passed:
                    return candidate
            return day
        if self.recurrence is None or not self.recurrence["byday"]:
            if self.recurrence is not None and clock is not None:
                moment = datetime.combine(day, time(*clock), self.zone)
                if moment <= self.now and self.date is None:
                    return day + timedelta(days=1) if self.recurrence["freq"] == "DAILY" else day
            return day
        for offset in range(0, 8):
            candidate = day + timedelta(days=offset)
            if candidate.weekday() not in self.recurrence["byday"]:
                continue
            if clock is not None and datetime.combine(candidate, time(*clock), self.zone) <= self.now:
                continue
            return candidate
        return day

    def event_payload(self, title: str, start: datetime, end: datetime, all_day: bool, rrule: str | None) -> dict:
        return {"title": title, "start": start.isoformat(), "end": end.isoformat(), "all_day": all_day,
                "recurrence": [rrule] if rrule else None, "time_zone": self.zone.key}


# --------------------------------------------------------------------------
# Text helpers

def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?)»])", r"\1", text)
    text = re.sub(r"([(«])\s+", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;:.])+", r"\1", text)
    text = re.sub(r"\(\s*\)|«\s*»", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.;:!?-–—")


def _strip_prefixes(text: str) -> tuple[str, str | None]:
    label: str | None = None
    signup = re.match(r"(?:запиши|записать)\s+(\S+(?:\s+\S+)?\s+(?:к|ко|на)\s.+)$", text, re.I)
    if signup and not re.match(r"(?:мне|себе|нам|пожалуйста|мысль|идею|дело|задачу|покупку|заметку|в)\b",
                               signup.group(1), re.I):
        return "Записать " + signup.group(1), "task"
    commanded = bool(re.match(r"(?:поставь|добавь|запиши|внеси|создай|заведи|закинь|кинь|сохрани|запланируй)\b",
                              text, re.I))
    previous = None
    while previous != text:
        previous = text
        for pattern, meaning in _PREFIXES:
            match = pattern.match(text)
            if not match or not match.group(0):
                continue
            rest = _tidy(text[match.end():])
            if not rest:
                continue
            if meaning and meaning != "task?":
                label = label or meaning
            elif meaning == "task?" and label is None:
                label = "task?"
            text = rest
            break
    if commanded:
        first = text.split(" ", 1)
        nominative = _ACCUSATIVE_EVENTS.get(_norm(first[0]))
        if nominative:
            text = " ".join([nominative] + first[1:])
    return text, label


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _trim_title(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_TITLE:
        return text
    cut = text[:MAX_TITLE - 1].rsplit(" ", 1)[0]
    return (cut or text[:MAX_TITLE - 1]) + "…"


def _title_for(kind: str, stripped: str, media_type: str | None, url: str | None) -> str:
    title = stripped
    if kind == "media":
        words = list(re.finditer(r"[^\W\d_]+(?:-[^\W\d_]+)*", stripped))
        for match in words:
            noun = _norm(match.group(0))
            if noun in _MEDIA_NOUNS:
                after = _tidy(stripped[match.end():])
                after = re.split(r"\s*[,;]\s*|\s+[-–—]\s+", after)[0] if after else after
                after = re.split(r"\s+(?:" + "|".join(_ATTRIBUTION) + r")\b", after, flags=re.I)[0]
                after = after.strip(" «»\"'“”")
                while after and _norm(after.rsplit(" ", 1)[-1]) in _CONSUME_VERBS and " " in after:
                    after = after.rsplit(" ", 1)[0]
                if after and _norm(after.split(" ")[0]) in {"про", "о", "об", "на", "по", "из", "от"}:
                    after = _MEDIA_NOMINATIVE.get(noun, match.group(0)) + " " + after
                if after:
                    title = after
                break
    elif kind == "link":
        words = [_norm(word) for word in re.findall(r"[^\W\d_]+", title)]
        if url and all(word in _CONSUME_VERBS or word in _LINK_FILLERS for word in words):
            title = re.sub(r"^https?://(?:www\.)?", "", url).rstrip("/")
    title = title.strip(" «»\"'“”") if kind == "media" else title
    return _trim_title(_capitalize(title))


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Неизвестный часовой пояс: " + str(name)) from exc


def parse(text: str, *, now: datetime | None = None, time_zone: str | None = None,
          contexts: Iterable[Mapping[str, Any]] = (), event_minutes: int = DEFAULT_EVENT_MINUTES) -> dict:
    """Parse one dictated capture into Brainalot fields.

    ``now`` fixes «сегодня» and relative times (tests pass a constant).  The
    result is JSON-serialisable; ``route`` is ``calendar`` when an exact time or
    a repeat was spoken and ``note`` otherwise.  ``contexts`` are dashboard
    contexts (``id``, ``title``, ``kind``) used to attach a project or person.
    """
    if not isinstance(text, str):
        raise ValueError("Текст диктовки должен быть строкой")
    zone = _zone(time_zone)
    moment = now or datetime.now(zone)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    cleaned = text.replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        raise ValueError("Пустая диктовка")
    if len(cleaned) > 2000:
        raise ValueError("Диктовка длиннее 2000 символов")
    latin = bool(re.search(r"[A-Za-z]", cleaned))
    cyrillic = bool(re.search(r"[А-Яа-яЁё]", cleaned))
    english_lead = bool(re.match(r"(?:call|email|send|review|meet|task|idea|note)\b", cleaned, re.I))
    english_time = bool(re.search(r"\b(?:today|tomorrow|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                                  r"in\s+\d+\s+(?:days?|hours?|minutes?)|at\s+\d{1,2})\b", cleaned, re.I))
    if (latin and (not cyrillic or english_lead and english_time)
            and not re.fullmatch(r"(?:https?://|www\.)\S+", cleaned, re.I)):
        from .dictation_en import parse_english
        return parse_english(cleaned, moment, zone, _contexts(contexts), event_minutes)
    return _Parser(cleaned, moment, zone, _contexts(contexts), event_minutes).run()
