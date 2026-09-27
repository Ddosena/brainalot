"""Shared, JSON-speaking command boundary for the user, Codex and Claude."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .core import ConflictError, NotFoundError, Store, ValidationError
from . import paths


DEFAULT_ROOT = paths.default_root()


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, json.dumps({"error": {"type": "arguments", "message": message}}, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(description="Brainalot: общий интерфейс для человека, Codex и Claude. Вывод — JSON.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Каталог данных (по умолчанию data рядом с проектом, у установленной программы %%LOCALAPPDATA%%\\Brainalot)")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("init", help="Создать пустое хранилище; токен не выводится")
    commands.add_parser("token", help="Явно показать токен локального API")
    serve = commands.add_parser("serve", help="Запустить API на 127.0.0.1")
    serve.add_argument("--port", type=int, default=None, help="По умолчанию первый свободный из 8765–8784")
    commands.add_parser("manual", help="Открыть ручной пульт без HTTP и модели")
    listing = commands.add_parser("list", help="Прочитать актуальные Markdown-заметки")
    listing.add_argument("--kind")
    listing.add_argument("--status")
    commands.add_parser("dashboard", help="Сегодня, неделя, входящие, библиотека")
    commands.add_parser("history", help="Журнал операций")
    backup = commands.add_parser("backup", help="Согласованный ZIP снимок хранилища")
    backup.add_argument("destination", type=Path)

    capture = commands.add_parser("capture", help="Сохранить мысль; без --text/--text-file текст читается из stdin")
    source = capture.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path, help="UTF-8 файл, '-' для stdin")
    capture.add_argument("--kind", default="inbox")
    capture.add_argument("--title")
    capture.add_argument("--due", help="YYYY-MM-DD")
    capture.add_argument("--media-type")
    capture.add_argument("--url")
    capture.add_argument("--tag", action="append", default=[], dest="tags", help="Повторяемый тег")
    capture.add_argument("--source", default="cli")
    capture.add_argument("--external-id", help="Устойчивый ID события для защиты от повторов")
    capture.add_argument("--source-capture-id", help="ID ранее сохранённой исходной диктовки")
    capture.add_argument("--context-id", help="ID существующего проекта или человека")
    capture.add_argument("--topic", help="Короткая тема записи")
    capture.add_argument("--importance", choices=["low", "normal", "high", "critical"], default="normal",
                         help="critical доступен только для дел и покупок")
    capture.add_argument("--show-in-unscheduled", action="store_true",
                         help="Показывать мысль среди бессрочных дел")

    parse = commands.add_parser("parse", help="Разобрать диктовку правилами без сохранения")
    parse.add_argument("--text", help="Фраза; без аргумента читается из stdin")
    parse.add_argument("--time-zone", help="IANA-зона, по умолчанию Europe/Moscow или MMM_TIMEZONE")
    dictate = commands.add_parser("dictate", help="Разобрать и сохранить диктовку: событие Google или запись")
    dictate.add_argument("--text", help="Фраза; без аргумента читается из stdin")
    dictate.add_argument("--external-id", help="Устойчивый ID события для защиты от повторов")
    dictate.add_argument("--source", default="cli")
    dictate.add_argument("--calendar-id", help="ID календаря Brainalot для нового события")
    dictate.add_argument("--time-zone", help="IANA-зона, по умолчанию Europe/Moscow или MMM_TIMEZONE")

    update = commands.add_parser("update", help="Изменить свойства с проверкой версии")
    update.add_argument("id")
    update.add_argument("--version", required=True)
    patch = update.add_mutually_exclusive_group()
    patch.add_argument("--patch", help="JSON объект свойств")
    patch.add_argument("--patch-file", type=Path, help="UTF-8 JSON файл, '-' для stdin; без аргумента — stdin")
    delete = commands.add_parser("delete", help="Удалить заметку и её исходник с проверкой версии")
    delete.add_argument("id")
    delete.add_argument("--version", required=True)
    feedback = commands.add_parser("feedback", help="Назначить следующий показ или явно архивировать")
    feedback.add_argument("id")
    feedback.add_argument("action", choices=["soon", "later", "archive"])
    feedback.add_argument("--version", required=True)
    undo = commands.add_parser("undo", help="Отменить одну операцию; ручные правки вызывают конфликт")
    undo.add_argument("operation_id")
    integrate = commands.add_parser("integrate", help="Регистрация в Windows: автозапуск, связь с Chrome, mmm в PATH")
    integrate.add_argument("action", choices=["install", "uninstall", "status"])
    migrate = commands.add_parser("migrate", help="Скопировать библиотеку и журнал из старой папки data")
    migrate.add_argument("source", type=Path, help="Старая папка данных (в ней vault и state)")
    return parser


def _read_input(text: str | None, path: Path | None, label: str) -> str:
    if text is not None:
        return text
    if path is not None and str(path) != "-":
        return path.read_text(encoding="utf-8-sig")
    if sys.stdin.isatty():
        raise ValidationError(f"{label}: передайте текст через аргумент, UTF-8 файл или stdin")
    return sys.stdin.read().lstrip("\ufeff")


def _output(value: object, *, error: bool = False) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str), file=sys.stderr if error else sys.stdout)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0].startswith("chrome-extension://"):
        # Chrome starts the native-messaging host with the caller's origin: mmm.exe doubles as the host.
        from .native_host import main as native_host
        return native_host()
    # Keep the same UTF-8 protocol in PowerShell, redirected files and agent subprocesses.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = paths.prepare_installed_root(args.root.expanduser().resolve())
    try:
        if args.command == "serve":
            if args.port is not None and not 1024 <= args.port <= 65535:
                raise ValidationError("Порт должен быть от 1024 до 65535")
            from .api import serve
            serve(root, port=args.port)
            return 0
        if args.command == "manual":
            try:
                from .manual import run
            except ImportError as exc:  # the installed program ships without tkinter
                raise ValidationError("Ручного пульта нет в установленной программе: пользуйтесь панелью Chrome") from exc
            run(root)
            return 0
        if args.command == "integrate":
            from . import integrate
            _output(getattr(integrate, args.action)(root))
            return 0
        if args.command == "migrate":
            _output(paths.migrate(args.source.expanduser().resolve(), root))
            return 0

        store = Store(root)
        match args.command:
            case "init":
                result = {"status": "ok", "root": str(root), "vault": str(store.vault), "mode": "manual"}
            case "token":
                result = {"token": store.token()}
            case "capture":
                result = store.capture(
                    _read_input(args.text, args.text_file, "Текст мысли"),
                    kind=args.kind, title=args.title, due=args.due, media_type=args.media_type,
                    url=args.url, tags=args.tags, source=args.source, external_id=args.external_id,
                    source_capture_id=args.source_capture_id, context_id=args.context_id,
                    topic=args.topic, importance=args.importance,
                    show_in_unscheduled=args.show_in_unscheduled,
                )
            case "parse" | "dictate":
                from .calendar import CalendarFeed
                from .dictation_service import DictationService
                from .google_calendar_api import GoogleCalendarApi
                service = DictationService(store, CalendarFeed(root), GoogleCalendarApi(root))
                text = _read_input(args.text, None, "Текст диктовки")
                if args.command == "parse":
                    result = service.preview(text, time_zone=args.time_zone)
                else:
                    result = service.apply(text, external_id=args.external_id, source=args.source,
                                           calendar_id=args.calendar_id, time_zone=args.time_zone)
            case "list":
                notes = store.list_notes()
                result = {"notes": [n for n in notes if (not args.kind or n["kind"] == args.kind) and (not args.status or n["status"] == args.status)]}
            case "dashboard":
                result = store.dashboard()
            case "update":
                patch = json.loads(_read_input(args.patch, args.patch_file, "JSON изменений"))
                if not isinstance(patch, dict):
                    raise ValidationError("Изменения должны быть JSON объектом")
                result = store.update(args.id, patch, args.version)
            case "delete":
                result = store.delete(args.id, args.version)
            case "feedback":
                result = store.feedback(args.id, args.action, args.version)
            case "history":
                result = {"operations": store.history()}
            case "undo":
                result = store.undo(args.operation_id)
            case "backup":
                result = {"path": str(store.backup(args.destination.expanduser().resolve()))}
            case _:
                raise ValidationError("Неизвестная команда")
        _output(result)
        return 0
    except ConflictError as exc:
        _output({"error": {"type": "conflict", "message": str(exc)}}, error=True)
        return 3
    except NotFoundError as exc:
        _output({"error": {"type": "not_found", "message": str(exc)}}, error=True)
        return 4
    except (ValidationError, json.JSONDecodeError, UnicodeError) as exc:
        _output({"error": {"type": "validation", "message": str(exc)}}, error=True)
        return 2
    except OSError as exc:
        _output({"error": {"type": "io", "message": str(exc)}}, error=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
