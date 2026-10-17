"""Work chats: collected messages, agent batches, Work proposals, the API and the CLI (no network)."""
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from megamozg import cli, paths, telegram_chats
from megamozg.api import create_app
from megamozg.chats import ChatStore
from megamozg.core import ConflictError, Store, ValidationError
from megamozg.work_inbox import WorkInbox

MSK = timezone(timedelta(hours=3))


def clock(start=datetime(2026, 9, 29, 12, 0, tzinfo=MSK)):
    state = {"now": start}
    tick = lambda: state["now"]  # noqa: E731
    tick.state = state
    return tick


def msg(message_id, text="Текст", minute=0, **extra):
    return {"id": str(message_id), "date": f"2026-09-29T12:{minute:02d}:00+03:00", "author": "Настя", "text": text, **extra}


SNAPSHOT = {
    "projects": [{"id": "print", "name": "3D-печать", "about": "Модуль 2", "color": "teal"}],
    "items": [{"id": "i1", "project": "print", "kind": "task", "title": "Преза Edit Mode 2", "start": "2026-09-29",
               "end": "2026-09-29", "status": "doing", "firstStep": "Открыть ТЗ", "notes": "x" * 5000,
               "checklist": [{"id": "c", "text": "Модель", "done": False}],
               "groups": [{"title": "ТЗ", "links": [{"title": "ТЗ", "url": "https://docs.example/tz"}]}]}],
    "boards": [{"id": "general", "items": [{"type": "image", "imageId": "blob"}]}],
}


def test_only_new_messages_are_kept_and_edits_come_back_unsorted(tmp_path):
    store = ChatStore(tmp_path, clock=clock())
    store.add_chat("telegram", "-1001", "Модуль 2", baseline="10")
    result = store.add_messages("telegram:-1001", [msg(9, "старое"), msg(11, "см. https://docs.example/tz."), msg(12, "ок")])
    assert result == {"added": 2, "updated": 0, "skipped": 1}
    assert store.message("telegram:-1001", "11")["links"] == ["https://docs.example/tz"]
    assert store.add_messages("telegram:-1001", [msg(11, "см. https://docs.example/tz.")])["skipped"] == 1

    batch = store.unprocessed()
    assert batch["count"] == 2 and [m["id"] for m in batch["chats"][0]["messages"]] == ["11", "12"]
    # The message is edited after the agent has read it: it stays unsorted.
    store.add_messages("telegram:-1001", [msg(12, "ок, но до пятницы")])
    assert store.mark_batch(batch["batch"]) == {"batch": batch["batch"], "marked": 1, "changed_since": 1}
    again = store.unprocessed()
    assert again["count"] == 1 and again["chats"][0]["messages"][0]["text"] == "ок, но до пятницы"
    assert [m["id"] for m in again["chats"][0]["context"]] == ["11"]
    assert store.unprocessed()["batch"] != again["batch"]


def test_message_checks_reject_bad_input(tmp_path):
    store = ChatStore(tmp_path)
    store.add_chat("telegram", "1", "Чат", baseline="0")
    for bad in (msg(1, "", links=[]), msg("a b"), msg(1, link="javascript:alert(1)"), {**msg(1), "extra": 1},
                {**msg(1), "date": "2026-09-29T12:00:00"}):
        with pytest.raises(ValidationError):
            store.add_messages("telegram:1", [bad])
    with pytest.raises(ValidationError):
        store.add_chat("skype", "1", "x", "0")


