"""Тести чату з обраною моделлю, застосування скілів та обліку токенів."""
from app.extensions import db
from app.models import Model, Skill, ChatMessage, TokenUsageLog
from tests.conftest import login, auth


def _model_id():
    return Model.query.filter_by(name="gpt-4o").first().id


def test_create_session_and_send_message(client):
    token = login(client, "u1", "pass")
    res = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": _model_id()})
    assert res.status_code == 201
    sid = res.get_json()["id"]

    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "Привіт, як справи?"})
    assert res.status_code == 200
    data = res.get_json()
    # Облік токенів: вхідні, вихідні, загальні.
    assert data["usage"]["prompt_tokens"] > 0
    assert data["usage"]["completion_tokens"] > 0
    assert data["usage"]["total_tokens"] == (
        data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"])
    assert data["session_total_tokens"] == data["usage"]["total_tokens"]


def test_session_keeps_history(client):
    token = login(client, "u1", "pass")
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": _model_id()}).get_json()["id"]
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "перше"})
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "друге"})
    res = client.get(f"/api/chat/sessions/{sid}", headers=auth(token))
    msgs = res.get_json()["messages"]
    # 2 обміни = 4 повідомлення (user+assistant двічі).
    assert len(msgs) == 4


def test_apply_skill_in_chat(client, app):
    # u1 активує опублікований скіл, далі застосовує його в чаті.
    token = login(client, "u1", "pass")
    skill_id = Skill.query.filter_by(name="Summarizer").first().id
    client.post(f"/api/skills/{skill_id}/activate", headers=auth(token))

    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": _model_id()}).get_json()["id"]
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "довгий текст", "skill_id": skill_id})
    assert res.status_code == 200
    assert res.get_json()["skill_id"] == skill_id

    user_msg = ChatMessage.query.filter_by(session_id=sid, role="user").first()
    assert user_msg.skill_id == skill_id


def test_chat_logs_token_usage(client):
    token = login(client, "u1", "pass")
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": _model_id()}).get_json()["id"]
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "облік токенів"})
    log = TokenUsageLog.query.filter_by(session_id=sid).first()
    assert log is not None
    assert log.total_tokens == log.prompt_tokens + log.completion_tokens


def test_inactive_model_not_usable(client, app):
    token_admin = login(client, "admin", "Admin123!")
    mid = _model_id()
    # Деактивуємо модель.
    client.post(f"/api/models/{mid}/activate", headers=auth(token_admin),
                json={"is_active": False})

    token = login(client, "u1", "pass")
    res = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid})
    assert res.status_code == 400


def test_active_filter_excludes_inactive(client):
    token = login(client, "admin", "Admin123!")
    mid = _model_id()
    client.post(f"/api/models/{mid}/activate", headers=auth(token),
                json={"is_active": False})
    res = client.get("/api/models?active=1", headers=auth(token))
    ids = [m["id"] for m in res.get_json()]
    assert mid not in ids


def test_delete_session(client):
    token = login(client, "u1", "pass")
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": _model_id()}).get_json()["id"]
    res = client.delete(f"/api/chat/sessions/{sid}", headers=auth(token))
    assert res.status_code == 200
    assert client.get(f"/api/chat/sessions/{sid}", headers=auth(token)).status_code == 404
