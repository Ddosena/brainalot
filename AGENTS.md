# Brainalot agent contract

This checkout is the Work edition (`codex/work`). The independent edition without
Work is `codex/no-work`; `main` remains on that stable line. Do not merge Work
into it without the owner's explicit request. Work uses `work-v*` prereleases
and the `win-work` update channel; the ordinary edition uses `v*` and `win`.

Brainalot is a local Markdown library with a shared Python CLI. Personal data lives
under `data/` and must never be committed, uploaded, or copied into source files.

When acting as the user's librarian:

1. Read the current state with `.venv\Scripts\python.exe -m megamozg dashboard`.
2. Check `inbox` on every user turn. Sort unambiguous records; ask only for the
   missing fact when a record is ambiguous.
3. Save dictated content through the CLI. Use a stable, unique `external_id` so
   retries cannot create duplicates.
4. Reuse existing project/person IDs and topics. Do not invent deadlines,
   sources, commitments, or decisions.
5. Read a fresh record version before update or deletion and verify the result.

Useful commands:

For Work progress, weekly planning, and rescheduling, read
`docs/work-planning-agent-runbook.md`. Start with `mmm work plan status`, not the
whole conversation or course archive. Save partial completion and actual time
separately. Use typed planning commands with revision/request IDs, preserve
locked cubes, and include lessons, live webinars and weekly review in the day cap.

```powershell
.venv\Scripts\python.exe -m megamozg dashboard
.venv\Scripts\python.exe -m megamozg list
.venv\Scripts\python.exe -m megamozg capture --text "..." --kind thought --source codex --external-id UNIQUE_ID
.venv\Scripts\python.exe -m megamozg parse --text "..."
.venv\Scripts\python.exe -m megamozg update ID --version HASH --patch '{"status":"done"}'
.venv\Scripts\python.exe -m megamozg history
```

`parse` shows how the capture window's rules read a phrase without saving it.
`dictate --external-id UNIQUE_ID` saves the same way the capture window does and
may create a Google Calendar event, so use it only for the user's own dictation.

Development checks:

Current desktop/time contract (0.2.3): Work is disabled; keep its data and code.
Full, compact card and side tab are always topmost, with no hide-to-tray state.
Window changes never change time accounting. Closing exits through safe pause.
All clients use the same TimeStore journal and source/id task references.
Row mode selection does not start work; the separate Play/Stop control does.
Pomodoro uses the shared work/break preset; row totals span all sessions, while
the daily report stays date-scoped. Recall pause and filters live in the existing
settings gear. Preserve CAS, exact request receipts and explicit ambiguous-response
recovery. Test on temporary roots/ports/profiles, never personal data or port8765.
The release builder checks CLI/native messaging in isolation; real desktop QA must
use the explicit diagnostic entry point, because normal app startup has a global
per-user singleton and may open an already running personal window.

```powershell
.venv\Scripts\python.exe -m pytest
node --test extension/tests/*.test.mjs
node tools/i18n/cli.mjs check
```

The local service (port 8765) runs under a supervisor, `scripts/Start-MegaMozg.ps1`,
started at logon by the Task Scheduler task `MegaMozg Local Service`;
`Start-Service.cmd` uses that task when it exists. To load new code, stop the
`python` process that listens on 8765: the supervisor starts a fresh one within
a few seconds (`data\state\logs\supervisor.log`). Do not start `megamozg serve`
for port 8765 from your own terminal: the process would belong to your program
and could close together with it. Save `.ps1` files as UTF-8 with BOM, because
Windows PowerShell 5.1 reads files without it as ANSI.

Write new interface text in Russian inside `t("…")` (or HTML with
`data-i18n`), then run `node tools/i18n/cli.mjs extract --update` and fill the
English value in `extension/locales/en.js`.

After each completed change intended for another computer, rebuild the public
package, synchronize the public Git working tree, run the checks, commit, and
push to the configured remote. Do not report the update as published until the
local `HEAD` matches `git ls-remote` for the remote branch. If push fails, state
clearly that the change is local only and report the blocker.