def test_vk_conversation_is_watched_from_now_and_gaps_are_marked(tmp_path):
    tick = clock()
    store = ChatStore(tmp_path, clock=tick)
    chat = {"peer_id": 2000000001, "title": "Скринкаст"}
    assert store.ingest_web("vk", chat, [msg(1, minute=5)]) == {"watched": False}
    store.ingest_web("vk", chat, connect=True)
    first = store.ingest_web("vk", chat, [msg(1, "до подключения", minute=0) | {"date": "2026-09-29T11:59:00+03:00"},
                                          msg(2, minute=1)], oldest_loaded="2026-09-29T11:59:00+03:00")
    assert (first["added"], first["skipped"], first["gap"]) == (1, 1, False)
    # The tab was closed; the next time it only has later messages loaded.
    later = store.ingest_web("vk", chat, [msg(9, minute=40)], oldest_loaded="2026-09-29T12:30:00+03:00")
    assert later["gap"] is True
    healed = store.ingest_web("vk", chat, [msg(5, minute=10)], oldest_loaded="2026-09-29T12:01:00+03:00")
    assert healed["gap"] is False
    assert store.status()["unprocessed"] == 3


def test_telegram_web_chat_uses_its_negative_id_and_the_connection_time(tmp_path):
    store = ChatStore(tmp_path, clock=clock())
    chat = {"peer_id": "-5230437713", "title": "Нетология_Разработка курса"}
    assert store.ingest_web("tgweb", chat, connect=True)["chat"]["key"] == "tgweb:-5230437713"
    result = store.ingest_web("tgweb", chat, [msg(290629, minute=0) | {"date": "2026-09-29T11:00:00+03:00"}, msg(290630, minute=5)])
    assert (result["added"], result["skipped"]) == (1, 1)
    for source, peer in (("vk", "-5"), ("tgweb", "abc"), ("skype", "1")):
        with pytest.raises(ValidationError):
            store.ingest_web(source, {"peer_id": peer}, connect=True)
    store.save_markup({"source": "tgweb", "parsed": 0}, "tgweb")
    assert (tmp_path / "state" / "tgweb-markup.json").exists() and not (tmp_path / "state" / "vk-markup.json").exists()


def seeded(tmp_path):
    store = ChatStore(tmp_path)
    store.add_chat("telegram", "-1001", "Модуль 2", baseline="10")
    store.add_messages("telegram:-1001", [msg(11, "Презентации сдать до 4 октября", link="https://t.me/c/1/11")])
    inbox = WorkInbox(tmp_path, store)
    inbox.save_snapshot(SNAPSHOT)
    return store, inbox


def proposal(**extra):
    return {"external_id": "tg:-1001:11#1", "kind": "deadline", "project_id": "print", "title": "Сдать модуль 2",
            "start": "2026-10-04", "sources": [{"chat": "telegram:-1001", "message_id": "11", "quote": "сдать до 4 октября"}],
            **extra}


def test_snapshot_is_trimmed_for_agents(tmp_path):
    _, inbox = seeded(tmp_path)
    view = inbox.snapshot_for_agent()
    assert view["available"] and not view["stale"]
    item = view["items"][0]
    assert len(item["notes"]) == 1000 and item["links"] == ["https://docs.example/tz"] and "boards" not in view
    assert WorkInbox(tmp_path / "other").snapshot_for_agent()["available"] is False
    with pytest.raises(ValidationError):
        inbox.save_snapshot({"projects": "x"})


