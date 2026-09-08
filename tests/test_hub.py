"""AI Knowledge Hub: розділи, життєвий цикл, рейтинг, пошук та аналітика."""
from app.models import CatalogSection, CatalogResource, ReviewLog, SearchQueryLog, Skill


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _section(client, headers, **over):
    payload = {"name": "Toolbox", "description": "ШІ-інструменти", "icon_emoji": "🧰"}
    payload.update(over)
    return client.post("/api/catalog/sections", json=payload, headers=headers)


def _resource(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт",
               "description": "Опис", "body": "Текст", "category": "Аналітика",
               "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=headers)


# ------------------------------- Розділи -------------------------------

def test_admin_creates_section(client):
    res = _section(client, _admin(client))
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["name"] == "Toolbox"
    assert res.get_json()["icon_emoji"] == "🧰"
    assert res.get_json()["is_active"] is True


def test_duplicate_section_rejected(client):
    h = _admin(client)
    _section(client, h)
    assert _section(client, h, name="toolbox").status_code == 409


def test_plain_user_may_not_create_section(client):
    assert _section(client, _login(client, "u1")).status_code == 403


def test_section_external_url_validated(client):
    h = _admin(client)
    assert _section(client, h, name="Bad", url="javascript:alert(1)").status_code == 400
    ok = _section(client, h, name="Портал", url="/portal/knowledge")
    assert ok.status_code == 201
    assert ok.get_json()["url"] == "/portal/knowledge"


def test_section_counts_resources_and_skills(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    _resource(client, h, section_id=sid)

    skill = Skill.query.filter_by(name="Summarizer").first()
    client.patch(f"/api/skills/{skill.id}", json={"section_id": sid}, headers=h)

    sections = client.get("/api/catalog/sections", headers=h).get_json()
    assert next(s for s in sections if s["id"] == sid)["items_count"] == 2


def test_resource_filtered_by_section(client):
    h = _admin(client)
    a = _section(client, h, name="A").get_json()["id"]
    b = _section(client, h, name="B").get_json()["id"]
    _resource(client, h, name="У A", section_id=a)
    _resource(client, h, name="У B", section_id=b)

    items = client.get(f"/api/catalog/resources?section_id={a}", headers=h).get_json()
    assert [i["name"] for i in items] == ["У A"]


def test_deleting_section_keeps_resources(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    rid = _resource(client, h, section_id=sid).get_json()["id"]

    assert client.delete(f"/api/catalog/sections/{sid}", headers=h).status_code == 200
    assert CatalogSection.query.get(sid) is None
    assert CatalogResource.query.get(rid) is not None
    assert CatalogResource.query.get(rid).section_id is None


def test_unknown_section_rejected(client):
    assert _resource(client, _admin(client), section_id=9999).status_code == 404


# --------------------------- Життєвий цикл ---------------------------

def test_needs_update_visible_archived_hidden(client):
    h = _admin(client)
    stale = _resource(client, h, name="Застарілий").get_json()["id"]
    gone = _resource(client, h, name="Знятий").get_json()["id"]
    client.post(f"/api/catalog/resources/{stale}/status",
                json={"status": "needs_update"}, headers=h)
    client.post(f"/api/catalog/resources/{gone}/status",
                json={"status": "archived"}, headers=h)

    names = [r["name"] for r in
             client.get("/api/catalog/resources", headers=_login(client, "u1")).get_json()]
    assert "Застарілий" in names
    assert "Знятий" not in names

    # Менеджер бачить обидва.
    all_names = {r["name"] for r in
                 client.get("/api/catalog/resources", headers=h).get_json()}
    assert {"Застарілий", "Знятий"} <= all_names


def test_archived_detail_hidden_from_plain_user(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "archived"}, headers=h)
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=_login(client, "u1")).status_code == 403


def test_status_change_writes_review_log(client):
    h = _admin(client)
    rid = _resource(client, h, status="draft").get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "published", "note": "перевірено"}, headers=h)
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "needs_update"}, headers=h)

    log = client.get(f"/api/catalog/resources/{rid}/review-log", headers=h).get_json()
    assert [(e["from_status"], e["to_status"]) for e in log] == [
        ("published", "needs_update"), ("draft", "published")]
    assert log[1]["note"] == "перевірено"
    assert log[1]["actor_name"]


