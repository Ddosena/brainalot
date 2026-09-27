"""The first-run page (http://127.0.0.1:<port>/welcome): what to do after installing Brainalot.

It is served without the API token, so it shows only local paths and instructions, never notes.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
import sys
from urllib.parse import quote

from . import __version__
from .integrate import STORE_URL, extension_folder

STYLE = """
:root{--bg:#f5f7f5;--card:#fff;--ink:#17352f;--muted:#5b6f69;--accent:#146b61;--line:#dbe4e0;--spark:#ffb03b}
@media(prefers-color-scheme:dark){:root{--bg:#111d1b;--card:#172725;--ink:#e3eeea;--muted:#9db3ad;--accent:#8dd8c4;--line:#27403b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "Segoe UI",system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:48px 20px 64px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:28px}.brand img{width:44px;height:44px}
h1{font-size:30px;line-height:1.2;margin:0}.lead{color:var(--muted);margin:6px 0 0}
ol{list-style:none;padding:0;margin:0;counter-reset:step}
li{counter-increment:step;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px 18px 60px;margin:0 0 12px;position:relative}
li::before{content:counter(step);position:absolute;left:18px;top:17px;width:28px;height:28px;border-radius:50%;background:var(--accent);color:var(--bg);font-weight:700;display:grid;place-items:center;font-size:14px}
h2{font-size:17px;margin:0 0 4px}p{margin:4px 0}.muted{color:var(--muted);font-size:14px}
code{font:13px/1.4 Consolas,monospace;background:color-mix(in srgb,var(--accent) 10%,transparent);padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}
a.button{display:inline-block;margin-top:10px;background:var(--accent);color:var(--bg);text-decoration:none;padding:9px 16px;border-radius:9px;font-weight:600}
footer{margin-top:28px;color:var(--muted);font-size:13px}
"""

TEXT = {
    "ru": {
        "title": "Brainalot работает",
        "lead": "Программа запущена в фоне и стартует вместе с Windows. Её значок — в области уведомлений.",
        "ext": "Добавьте расширение в Chrome",
        "ext_store": "Откройте страницу расширения и нажмите «Установить».",
        "ext_button": "Открыть в Chrome Web Store",
        "ext_local": "Пока расширения нет в магазине, загрузите его из папки программы: откройте "
                     "<code>chrome://extensions</code>, включите «Режим разработчика», нажмите «Загрузить "
                     "распакованное расширение» и выберите папку:",
        "panel": "Откройте боковую панель",
        "panel_text": "Нажмите значок Brainalot на панели Chrome. Панель подключится сама — токен вводить не нужно.",
        "notes": "Ваши заметки",
        "notes_text": "Обычные Markdown-файлы на этом компьютере. Их можно открыть в Obsidian:",
        "obsidian": "Открыть в Obsidian",
        "agents": "Для Codex и Claude",
        "agents_text": "В терминале доступна команда <code>mmm</code>: <code>mmm list</code>, <code>mmm capture --text \"...\"</code>.",
        "footer": "Эту страницу можно закрыть. Снова открыть её — пункт «Как начать» в меню значка Brainalot.",
    },
    "en": {
        "title": "Brainalot is running",
        "lead": "It runs in the background and starts with Windows. Its icon is in the notification area.",
        "ext": "Add the Chrome extension",
        "ext_store": "Open the extension page and press “Add to Chrome”.",
        "ext_button": "Open in the Chrome Web Store",
        "ext_local": "Until the extension is in the store, load it from the program folder: open "
                     "<code>chrome://extensions</code>, turn on “Developer mode”, press “Load unpacked” and "
                     "choose this folder:",
        "panel": "Open the side panel",
        "panel_text": "Click the Brainalot icon in Chrome. The panel connects by itself — no token to paste.",
        "notes": "Your notes",
        "notes_text": "Plain Markdown files on this computer. You can open them in Obsidian:",
        "obsidian": "Open in Obsidian",
        "agents": "For Codex and Claude",
        "agents_text": "The <code>mmm</code> command works in a terminal: <code>mmm list</code>, <code>mmm capture --text \"...\"</code>.",
        "footer": "You can close this page. Open it again with “Getting started” in the Brainalot icon menu.",
    },
}


def page(vault: Path, port: int, language: str = "ru") -> str:
    text = TEXT.get(language, TEXT["ru"])
    folder = extension_folder()
    if STORE_URL:
        extension = f"<p>{text['ext_store']}</p><a class=button href=\"{escape(STORE_URL)}\">{text['ext_button']}</a>"
    else:
        extension = f"<p>{text['ext_local']}</p><p><code>{escape(str(folder))}</code></p>"
    obsidian = "obsidian://open?path=" + quote(str(vault), safe="")
    return f"""<!doctype html><html lang="{language}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Brainalot</title><style>{STYLE}</style>
<main><div class=brand><img src="/welcome/icon.png" alt=""><div><h1>{text['title']}</h1>
<p class=lead>{text['lead']}</p></div></div>
<ol>
<li><h2>{text['ext']}</h2>{extension}</li>
<li><h2>{text['panel']}</h2><p>{text['panel_text']}</p></li>
<li><h2>{text['notes']}</h2><p>{text['notes_text']}</p><p><code>{escape(str(vault))}</code></p>
<a class=button href="{escape(obsidian)}">{text['obsidian']}</a></li>
<li><h2>{text['agents']}</h2><p>{text['agents_text']}</p></li>
</ol>
<footer>{text['footer']}<br>Brainalot {escape(__version__)} · 127.0.0.1:{port}</footer></main></html>"""


def icon_bytes() -> bytes:
    for base in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]):
        candidate = base / "extension" / "icons" / "icon-128.png"
        if candidate.is_file():
            return candidate.read_bytes()
    return b""