def test_proposals_are_checked_against_the_snapshot_and_the_messages(tmp_path):
    store, inbox = seeded(tmp_path)
    saved = inbox.propose({"proposals": [proposal()]}, agent="codex")["proposals"][0]
    listed = inbox.list()[0]
    assert listed["id"] == saved["id"] and listed["end"] == "2026-10-04" and listed["agent"] == "codex"
    # Author, time and link come from the stored message, not from the agent.
    source = dict(listed["sources"][0])
    assert datetime.fromisoformat(source.pop("date")) == datetime(2026, 9, 29, 12, 0, tzinfo=MSK)
    assert source == {"chat": "telegram:-1001", "chat_title": "Модуль 2", "message_id": "11", "author": "Настя",
                      "link": "https://t.me/c/1/11", "quote": "сдать до 4 октября"}
    assert inbox.propose([proposal()])["proposals"][0]["repeated"] is True
    with pytest.raises(ConflictError):
        inbox.propose([proposal(title="Другое")])
    for bad, text in ((proposal(external_id="x2", project_id="nope"), "project_id"),
                      (proposal(external_id="x3", sources=[{"chat": "telegram:-1001", "message_id": "99"}]), "sources"),
                      (proposal(external_id="x4", kind="attach"), "target_item_id"),
                      (proposal(external_id="x5", links=[{"title": "x", "url": "file:///C:/secret"}]), "http"),
                      (proposal(external_id="x6", files=[{"name": "a", "path": "C:/Windows/win.ini"}]), "files.path"),
                      (proposal(external_id="x7", start="2026-02-30"), "дата"),
                      (proposal(external_id="x8", surprise=1), "Неизвестные")):
        with pytest.raises(ValidationError, match=text):
            inbox.propose([bad])
    attach = inbox.propose([proposal(external_id="x9", kind="attach", title="", target_item_id="i1", project_id=None,
                                     links=[{"title": "Бриф", "url": "https://docs.example/brief"}])])
    assert inbox.list()[1]["project_id"] == "print" and attach["proposals"][0]["status"] == "pending"


def test_proposals_are_resolved_once(tmp_path):
    _, inbox = seeded(tmp_path)
    proposal_id = inbox.propose([proposal()])["proposals"][0]["id"]
    assert inbox.resolve(proposal_id, "accept", "item-7")["status"] == "accepted"
    assert inbox.resolve(proposal_id, "accept")["repeated"] is True
    with pytest.raises(ConflictError):
        inbox.resolve(proposal_id, "reject")
    assert inbox.list("accepted")[0]["resolution"] == {"item_id": "item-7"} and inbox.list() == []


def test_telegram_messages_become_records():
    Channel = type("Channel", (), {})
    channel = Channel()
    channel.id, channel.username = 1234, None
    entity_url = SimpleNamespace(url="https://docs.example/hidden")
    message = SimpleNamespace(id=77, date=datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc), message="Смотри ТЗ",
                              entities=[entity_url, SimpleNamespace()], out=False, mentioned=True, reply_to_msg_id=70,
                              action=None, post_author=None)
    sender = SimpleNamespace(first_name="Анастасия", last_name="Старчикова", username="nastya")
    record = telegram_chats.message_record(message, sender, channel, [])
    assert record["link"] == "https://t.me/c/1234/77" and record["links"] == ["https://docs.example/hidden"]
    assert record["author"] == "Анастасия Старчикова" and record["mentions_me"] and record["reply_to"] == 70
    public = SimpleNamespace(username="netology_chat")
    assert telegram_chats.message_link(public, 5) == "https://t.me/netology_chat/5"
    assert telegram_chats.message_link(SimpleNamespace(username=None), 5) is None
    assert telegram_chats.message_record(SimpleNamespace(**{**vars(message), "action": object()}), sender, channel, []) is None
    empty = SimpleNamespace(**{**vars(message), "message": "", "entities": []})
    assert telegram_chats.message_record(empty, sender, channel, []) is None
    assert telegram_chats.sender_name(SimpleNamespace(title="Нетология"), message) == "Нетология"
    assert telegram_chats._safe('Бриф: "итог"?.docx') == "Бриф_ _итог__.docx"


def test_telegram_needs_a_login_and_keeps_the_session_out_of_migration(tmp_path):
    assert telegram_chats.logged_in(tmp_path) is False
    with pytest.raises(ValidationError, match="telegram-login"):
        telegram_chats.dialogs(tmp_path)
    assert "telegram" in paths._STATE_SKIP


