"""Воронка ідей: подання, життєвий цикл, права, маршрутизація (BR-15, FR-10)."""
from app.models import Idea, ReviewLog


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _idea(client, headers, **over):
    payload = {"title": "Автоскладання протоколів нарад",
               "body": "Агент слухає нараду і формує протокол із задачами.",
               "problem": "Протоколи пишуть вручну по годині після кожної наради.",
               "expected_effect": "Економія ~6 годин на тиждень на відділ.",
               "contact": "ivanenko@example.com"}
    payload.update(over)
    return client.post("/api/ideas", json=payload, headers=headers)


def _resource(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=headers)


# ------------------------------ Подання ------------------------------

def test_any_user_may_submit_idea(client):
    res = _idea(client, _login(client, "u1"))
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    assert data["status"] == "submitted"
    assert data["author_name"] == "u1"


def test_idea_requires_title_and_body(client):
    h = _login(client, "u1")
    assert _idea(client, h, title="").status_code == 400
    assert _idea(client, h, body="  ").status_code == 400


def test_anonymous_may_not_submit_idea(client):
    assert client.post("/api/ideas", json={"title": "x", "body": "y"}).status_code == 401


def test_idea_may_reference_source_material(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    res = _idea(client, h, source_resource_id=rid)
    assert res.status_code == 201
    assert res.get_json()["source_resource_name"] == "Промпт"


def test_idea_rejects_unknown_source_material(client):
    assert _idea(client, _admin(client), source_resource_id=9999).status_code == 404


# --------------------------- Видимість ---------------------------

def test_author_sees_only_own_ideas(client):
    _idea(client, _login(client, "u1"))
    _idea(client, _login(client, "u2"), title="Інша ідея")

    mine = client.get("/api/ideas", headers=_login(client, "u1")).get_json()
    assert [i["title"] for i in mine] == ["Автоскладання протоколів нарад"]


def test_manager_sees_all_ideas(client):
    _idea(client, _login(client, "u1"))
    _idea(client, _login(client, "u2"), title="Інша ідея")

    all_ideas = client.get("/api/ideas", headers=_admin(client)).get_json()
    assert len(all_ideas) == 2


def test_manager_can_filter_to_own_ideas(client):
    _idea(client, _login(client, "u1"))
    h = _admin(client)
    _idea(client, h, title="Ідея адміна")
    assert [i["title"] for i in client.get("/api/ideas?mine=1", headers=h).get_json()] \
        == ["Ідея адміна"]


def test_user_may_not_read_foreign_idea(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.get(f"/api/ideas/{iid}",
                      headers=_login(client, "u2")).status_code == 403
    assert client.get(f"/api/ideas/{iid}", headers=_admin(client)).status_code == 200


# -------------------------- Життєвий цикл --------------------------

def test_manager_changes_status_with_note(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    res = client.post(f"/api/ideas/{iid}/status",
                      json={"status": "in_review", "note": "Беремо на найближчий комітет"},
                      headers=_admin(client))
    assert res.status_code == 200
    assert res.get_json()["status"] == "in_review"
    assert res.get_json()["status_note"] == "Беремо на найближчий комітет"


def test_author_sees_decision_on_own_idea(client):
    h = _login(client, "u1")
    iid = _idea(client, h).get_json()["id"]
    client.post(f"/api/ideas/{iid}/status",
                json={"status": "rejected", "note": "Дублює наявний агент"},
                headers=_admin(client))

    mine = client.get("/api/ideas", headers=h).get_json()[0]
    assert mine["status"] == "rejected"
    assert mine["status_note"] == "Дублює наявний агент"


def test_closing_status_requires_note(client):
    """Автор має розуміти рішення, а не бачити лише нову позначку."""
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    h = _admin(client)
    for status in ("rejected", "implemented"):
        res = client.post(f"/api/ideas/{iid}/status", json={"status": status}, headers=h)
        assert res.status_code == 400, status
        assert "коментар" in res.get_json()["message"].lower()


def test_open_status_does_not_require_note(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.post(f"/api/ideas/{iid}/status", json={"status": "in_review"},
                       headers=_admin(client)).status_code == 200


def test_unknown_status_rejected(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.post(f"/api/ideas/{iid}/status", json={"status": "maybe"},
                       headers=_admin(client)).status_code == 400


def test_plain_user_may_not_change_status(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.post(f"/api/ideas/{iid}/status", json={"status": "accepted"},
                       headers=_login(client, "u1")).status_code == 403


def test_status_changes_are_logged(client, app):
    h = _login(client, "u1")
    iid = _idea(client, h).get_json()["id"]
    client.post(f"/api/ideas/{iid}/status", json={"status": "in_review"},
                headers=_admin(client))
    client.post(f"/api/ideas/{iid}/status",
                json={"status": "implemented", "note": "Зробили"}, headers=_admin(client))

    log = client.get(f"/api/ideas/{iid}/log", headers=h).get_json()
    assert [r["to_status"] for r in log] == ["implemented", "in_review", "submitted"]
    with app.app_context():
        assert ReviewLog.query.filter_by(item_type="idea").count() == 3


def test_log_hidden_from_other_users(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.get(f"/api/ideas/{iid}/log",
                      headers=_login(client, "u2")).status_code == 403


# ------------------------- Маршрутизація -------------------------

def test_implemented_idea_links_to_resource(client):
    h = _admin(client)
    rid = _resource(client, h, name="Агент протоколів").get_json()["id"]
    iid = _idea(client, _login(client, "u1")).get_json()["id"]

    res = client.post(f"/api/ideas/{iid}/status",
                      json={"status": "implemented", "note": "Готово", "resource_id": rid},
                      headers=h)
    assert res.status_code == 200
    assert res.get_json()["resource_name"] == "Агент протоколів"


def test_manager_routes_idea_to_external_process(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    res = client.patch(f"/api/ideas/{iid}",
                       json={"external_url": "https://ideas.example.com/i/42"},
                       headers=_admin(client))
    assert res.status_code == 200
    assert res.get_json()["external_url"] == "https://ideas.example.com/i/42"


def test_external_url_validated(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.patch(f"/api/ideas/{iid}", json={"external_url": "javascript:alert(1)"},
                        headers=_admin(client)).status_code == 400


def test_plain_user_may_not_route_idea(client):
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    assert client.patch(f"/api/ideas/{iid}", json={"external_url": "https://e.com"},
                        headers=_login(client, "u1")).status_code == 403


# ----------------------------- Черга -----------------------------

def test_stats_count_by_status(client):
    h = _admin(client)
    first = _idea(client, _login(client, "u1")).get_json()["id"]
    _idea(client, _login(client, "u2"), title="Друга")
    client.post(f"/api/ideas/{first}/status",
                json={"status": "rejected", "note": "Ні"}, headers=h)

    stats = client.get("/api/ideas/stats", headers=h).get_json()
    assert stats["total"] == 2
    assert stats["open"] == 1                      # відхилена вибула з черги
    assert stats["by_status"]["rejected"] == 1


def test_open_filter_excludes_closed_ideas(client):
    h = _admin(client)
    iid = _idea(client, _login(client, "u1")).get_json()["id"]
    _idea(client, _login(client, "u2"), title="Жива")
    client.post(f"/api/ideas/{iid}/status",
                json={"status": "implemented", "note": "Зробили"}, headers=h)

    assert [i["title"] for i in client.get("/api/ideas?status=open", headers=h).get_json()] \
        == ["Жива"]


def test_stats_hidden_from_plain_user(client):
    assert client.get("/api/ideas/stats", headers=_login(client, "u1")).status_code == 403