def test_owner_and_review_dates_round_trip(client):
    h = _admin(client)
    rid = _resource(client, h, owner="Марина К.", reuse_level="adaptable",
                    tools="Copilot Studio",
                    next_review_at="2020-01-01").get_json()["id"]
    r = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert r["owner"] == "Марина К."
    assert r["reuse_level"] == "adaptable"
    assert r["tools"] == "Copilot Studio"
    assert r["next_review_at"].startswith("2020-01-01")
    assert r["review_overdue"] is True   # дата в минулому


def test_invalid_date_rejected(client):
    assert _resource(client, _admin(client), next_review_at="не дата").status_code == 400


def test_strict_publish_blocks_incomplete_material(client):
    h = _admin(client)
    assert client.get("/api/catalog/settings", headers=h).get_json()["strict_publish"] is False

    client.post("/api/catalog/settings", json={"strict_publish": True}, headers=h)
    blocked = _resource(client, h, name="Неповний")
    assert blocked.status_code == 400
    assert blocked.get_json()["error"] == "publish_requirements"

    # Чернетка створюється попри сувору публікацію.
    assert _resource(client, h, name="Чернетка", status="draft").status_code == 201

    sid = _section(client, h).get_json()["id"]
    ok = _resource(client, h, name="Повний", section_id=sid, owner="Власник",
                   next_review_at="2030-01-01")
    assert ok.status_code == 201


def test_strict_publish_requires_manager(client):
    assert client.post("/api/catalog/settings", json={"strict_publish": True},
                       headers=_login(client, "u1")).status_code == 403


# ------------------------- Рейтинг і фідбек -------------------------

