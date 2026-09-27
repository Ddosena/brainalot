# Privacy policy — Brainalot

Last updated: 2026-09-27

**Brainalot stores your library on your computer. We do not run a notes server or collect analytics.
Optional Google Calendar integration, model downloads and update checks make the network requests
described below.**

## What Brainalot is

Brainalot is a Windows program and a Chrome extension. The program stores your notes, tasks and
settings as files on your computer. The extension shows them in Chrome's side panel and talks only to
the program on the same computer (address `127.0.0.1`).

## Data the extension handles

| Data | Where it goes |
|---|---|
| Notes, tasks, reminders you write or dictate | Sent to the Brainalot program on your computer (`127.0.0.1`) and saved as Markdown files in your Documents folder. |
| Files you attach | Saved next to your notes on your computer. |
| Voice recordings (when you use dictation) | Sent to the Brainalot program on your computer and turned into text there. Not sent to the internet. |
| Settings, the connection token, a queue of notes not yet saved | Stored in Chrome's local extension storage on this computer. |
| Browser tabs (`tabs` permission) | Used to open and place the Brainalot windows, and — only when you choose to save the current page — to read that tab's address and title, which become a note on your computer. Page contents and browsing history are not read. |

The extension does not read web pages, does not inject scripts into sites, and has no access to any
address except `127.0.0.1`.

## Data that leaves your computer

- **Google Calendar.** If you connect a calendar, the Brainalot program downloads your calendar from
  Google, and — if you allow editing — creates, moves or deletes events when you request it. This goes directly from your
  computer to Google under Google's privacy policy. The calendar link and access keys are stored only on
  your computer. Text used to create an event is sent to that Google Calendar.
- **Speech model.** When you first enable voice input, the program downloads model weights from
  Hugging Face and its file delivery services. They receive the download request and network metadata
  such as your IP address; your recording and transcript are not uploaded. Once downloaded, the model
  is loaded locally. Audio is deleted from the program's temporary transcription file after processing.
- **Updates.** Installed copies automatically check releases at
  [GitHub](https://github.com/Ddosena/brainalot/releases). Requests include network metadata such as
  your IP address but no notes, recordings, calendar credentials or connection token. Portable and
  source copies do not run the installed updater. A configured update mirror receives these requests
  instead if `BRAINALOT_UPDATE_URL` is set.
- **Your system's file sync.** Windows may redirect Documents into OneDrive or another synced folder.
  Your own sync configuration can therefore upload Markdown files and attachments. Brainalot itself
  does not enable file sync; access tokens and service state use Local AppData.

We do not sell user data, use it for advertising, or use it to determine creditworthiness. Data obtained
from Google APIs is used only for the calendar features you request, in accordance with the Chrome
Web Store User Data Policy, including its Limited Use requirements.

## Deleting your data

Uninstalling the program leaves your notes in `Documents\Brainalot` so you do not lose them; delete that
folder and `%LOCALAPPDATA%\Brainalot` to remove everything. Removing the extension deletes its local
storage in Chrome. Source installations keep their files under `data/` in the project folder. Local
operation and delivery journals retain earlier content to support recovery and undo; deleting an
individual note does not erase all journal history. Remove the data folders and your own backups to
erase the entire local library. Google Calendar events are managed separately in your Google account.

## Contact

Questions: [Brainalot issues](https://github.com/Ddosena/brainalot/issues). Do not post private notes,
tokens, recordings or calendar links in a public issue.

---

# Политика конфиденциальности — Brainalot

**Библиотека хранится на вашем компьютере. У нас нет сервера заметок и аналитики. Подключение
Google Календаря, загрузка модели и проверка обновлений используют интернет.**

- Расширение обращается только к программе Brainalot на этом же компьютере (`127.0.0.1`). Записи,
  файлы и голос уходят туда. Записи сохраняются в Markdown в «Документы\Brainalot». Голос
  распознаётся локально и не отправляется в интернет; временный аудиофайл удаляется после обработки.
- Настройки, токен подключения и очередь несохранённых записей лежат в локальном хранилище Chrome.
- Разрешение `tabs` нужно, чтобы открывать окна Brainalot и — только когда вы сами сохраняете текущую
  страницу — взять её адрес и заголовок для записи. Содержимое страниц и история не читаются.
- При подключении Google Календаря приложение читает события; с разрешением на запись создаёт,
  переносит и удаляет их по вашему действию. Текст нового события отправляется напрямую в Google.
- При первом включении голоса модель скачивается с Hugging Face. Установленная программа
  автоматически проверяет обновления на GitHub или настроенном зеркале. Эти сервисы видят сетевые
  метаданные, включая IP-адрес, но не записи, аудио или токены календаря.
- Если «Документы» синхронизируются через OneDrive или другой сервис, ваши настройки системы могут
  отправлять туда заметки и вложения. Brainalot не включает синхронизацию; секреты лежат в Local AppData.
- Мы не продаём данные и не используем их для рекламы. Данные Google API используются только
  для запрошенных функций календаря с соблюдением Chrome Web Store User Data Policy и Limited Use.
- Удаление программы оставляет заметки в «Документы\Brainalot»; чтобы стереть всё, удалите эту папку и
  `%LOCALAPPDATA%\Brainalot`, локальное хранилище расширения и собственные резервные копии.
  При установке из исходников удалите `data/`. Удаление отдельной заметки не очищает журнал
  восстановления и отмены операций. События Google удаляются отдельно в вашем Google-аккаунте.
