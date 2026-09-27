"""Small direct Store control panel. No HTTP service or model required."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .core import Store

KIND_LABELS = {"Входящие":"inbox", "Дело":"task", "Покупка":"purchase", "Медиа":"media",
               "Идея":"idea", "Мысль":"thought", "Ссылка":"link", "Проект":"project", "Человек":"person"}
IMPORTANCE_LABELS = {"Низкая":"low", "Обычная":"normal", "Высокая":"high", "Очень высокая":"critical"}
MEDIA_LABELS = {"Фильм":"movie", "Сериал":"series", "Книга":"book", "Игра":"game",
                "Подкаст":"podcast", "Статья":"article"}
STATUS_LABELS = {"inbox":"Входящие", "active":"Активно", "done":"Выполнено",
                 "cancelled":"Отменено", "archived":"В архиве"}


def run(root: str | Path) -> None:
    store = Store(root)
    window = tk.Tk()
    window.title("Brainalot — ручной пульт")
    window.geometry("800x700")
    canvas = tk.Canvas(window, highlightthickness=0)
    scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    frame = ttk.Frame(canvas, padding=12)
    frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(frame_id, width=event.width))
    text = tk.Text(frame, height=5, wrap="word")
    text.pack(fill="x")
    kind = tk.StringVar(value="Входящие")
    ttk.Label(frame, text="Категория").pack(anchor="w")
    ttk.Combobox(frame, textvariable=kind, values=list(KIND_LABELS), state="readonly").pack(fill="x")
    title = ttk.Entry(frame)
    title.pack(fill="x")
    ttk.Label(frame, text="Название (можно оставить пустым)").pack(anchor="w")
    tags = ttk.Entry(frame)
    tags.pack(fill="x")
    ttk.Label(frame, text="Теги через запятую").pack(anchor="w")
    context = ttk.Combobox(frame, state="readonly")
    context.pack(fill="x")
    ttk.Label(frame, text="К чему относится (проект или человек)").pack(anchor="w")
    topic = ttk.Entry(frame)
    topic.pack(fill="x")
    ttk.Label(frame, text="Тема (можно оставить пустой)").pack(anchor="w")
    importance = tk.StringVar(value="Обычная")
    ttk.Combobox(frame, textvariable=importance, values=list(IMPORTANCE_LABELS), state="readonly").pack(fill="x")
    ttk.Label(frame, text="Важность показа").pack(anchor="w")
    due = ttk.Entry(frame)
    due.pack(fill="x")
    due.insert(0, "")
    ttk.Label(frame, text="Срок YYYY-MM-DD (можно оставить пустым)").pack(anchor="w")
    media = tk.StringVar(value="Фильм")
    ttk.Combobox(frame, textvariable=media, values=list(MEDIA_LABELS), state="readonly").pack(fill="x")
    notes_box = tk.Listbox(frame, height=16)
    notes_box.pack(fill="both", expand=True)
    details = tk.Text(frame, height=6, wrap="word")
    details.pack(fill="x")
    ttk.Label(frame, text="Тело записи — только чтение; правьте его в Obsidian.").pack(anchor="w")
    details.configure(state="disabled")
    notes: list[dict] = []
    context_choices: dict[str, str | None] = {"Без привязки": None}
    selected_id = None

    def refresh():
        nonlocal notes, context_choices
        notes = store.list_notes()
        previous = context.get()
        context_choices = {"Без привязки": None}
        for note in notes:
            if note["kind"] in {"project", "person"}:
                suffix = " (в архиве)" if note["status"] == "archived" else ""
                context_choices[f"{note['title']}{suffix} · {note['id'][:8]}"] = note["id"]
        context.configure(values=list(context_choices))
        context.set(previous if previous in context_choices else "Без привязки")
        notes_box.delete(0, "end")
        for note in notes:
            notes_box.insert("end", f"{next((k for k,v in KIND_LABELS.items() if v == note['kind']), note['kind'])} · "
                       f"{STATUS_LABELS.get(note['status'], note['status'])} · {note.get('due') or 'Без срока'} · {note['title']}")

    def selected():
        indexes = notes_box.curselection()
        return notes[indexes[0]] if indexes else None

    def show(_=None):
        nonlocal selected_id
        note = selected()
        details.configure(state="normal")
        details.delete("1.0", "end")
        if note:
            selected_id = note["id"]
            kind.set(next(k for k,v in KIND_LABELS.items() if v == note["kind"]))
            media.set(next((k for k,v in MEDIA_LABELS.items() if v == note.get("media_type")), "Фильм"))
            importance.set(next((k for k,v in IMPORTANCE_LABELS.items() if v == note.get("importance", "normal")), "Обычная"))
            linked = next((label for label, identifier in context_choices.items() if identifier == note.get("context_id")), "Без привязки")
            context.set(linked)
            for field, value in ((due, note.get("due") or ""), (title, note["title"]), (tags, ", ".join(note["tags"])),
                                 (topic, note.get("topic") or "")):
                field.delete(0, "end"); field.insert(0, value)
            details.insert("end", f"{note['id']}\n{note['path']}\n{note['body']}")
        details.configure(state="disabled")

    def new_entry():
        nonlocal selected_id
        selected_id = None
        notes_box.selection_clear(0, "end")
        kind.set("Входящие"); media.set("Фильм"); importance.set("Обычная")
        context.set("Без привязки")
        for field in (due, title, tags, topic): field.delete(0, "end")
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.configure(state="disabled")

    def save():
        try:
            selected_kind = KIND_LABELS[kind.get()]
            store.capture(text.get("1.0", "end-1c"), kind=selected_kind, title=title.get() or None,
                          tags=[x.strip() for x in tags.get().split(",") if x.strip()], due=due.get() or None if selected_kind in {"task", "purchase"} else None,
                          media_type=MEDIA_LABELS[media.get()] if selected_kind == "media" else None,
                          context_id=context_choices.get(context.get()), topic=topic.get() or None,
                          importance=IMPORTANCE_LABELS[importance.get()], source="manual")
            text.delete("1.0", "end")
            new_entry()
            refresh()
        except Exception as exc:
            messagebox.showerror("Не удалось сохранить", str(exc), parent=window)

    def change(status=None):
        note = selected()
        if not note or selected_id != note["id"]:
            return
        try:
            selected_kind = KIND_LABELS[kind.get()]
            patch = {"status": status} if status else {"kind": selected_kind, "title": title.get(),
                "tags": [x.strip() for x in tags.get().split(",") if x.strip()], "due": due.get() or None if selected_kind in {"task", "purchase"} else None,
                "media_type": MEDIA_LABELS[media.get()] if selected_kind == "media" else None,
                "context_id": context_choices.get(context.get()), "topic": topic.get() or None,
                "importance": IMPORTANCE_LABELS[importance.get()]}
            store.update(note["id"], patch, note["version"])
            refresh()
        except Exception as exc:
            messagebox.showerror("Не удалось изменить", str(exc), parent=window)

    actions = ttk.Frame(frame)
    actions.pack(fill="x", pady=8)
    for label, command in [("Новая запись", new_entry), ("Добавить", save), ("Обновить", refresh), ("Сохранить изменения", change),
                            ("Выполнено", lambda: change("done")), ("Отменено", lambda: change("cancelled"))]:
        ttk.Button(actions, text=label, command=command).pack(side="left", padx=2)
    notes_box.bind("<<ListboxSelect>>", show)
    refresh()
    window.mainloop()