def test_resource_feedback_with_rating(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    u1 = _login(client, "u1")

    assert client.post(f"/api/catalog/resources/{rid}/feedback",
                       json={"rating": 5, "message": "Клас"}, headers=u1).status_code == 201
    assert client.post(f"/api/catalog/resources/{rid}/feedback",
                       json={"rating": 3}, headers=_login(client, "u2")).status_code == 201

    r = client.get(f"/api/catalog/resources/{rid}", headers=u1).get_json()
    assert r["rating_avg"] == 4.0
    assert r["rating_count"] == 2

    # Відгуки надходять до спільного інбоксу керування.
    inbox = client.get("/api/skills/feedback", headers=h).get_json()
    assert any(f["item_type"] == "resource" and f["rating"] == 5
               for f in inbox["items"])


def test_empty_feedback_rejected(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    assert client.post(f"/api/catalog/resources/{rid}/feedback", json={},
                       headers=_login(client, "u1")).status_code == 400


def test_rating_out_of_range_rejected(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    for bad in (0, 6, "п'ять"):
        assert client.post(f"/api/catalog/resources/{rid}/feedback",
                           json={"rating": bad},
                           headers=_login(client, "u1")).status_code == 400


def test_skill_feedback_accepts_rating(client):
    skill = Skill.query.filter_by(name="Summarizer").first()
    res = client.post(f"/api/skills/{skill.id}/feedback",
                      json={"rating": 4, "message": "Норм"},
                      headers=_login(client, "u1"))
    assert res.status_code == 201
    inbox = client.get("/api/skills/feedback", headers=_admin(client)).get_json()
    assert inbox["items"][0]["rating"] == 4
    assert inbox["items"][0]["item_type"] == "skill"


# --------------------------- Пошукові запити ---------------------------

def test_search_log_and_success_marking(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    u1 = _login(client, "u1")

    log = client.post("/api/catalog/search-log",
                      json={"query": "тендер", "results_count": 1}, headers=u1)
    assert log.status_code == 201
    log_id = log.get_json()["id"]

    opened = client.post(f"/api/catalog/search-log/{log_id}/opened",
                         json={"item_type": "resource", "item_id": rid}, headers=u1)
    assert opened.status_code == 200
    assert SearchQueryLog.query.get(log_id).opened_item_type == "resource"


def test_search_log_empty_query_rejected(client):
    assert client.post("/api/catalog/search-log", json={"query": "  "},
                       headers=_login(client, "u1")).status_code == 400


def test_cannot_mark_someone_elses_search(client):
    log_id = client.post("/api/catalog/search-log",
                         json={"query": "щось", "results_count": 0},
                         headers=_login(client, "u1")).get_json()["id"]
    assert client.post(f"/api/catalog/search-log/{log_id}/opened",
                       json={"item_type": "resource", "item_id": 1},
                       headers=_login(client, "u2")).status_code == 403


# ------------------------------ Аналітика ------------------------------

def test_analytics_report(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    rid = _resource(client, h, section_id=sid, owner="Власник").get_json()["id"]
    _resource(client, h, name="Кейс", resource_type="case", body="Опис кейсу",
              section_id=sid)
    u1 = _login(client, "u1")

    client.post(f"/api/catalog/resources/{rid}/feedback",
                json={"rating": 5}, headers=u1)
    client.post(f"/api/catalog/resources/{rid}/open", headers=u1)
    hit = client.post("/api/catalog/search-log",
                      json={"query": "промпт", "results_count": 2}, headers=u1).get_json()
    client.post(f"/api/catalog/search-log/{hit['id']}/opened",
                json={"item_type": "resource", "item_id": rid}, headers=u1)
    client.post("/api/catalog/search-log",
                json={"query": "мультиагентність", "results_count": 0}, headers=u1)

    a = client.get("/api/catalog/analytics", headers=h).get_json()
    assert a["by_type"]["prompt"] == 1 and a["by_type"]["case"] == 1
    assert a["by_type"]["skill"] >= 1
    assert next(s for s in a["by_section"] if s["id"] == sid)["resources"] == 2
    assert a["freshness"]["published"] == 2
    assert a["freshness"]["no_owner"] == 1     # кейс без власника
    assert a["top_opened"][0]["id"] == rid
    assert a["rating"]["avg"] == 5.0
    assert a["search"]["total"] == 2
    assert a["search"]["success_rate"] == 50.0
    assert a["search"]["no_results_rate"] == 50.0
    assert [q["query"] for q in a["search"]["no_results"]] == ["мультиагентність"]


def test_analytics_requires_manager(client):
    assert client.get("/api/catalog/analytics",
                      headers=_login(client, "u1")).status_code == 403


def test_case_type_requires_body(client):
    assert _resource(client, _admin(client), resource_type="case",
                     name="Кейс", body="").status_code == 400


# ---------------------------- MCP-сервери ----------------------------

def _mcp(client, headers, **over):
    payload = {"resource_type": "mcp", "name": "MCP: База знань",
               "description": "Доступ агентів до регламентів компанії",
               "url": "https://mcp.example.com/sse", "category": "Внутрішні ресурси",
               "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=headers)


def test_mcp_requires_endpoint(client):
    res = _mcp(client, _admin(client), url="")
    assert res.status_code == 400
    assert "endpoint" in res.get_json()["message"]


def test_mcp_created_without_body(client):
    """Інструкція підключення бажана, але endpoint самодостатній."""
    res = _mcp(client, _admin(client))
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    assert data["resource_type"] == "mcp"
    assert data["url"] == "https://mcp.example.com/sse"
    assert data["link_scope"] == "external"   # проставляється автоматично
    assert data["body"] is None


def test_mcp_keeps_body_and_scope(client):
    res = _mcp(client, _admin(client), link_scope="internal",
               body="## Як підключити\n\n1. Скопіюйте endpoint.")
    assert res.status_code == 201
    assert res.get_json()["link_scope"] == "internal"
    assert res.get_json()["body"].startswith("## Як підключити")


def test_mcp_endpoint_must_be_valid_url(client):
    assert _mcp(client, _admin(client),
                url="javascript:alert(1)").status_code == 400


def test_mcp_type_filter(client):
    h = _admin(client)
    _mcp(client, h)
    _resource(client, h, name="Звичайний промпт")

    items = client.get("/api/catalog/resources?type=mcp", headers=h).get_json()
    assert [i["name"] for i in items] == ["MCP: База знань"]


def test_mcp_counted_in_analytics(client):
    h = _admin(client)
    _mcp(client, h)
    a = client.get("/api/catalog/analytics", headers=h).get_json()
    assert a["by_type"]["mcp"] == 1