def test_api_serves_status_snapshot_proposals_and_vk(tmp_path):
    store, inbox = seeded(tmp_path)
    token = Store(tmp_path).token()
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + token, "Origin": "chrome-extension://" + "a" * 32}
    assert client.get("/api/chats/status").status_code == 401

    status = client.get("/api/chats/status", headers=headers).json()
    assert status["unprocessed"] == 1 and status["telegram"]["installed"] in (True, False)
    assert client.post("/api/work/snapshot", headers=headers, json={"state": SNAPSHOT}).json()["items"] == 1
    proposal_id = inbox.propose([proposal()])["proposals"][0]["id"]
    assert client.get("/api/work/proposals", headers=headers).json()["proposals"][0]["id"] == proposal_id
    resolved = client.post(f"/api/work/proposals/{proposal_id}", headers=headers, json={"action": "reject"})
    assert resolved.json()["status"] == "rejected"
    assert client.post("/api/work/proposals/nope", headers=headers, json={"action": "accept"}).status_code == 404

    chat = {"peer_id": "2000000007", "title": "Скринкаст"}
    web = lambda body: client.post("/api/chats/web", headers=headers, json={"source": "vk", **body})  # noqa: E731
    assert web({"chat": chat, "messages": [msg(1)]}).json() == {"watched": False}
    assert web({"chat": chat, "connect": True}).json()["chat"]["key"] == "vk:2000000007"
    later = datetime.now().astimezone() + timedelta(minutes=1)
    assert web({"chat": chat, "messages": [msg(2) | {"date": later.isoformat()}]}).json()["added"] == 1
    assert web({"chat": chat, "bogus": 1}).status_code == 422
    assert client.post("/api/chats/web", headers=headers, json={"source": "icq", "chat": chat, "connect": True}).status_code == 422
    assert client.post("/api/chats/remove", headers=headers, json={"key": "vk:2000000007"}).json()["enabled"] is False

    markup = {"path": "/im/convo/2000000009", "peer": "2000000009", "parsed": 0, "tree": {"t": "div", "c": "ConvoHistory"}}
    saving = client.post("/api/chats/markup", headers=headers, json={"source": "vk", "markup": markup})
    assert saving.json()["saved"] is True
    saved = json.loads((tmp_path / "state" / "vk-markup.json").read_text(encoding="utf-8"))
    assert saved["tree"]["c"] == "ConvoHistory" and saved["received"]
    assert client.post("/api/chats/markup", headers=headers, json={"source": "vk", "markup": "<html>"}).status_code == 422


def run_cli(args, capsys, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(args)
    out = capsys.readouterr()
    return code, json.loads(out.out or out.err)


def test_cli_lets_an_agent_read_propose_and_mark(tmp_path, capsys, monkeypatch):
    seeded(tmp_path)
    root = ["--root", str(tmp_path)]
    code, listing = run_cli(root + ["chats", "list"], capsys)
    assert code == 0 and listing["unprocessed"] == 1 and listing["telegram"]["logged_in"] is False
    # A connected Telegram chat without a login: new still returns what is stored and says why.
    code, batch = run_cli(root + ["chats", "new"], capsys)
    assert code == 0 and batch["count"] == 1 and "telegram-login" in batch["sync"]["error"]
    code, snapshot = run_cli(root + ["work", "snapshot"], capsys)
    assert snapshot["projects"][0]["id"] == "print"
    code, proposed = run_cli(root + ["work", "propose", "--agent", "claude", "--file", "-"], capsys,
                             json.dumps({"proposals": [proposal()]}), monkeypatch)
    assert code == 0 and proposed["proposals"][0]["status"] == "pending"
    code, bad = run_cli(root + ["work", "propose", "--file", "-"], capsys,
                        json.dumps({"proposals": [proposal(external_id="z", project_id="nope")]}), monkeypatch)
    assert code == 2 and "project_id" in bad["error"]["message"]
    code, marked = run_cli(root + ["chats", "done", "--batch", batch["batch"]], capsys)
    assert marked["marked"] == 1
    code, empty = run_cli(root + ["chats", "new", "--no-sync"], capsys)
    assert empty["batch"] is None and empty["next"] is None
