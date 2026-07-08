"""Тести цін моделей, обліку вартості (USD) та аналітики (timeline / money)."""
from app.models import Model, Skill, User, TokenUsageLog
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
        "name": "Priced", "provider": "azure", "deployment_name": "gpt-4o",
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
        "name": "Bad", "provider": "azure", "deployment_name": "x",
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


# ---------- Область статистики (Admin): користувач / група ----------

def _make_group(client, admin_token, name, member_usernames):
    gid = client.post("/api/groups", headers=auth(admin_token),
                      json={"name": name}).get_json()["id"]
    for uname in member_usernames:
        uid = User.query.filter_by(username=uname).first().id
        client.post(f"/api/groups/{gid}/members", headers=auth(admin_token),
                    json={"user_id": uid})
    return gid


def test_money_scope_defaults_to_self(client):
    admin = login(client, "admin", "Admin123!")
    _chat_once(client, admin)
    res = client.get("/api/usage/money", headers=auth(admin)).get_json()
    assert res["scope"] == {"type": "user", "user_id":
                            User.query.filter_by(username="admin").first().id,
                            "username": "admin"}


def test_admin_can_scope_to_other_user(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    _chat_once(client, u1)
    uid = User.query.filter_by(username="u1").first().id
    res = client.get(f"/api/usage/money?user_id={uid}", headers=auth(admin)).get_json()
    assert res["scope"]["type"] == "user" and res["scope"]["username"] == "u1"
    assert res["cost_total"] > 0


def test_admin_can_scope_to_group_sum(client):
    admin = login(client, "admin", "Admin123!")
    u1, u2 = login(client, "u1", "pass"), login(client, "u2", "pass")
    _chat_once(client, u1)
    _chat_once(client, u2)
    _chat_once(client, u2)
    gid = _make_group(client, admin, "Команда", ["u1", "u2"])

    grp = client.get(f"/api/usage/money?group_id={gid}", headers=auth(admin)).get_json()
    assert grp["scope"] == {"type": "group", "group_id": gid,
                            "group": "Команда", "members": 2}
    # Сума статистик по учасниках дорівнює груповій.
    c1 = client.get(f"/api/usage/money?user_id={User.query.filter_by(username='u1').first().id}",
                    headers=auth(admin)).get_json()["cost_total"]
    c2 = client.get(f"/api/usage/money?user_id={User.query.filter_by(username='u2').first().id}",
                    headers=auth(admin)).get_json()["cost_total"]
    assert abs(grp["cost_total"] - (c1 + c2)) < 1e-9
    assert grp["requests"] == 3


def test_non_admin_cannot_scope_others(client):
    u1 = login(client, "u1", "pass")
    other = User.query.filter_by(username="u2").first().id
    assert client.get(f"/api/usage/money?user_id={other}",
                      headers=auth(u1)).status_code == 403
    assert client.get("/api/usage/money?group_id=1",
                      headers=auth(u1)).status_code == 403
    # Але власний user_id дозволено (це «сам користувач»).
    me = User.query.filter_by(username="u1").first().id
    assert client.get(f"/api/usage/money?user_id={me}",
                      headers=auth(u1)).status_code == 200


def test_scope_options_admin_only(client):
    admin = login(client, "admin", "Admin123!")
    _make_group(client, admin, "Група А", ["u1"])
    opts = client.get("/api/usage/scope-options", headers=auth(admin)).get_json()
    assert any(u["username"] == "u1" for u in opts["users"])
    assert any(g["name"] == "Група А" for g in opts["groups"])

    u1 = login(client, "u1", "pass")
    assert client.get("/api/usage/scope-options", headers=auth(u1)).status_code == 403
