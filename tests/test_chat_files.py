"""Тести посилань на створені файли в чаті: персистування + санітизація."""
import json
from app.models import User, Model, ChatMessage
from app.services import chat_service, file_service
from tests.conftest import login, auth

USAGE = {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


# ---------- Санітизація вигаданих посилань ----------

def test_sanitize_drops_local_links_keeps_real():
    s = chat_service._sanitize_reply_links
    # Локальні/відносні/sandbox — зводяться до тексту.
    assert s("[звіт.xlsx](звіт.xlsx)") == "звіт.xlsx"
    assert s("[файл](sandbox:/mnt/data/f.xlsx)") == "файл"
    assert s("дивись [тут](./out/report.csv) будь ласка") == "дивись тут будь ласка"
    # Робочі — лишаються без змін.
    assert s("[док](https://example.com/a)") == "[док](https://example.com/a)"
    assert s("[файл](/files/abc/report.xlsx)") == "[файл](/files/abc/report.xlsx)"
    # Порожній/None — без падінь.
    assert s("") == ""
    assert s(None) is None


# ---------- Персистування посилань з повідомленням ----------

def _make_file(app, username="u1"):
    user = User.query.filter_by(username=username).first()
    uf = file_service.save_bytes(user, "звіт.xlsx", b"binary-data",
                                 source="skill_run")
    return user, uf


def test_files_persisted_in_message_metadata(client, app):
    user, uf = _make_file(app)
    mid = Model.query.filter_by(name="gpt-4o").first().id
    session = chat_service.create_session(user, mid)

    chat_service._store_exchange(session, user, mid, None,
                                 "зроби звіт", "Готово: файл звіт.xlsx створено",
                                 USAGE, files=[uf.to_dict()])

    msg = (ChatMessage.query.filter_by(session_id=session.id, role="assistant")
           .first())
    assert msg.msg_metadata is not None
    assert json.loads(msg.msg_metadata)["file_ids"] == [uf.id]

    d = msg.to_dict()
    assert len(d["files"]) == 1
    assert d["files"][0]["id"] == uf.id
    assert d["files"][0]["url"].startswith("/files/")


def test_persisted_files_survive_reload_via_api(client, app):
    user, uf = _make_file(app)
    mid = Model.query.filter_by(name="gpt-4o").first().id
    session = chat_service.create_session(user, mid)
    chat_service._store_exchange(session, user, mid, None, "u", "reply",
                                 USAGE, files=[uf.to_dict()])
    sid = session.id

    token = login(client, "u1", "pass")
    data = client.get(f"/api/chat/sessions/{sid}", headers=auth(token)).get_json()
    assistant = [m for m in data["messages"] if m["role"] == "assistant"][0]
    assert [f["id"] for f in assistant["files"]] == [uf.id]


def test_deleted_file_drops_from_message(client, app):
    """Живе резолвлення: якщо файл видалено — посилання зникає з повідомлення."""
    user, uf = _make_file(app)
    mid = Model.query.filter_by(name="gpt-4o").first().id
    session = chat_service.create_session(user, mid)
    chat_service._store_exchange(session, user, mid, None, "u", "reply",
                                 USAGE, files=[uf.to_dict()])
    msg = ChatMessage.query.filter_by(session_id=session.id, role="assistant").first()
    assert len(msg.to_dict()["files"]) == 1

    file_service.delete_file(user, uf.id)
    assert msg.to_dict()["files"] == []


def test_message_without_files_has_empty_list(client, app):
    user = User.query.filter_by(username="u1").first()
    mid = Model.query.filter_by(name="gpt-4o").first().id
    session = chat_service.create_session(user, mid)
    chat_service._store_exchange(session, user, mid, None, "u", "reply", USAGE)
    msg = ChatMessage.query.filter_by(session_id=session.id, role="assistant").first()
    assert msg.msg_metadata is None
    assert msg.to_dict()["files"] == []
