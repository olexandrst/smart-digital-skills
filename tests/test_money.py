"""Тести цін моделей, обліку вартості (USD) та аналітики (timeline / money)."""
from app.models import Model, Skill, TokenUsageLog
from app.services import quota_service
from tests.conftest import login, auth


def _chat_once(client, token, content="привіт світ"):
    mid = Model.query.filter_by(name="gpt-4o").first().id
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    return client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                       json={"content": content})


# ---------- Ціни моделі ----------

def test_model_price_attributes(client):
    admin = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(admin), json={
        "name": "Priced", "provider": "openai", "deployment_name": "gpt-4o",
        "price_in": "10.58", "price_out": "42.0"})
    assert res.status_code == 201
    d = res.get_json()
    assert d["price_in"] == 10.58 and d["price_out"] == 42.0

    # Оновлення ціни.
    res = client.patch(f"/api/models/{d['id']}", headers=auth(admin),
                       json={"price_in": "3.5"})
    assert res.get_json()["price_in"] == 3.5

    # Некоректна ціна — помилка.
    assert client.post("/api/models", headers=auth(admin), json={
        "name": "Bad", "provider": "openai", "deployment_name": "x",
        "price_in": "abc"}).status_code == 400


def test_compute_cost():
    m = Model(name="m", deployment_name="d", price_in=1000.0, price_out=2000.0)
    cin, cout, ctot = quota_service.compute_cost(m, 1_000_000, 500_000)
    assert cin == 1000.0 and cout == 1000.0 and ctot == 2000.0
    # Без цін — вартість нуль.
    assert quota_service.compute_cost(Model(name="x", deployment_name="d"), 100, 100) == (0.0, 0.0, 0.0)


# ---------- Логування вартості + /usage/me ----------

def test_cost_logged_and_in_usage(client):
    token = login(client, "u1", "pass")
    assert _chat_once(client, token).status_code == 200

    log = TokenUsageLog.query.filter_by(is_system=False).first()
    assert log.cost_total > 0
    assert abs(log.cost_total - (log.cost_in + log.cost_out)) < 1e-9

    me = client.get("/api/usage/me", headers=auth(token)).get_json()
    assert me["cost_total"] > 0
    assert me["quota"]["used"] > 0  # квота витрачається у грошах


# ---------- Часова шкала (timeline) ----------

def test_timeline_buckets(client):
    token = login(client, "u1", "pass")
    _chat_once(client, token)
    _chat_once(client, token)
    res = client.get("/api/usage/timeline", headers=auth(token)).get_json()
    assert res["granularity"] in ("hour", "day", "week")
    assert res["buckets"] and res["from"] and res["to"]
    b = res["buckets"][-1]
    assert {"t", "prompt_tokens", "completion_tokens", "total_tokens", "cost_total"} <= set(b)


def test_timeline_empty(client):
    token = login(client, "u2", "pass")
    res = client.get("/api/usage/timeline", headers=auth(token)).get_json()
    assert res["buckets"] == [] and res["from"] is None


# ---------- Гроші за моделями (money) ----------

def test_money_breakdown_by_model(client):
    token = login(client, "u1", "pass")
    _chat_once(client, token)
    res = client.get("/api/usage/money", headers=auth(token)).get_json()
    assert res["cost_total"] > 0
    assert res["activity_from"] and res["activity_to"]
    assert any(m["model"] == "gpt-4o" and m["cost"] > 0 for m in res["by_model"])


def test_money_breakdown_by_skill(client):
    token = login(client, "u1", "pass")
    skill = Skill.query.filter_by(name="Summarizer").first()
    # Активуємо навичку для користувача, потім запускаємо її у чаті.
    assert client.post(f"/api/skills/{skill.id}/activate",
                       headers=auth(token)).status_code == 200
    mid = Model.query.filter_by(name="gpt-4o").first().id
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    assert client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                       json={"content": "стисни це", "skill_id": skill.id}).status_code == 200

    res = client.get("/api/usage/money", headers=auth(token)).get_json()
    row = next((s for s in res["by_skill"] if s["skill"] == "Summarizer"), None)
    assert row is not None
    assert row["runs"] >= 1
    assert row["cost"] > 0
    assert abs(row["avg"] - row["cost"] / row["runs"]) < 1e-9


def test_money_period_filter_excludes_future(client):
    token = login(client, "u1", "pass")
    _chat_once(client, token)
    # Період у майбутньому → 0 витрат.
    res = client.get("/api/usage/money?from=2099-01-01&to=2099-12-31",
                     headers=auth(token)).get_json()
    assert res["cost_total"] == 0
    # Але межі всієї активності лишаються заповненими.
    assert res["activity_from"] is not None
