"""Тести онлайн-режиму чату (веб-пошук DuckDuckGo, гібридний RAG) та обліку."""
from app.models import Model, Skill, TokenUsageLog
from app.services import web_search_service
from tests.conftest import login, auth


def _session(client, token):
    mid = Model.query.filter_by(name="gpt-4o").first().id
    return client.post("/api/chat/sessions", headers=auth(token),
                       json={"model_id": mid}).get_json()["id"]


# ---------- Сервіс веб-пошуку ----------

def test_web_search_service_mock(app):
    with app.app_context():
        res = web_search_service.search("flask tutorial", max_results=3)
        assert len(res) == 3
        assert all({"title", "url", "snippet"} <= set(r) for r in res)
        assert "не дав результатів" not in web_search_service.format_results(res)
        assert web_search_service.format_results([]) == "(веб-пошук не дав результатів)"


# ---------- Онлайн-режим у чаті ----------

def test_offline_message_logs_offline_mode(client):
    token = login(client, "u1", "pass")
    sid = _session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "привіт"})
    assert res.get_json()["mode"] == "offline"
    log = TokenUsageLog.query.filter_by(is_system=False).order_by(TokenUsageLog.id.desc()).first()
    assert log.mode == "offline"


def test_online_message_sums_two_calls(client):
    token = login(client, "u1", "pass")
    sid = _session(client, token)
    # Офлайн-повідомлення для порівняння вартості одного виклику.
    off = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "Розкажи про Power Automate"}).get_json()

    on = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                     json={"content": "Останні новини про Power Automate",
                           "web_search": True}).get_json()
    assert on["mode"] == "online"
    # Онлайн = два виклики LLM → токенів більше, ніж за один офлайн-виклик.
    assert on["usage"]["total_tokens"] > off["usage"]["total_tokens"]
    assert on["usage"]["prompt_tokens"] > 0 and on["usage"]["completion_tokens"] > 0
    # Джерела додано у відповідь.
    assert "Джерела" in on["content"]

    log = TokenUsageLog.query.filter_by(is_system=False).order_by(TokenUsageLog.id.desc()).first()
    assert log.mode == "online"
    # Токени залоговані сумою обох викликів (== повернутому usage).
    assert log.total_tokens == on["usage"]["total_tokens"]


def test_web_search_ignored_with_skill(client):
    token = login(client, "u1", "pass")
    skill = Skill.query.filter_by(name="Summarizer").first()
    client.post(f"/api/skills/{skill.id}/activate", headers=auth(token))
    sid = _session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "стисни", "skill_id": skill.id, "web_search": True})
    assert res.status_code == 200
    assert res.get_json()["mode"] == "offline"  # з навичкою веб-пошук не застосовується


# ---------- Аналітика: розподіл за режимом + підсумок навичок ----------

def test_usage_by_mode_and_skills_total(client):
    token = login(client, "u1", "pass")
    skill = Skill.query.filter_by(name="Summarizer").first()
    client.post(f"/api/skills/{skill.id}/activate", headers=auth(token))
    sid = _session(client, token)
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "офлайн-питання"})
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "онлайн-питання", "web_search": True})
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "текст", "skill_id": skill.id})

    me = client.get("/api/usage/me", headers=auth(token)).get_json()
    modes = {m["mode"]: m for m in me["by_mode"]}
    assert set(modes) == {"offline", "online"}
    assert modes["online"]["requests"] == 1
    assert modes["offline"]["requests"] == 2  # офлайн-чат + запуск навички
    assert modes["online"]["total_tokens"] > 0
    # Токени-вкладка також має розподіл по навичках (лише токени).
    me_skill = next(s for s in me["by_skill"] if s["skill"] == "Summarizer")
    assert me_skill["total_tokens"] > 0 and me_skill["runs"] == 1
    assert me["skills_total"]["total_tokens"] == me_skill["total_tokens"]

    money = client.get("/api/usage/money", headers=auth(token)).get_json()
    mm = {m["mode"]: m for m in money["by_mode"]}
    assert mm["online"]["cost_total"] > 0
    # Підсумок по навичках.
    st = money["skills_total"]
    assert st["runs"] == 1 and st["total_tokens"] > 0 and st["cost"] > 0
    row = next(s for s in money["by_skill"] if s["skill"] == "Summarizer")
    assert row["total_tokens"] == st["total_tokens"]
