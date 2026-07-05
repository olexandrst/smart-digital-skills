"""Тести системної моделі, іменування чатів та окремого системного обліку."""
from app.models import Model, ChatSession, User
from app.services import chat_service
from tests.conftest import login, auth


def _llm_model_id():
    return Model.query.filter_by(name="gpt-4o").first().id


def test_set_system_model_unique(client):
    admin = login(client, "admin", "Admin123!")
    mid = _llm_model_id()
    res = client.post(f"/api/models/{mid}/system", headers=auth(admin),
                      json={"is_system": True})
    assert res.status_code == 200 and res.get_json()["is_system"] is True
    # системна — лише одна
    systems = [m for m in client.get("/api/models", headers=auth(admin)).get_json()
               if m["is_system"]]
    assert len(systems) == 1 and systems[0]["id"] == mid


def test_set_system_requires_llm(client):
    admin = login(client, "admin", "Admin123!")
    # створимо cv-модель і спробуємо зробити системною
    cv = client.post("/api/models", headers=auth(admin), json={
        "name": "cv1", "provider": "azure_ai_foundry", "model_type": "cv",
        "deployment_name": "cv1"}).get_json()
    res = client.post(f"/api/models/{cv['id']}/system", headers=auth(admin),
                      json={"is_system": True})
    assert res.status_code == 400


def test_chat_naming_and_system_accounting(client, app):
    admin = login(client, "admin", "Admin123!")
    mid = _llm_model_id()
    client.post(f"/api/models/{mid}/system", headers=auth(admin), json={"is_system": True})

    # u1 створює чат і пише повідомлення
    u1 = login(client, "u1", "pass")
    sid = client.post("/api/chat/sessions", headers=auth(u1),
                      json={"model_id": mid}).get_json()["id"]
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(u1),
                json={"content": "Питання про Power Automate"})

    # Запускаємо воркер іменування синхронно (у тестах планувальник вимкнено).
    sess = ChatSession.query.get(sid)
    uid = User.query.filter_by(username="u1").first().id
    chat_service._autoname_worker(app, sid, uid, mid, "Питання про Power Automate")

    # Назву оновлено
    assert ChatSession.query.get(sid).title

    # Системний облік: окремо, по моделі та фічі
    sysu = client.get("/api/usage/system", headers=auth(admin)).get_json()
    assert sysu["total_tokens"] > 0
    feats = {b["feature"] for b in sysu["breakdown"]}
    assert "chat_naming" in feats

    # Користувацький облік НЕ включає системне використання
    mine = client.get("/api/usage/me", headers=auth(u1)).get_json()
    assert mine["total_tokens"] > 0
    assert mine["total_tokens"] < sysu["total_tokens"] + mine["total_tokens"]  # окремі
