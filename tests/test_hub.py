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


# ------------------------------ Колекції ------------------------------

def _folder(client, headers, section_id, **over):
    payload = {"name": "Use Cases", "section_id": section_id,
               "description": "Кейси застосування AI", "icon_emoji": "💼"}
    payload.update(over)
    return client.post("/api/catalog/folders", json=payload, headers=headers)


def test_folder_crud(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]

    res = _folder(client, h, sid)
    assert res.status_code == 201, res.get_json()
    fid = res.get_json()["id"]
    assert res.get_json()["section_name"] == "Toolbox"

    patched = client.patch(f"/api/catalog/folders/{fid}",
                           json={"name": "Практичні кейси", "position": 3},
                           headers=h)
    assert patched.status_code == 200
    assert patched.get_json()["name"] == "Практичні кейси"
    assert patched.get_json()["position"] == 3

    assert client.delete(f"/api/catalog/folders/{fid}", headers=h).status_code == 200
    assert client.get("/api/catalog/folders", headers=h).get_json() == []


def test_folder_requires_section(client):
    h = _admin(client)
    res = client.post("/api/catalog/folders", json={"name": "Без розділу"}, headers=h)
    assert res.status_code == 400
    assert "розділ" in res.get_json()["message"]


def test_folder_name_unique_within_section_only(client):
    """Однакові назви в різних розділах дозволені, у межах одного — ні."""
    h = _admin(client)
    first = _section(client, h).get_json()["id"]
    second = _section(client, h, name="Learning Space").get_json()["id"]

    assert _folder(client, h, first).status_code == 201
    assert _folder(client, h, first, name="use cases").status_code == 409
    assert _folder(client, h, second).status_code == 201


def test_plain_user_may_not_create_folder(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    assert _folder(client, _login(client, "u1"), sid).status_code == 403


def test_folder_sets_section_of_resource(client):
    """Колекція задає розділ: шлях «розділ → колекція → картка» не суперечливий."""
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    other = _section(client, h, name="Insight Center").get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]

    created = _resource(client, h, folder_id=fid, section_id=other)
    assert created.status_code == 201
    assert created.get_json()["section_id"] == sid
    assert created.get_json()["folder_name"] == "Use Cases"


def test_changing_section_detaches_foreign_folder(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    other = _section(client, h, name="Insight Center").get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]
    rid = _resource(client, h, folder_id=fid).get_json()["id"]

    moved = client.patch(f"/api/catalog/resources/{rid}",
                         json={"section_id": other}, headers=h)
    assert moved.status_code == 200
    assert moved.get_json()["section_id"] == other
    assert moved.get_json()["folder_id"] is None


def test_deleting_folder_keeps_resources(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]
    rid = _resource(client, h, folder_id=fid).get_json()["id"]

    client.delete(f"/api/catalog/folders/{fid}", headers=h)
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["folder_id"] is None
    assert item["section_id"] == sid          # матеріал лишився в розділі


def test_deleting_section_removes_its_folders(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]
    rid = _resource(client, h, folder_id=fid).get_json()["id"]

    client.delete(f"/api/catalog/sections/{sid}", headers=h)
    assert client.get("/api/catalog/folders", headers=h).get_json() == []
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["folder_id"] is None and item["section_id"] is None
    assert fid  # колекція існувала до видалення розділу


def test_moving_folder_moves_its_resources(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    other = _section(client, h, name="Insight Center").get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]
    rid = _resource(client, h, folder_id=fid).get_json()["id"]

    assert client.patch(f"/api/catalog/folders/{fid}", json={"section_id": other},
                        headers=h).status_code == 200
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["section_id"] == other and item["folder_id"] == fid


def test_folder_counts_resources(client):
    h = _admin(client)
    sid = _section(client, h).get_json()["id"]
    fid = _folder(client, h, sid).get_json()["id"]
    _resource(client, h, folder_id=fid)
    _resource(client, h, name="Чернетка", folder_id=fid, status="draft")

    folders = client.get("/api/catalog/folders", headers=h).get_json()
    assert folders[0]["items_count"] == 2          # менеджер бачить і чернетку
    plain = client.get("/api/catalog/folders", headers=_login(client, "u1")).get_json()
    assert plain[0]["items_count"] == 1


# ------------------------ Довідники метаданих ------------------------

def _seed_terms(app):
    """Наповнює довідники так, як це робить старт застосунку."""
    from app.core.schema import seed_catalog_terms
    with app.app_context():
        seed_catalog_terms()


def _term(client, headers, kind, name, **over):
    payload = {"kind": kind, "name": name}
    payload.update(over)
    return client.post("/api/catalog/terms", json=payload, headers=headers)


def _terms_of(client, headers, kind):
    return client.get(f"/api/catalog/terms?kind={kind}", headers=headers).get_json()


def test_default_terms_seeded(client, app):
    _seed_terms(app)
    h = _admin(client)
    assert len(_terms_of(client, h, "complexity")) == 3
    assert len(_terms_of(client, h, "business_value")) == 4
    types = _terms_of(client, h, "material_type")
    assert {t["code"] for t in types} == {"prompt", "instruction", "case",
                                          "agent", "mcp", "link"}


def test_seeding_terms_is_idempotent(client, app):
    _seed_terms(app)
    _seed_terms(app)
    assert len(_terms_of(client, _admin(client), "complexity")) == 3


def test_term_crud_and_duplicate_guard(client):
    h = _admin(client)
    created = _term(client, h, "complexity", "Базовий")
    assert created.status_code == 201
    tid = created.get_json()["id"]

    assert _term(client, h, "complexity", " базовий ").status_code == 409
    renamed = client.patch(f"/api/catalog/terms/{tid}", json={"name": "Початковий"},
                           headers=h)
    assert renamed.status_code == 200
    assert renamed.get_json()["name"] == "Початковий"
    assert client.delete(f"/api/catalog/terms/{tid}", headers=h).status_code == 200


def test_unknown_term_kind_rejected(client):
    assert _term(client, _admin(client), "colour", "Синій").status_code == 400


def test_plain_user_may_not_manage_terms(client):
    assert _term(client, _login(client, "u1"), "tag", "RAG").status_code == 403


def test_material_types_not_addable_via_dictionary(client, app):
    """Вид матеріалу задає поведінку коду — через довідник його не додати."""
    _seed_terms(app)
    h = _admin(client)
    res = _term(client, h, "material_type", "Відеокурс")
    assert res.status_code == 400
    type_term = _terms_of(client, h, "material_type")[0]
    assert client.delete(f"/api/catalog/terms/{type_term['id']}",
                         headers=h).status_code == 400
    renamed = client.patch(f"/api/catalog/terms/{type_term['id']}",
                           json={"name": "Готові промпти"}, headers=h)
    assert renamed.status_code == 200     # назву міняти можна


def test_renaming_term_shows_in_cards(client):
    h = _admin(client)
    tid = _term(client, h, "complexity", "Базовий").get_json()["id"]
    rid = _resource(client, h, complexity_id=tid).get_json()["id"]

    client.patch(f"/api/catalog/terms/{tid}", json={"name": "Початковий"}, headers=h)
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["complexity_name"] == "Початковий"


def test_complexity_outside_dictionary_rejected(client):
    h = _admin(client)
    tag = _term(client, h, "tag", "RAG").get_json()["id"]
    assert _resource(client, h, complexity_id=9999).status_code == 400
    # Термін іншого довідника теж не приймається.
    assert _resource(client, h, name="Інший", complexity_id=tag).status_code == 400


def test_tags_create_dictionary_entries(client):
    h = _admin(client)
    created = _resource(client, h, tags="RAG, Аналітика")
    assert created.get_json()["tags"] == ["RAG", "Аналітика"]
    assert {t["name"] for t in _terms_of(client, h, "tag")} == {"RAG", "Аналітика"}


def test_tags_deduplicated_by_case_and_spaces(client):
    h = _admin(client)
    created = _resource(client, h, tags="RAG,  rag , RAG  ")
    assert created.get_json()["tags"] == ["RAG"]
    assert len(_terms_of(client, h, "tag")) == 1


def test_tags_replaced_on_update(client):
    h = _admin(client)
    rid = _resource(client, h, tags="RAG, Аналітика").get_json()["id"]
    updated = client.patch(f"/api/catalog/resources/{rid}",
                           json={"tags": "Аналітика"}, headers=h)
    assert updated.get_json()["tags"] == ["Аналітика"]


def test_renaming_tag_shows_in_cards(client):
    h = _admin(client)
    rid = _resource(client, h, tags="RAG").get_json()["id"]
    tid = _terms_of(client, h, "tag")[0]["id"]

    client.patch(f"/api/catalog/terms/{tid}", json={"name": "Retrieval"}, headers=h)
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["tags"] == ["Retrieval"]


def test_merging_tags_moves_cards_without_duplicates(client):
    h = _admin(client)
    both = _resource(client, h, tags="RAG, Пошук").get_json()["id"]
    one = _resource(client, h, name="Другий", tags="RAG").get_json()["id"]
    terms = {t["name"]: t["id"] for t in _terms_of(client, h, "tag")}

    merged = client.post(f"/api/catalog/terms/{terms['RAG']}/merge",
                         json={"into": terms["Пошук"]}, headers=h)
    assert merged.status_code == 200
    assert [t["name"] for t in _terms_of(client, h, "tag")] == ["Пошук"]
    assert client.get(f"/api/catalog/resources/{both}",
                      headers=h).get_json()["tags"] == ["Пошук"]
    assert client.get(f"/api/catalog/resources/{one}",
                      headers=h).get_json()["tags"] == ["Пошук"]


def test_merging_complexity_moves_cards(client):
    h = _admin(client)
    src = _term(client, h, "complexity", "Складний").get_json()["id"]
    dst = _term(client, h, "complexity", "Просунутий").get_json()["id"]
    rid = _resource(client, h, complexity_id=src).get_json()["id"]

    assert client.post(f"/api/catalog/terms/{src}/merge", json={"into": dst},
                       headers=h).status_code == 200
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=h).get_json()["complexity_id"] == dst


def test_merging_across_dictionaries_rejected(client):
    h = _admin(client)
    tag = _term(client, h, "tag", "RAG").get_json()["id"]
    level = _term(client, h, "complexity", "Базовий").get_json()["id"]
    assert client.post(f"/api/catalog/terms/{tag}/merge", json={"into": level},
                       headers=h).status_code == 400


def test_deleting_term_clears_it_in_cards(client):
    h = _admin(client)
    tid = _term(client, h, "complexity", "Базовий").get_json()["id"]
    rid = _resource(client, h, complexity_id=tid).get_json()["id"]

    client.delete(f"/api/catalog/terms/{tid}", headers=h)
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=h).get_json()["complexity_id"] is None


def test_legacy_tags_migrated_into_dictionary(client, app):
    """Старі теги рядком через кому переносяться в довідник без втрат і дублів."""
    from app.extensions import db
    from app.models import CatalogResource
    from app.core.schema import migrate_legacy_tags

    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    with app.app_context():
        # Імітуємо стан бази до появи довідника.
        CatalogResource.query.filter_by(id=rid).update(
            {CatalogResource.tags: "RAG, аналітика ,  RAG , Copilot"})
        db.session.commit()
        migrate_legacy_tags()
        migrate_legacy_tags()        # повторний запуск нічого не дублює

    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert sorted(item["tags"]) == ["Copilot", "RAG", "аналітика"]
    assert len(_terms_of(client, h, "tag")) == 3


def test_migration_skips_resources_with_tags_already_linked(client, app):
    from app.extensions import db
    from app.models import CatalogResource
    from app.core.schema import migrate_legacy_tags

    h = _admin(client)
    rid = _resource(client, h, tags="Актуальний").get_json()["id"]
    with app.app_context():
        CatalogResource.query.filter_by(id=rid).update(
            {CatalogResource.tags: "Застарілий"})
        db.session.commit()
        migrate_legacy_tags()

    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=h).get_json()["tags"] == ["Актуальний"]


# ------------------------- Фільтри каталогу -------------------------

def _names(client, headers, query=""):
    res = client.get(f"/api/catalog/resources{query}", headers=headers)
    assert res.status_code == 200, res.get_json()
    return sorted(i["name"] for i in res.get_json())


def _filter_fixture(client, headers):
    """Два матеріали, що різняться кожним атрибутом фільтрації."""
    sid = _section(client, headers).get_json()["id"]
    other_section = _section(client, headers, name="Insight Center").get_json()["id"]
    fid = _folder(client, headers, sid).get_json()["id"]
    other_folder = _folder(client, headers, other_section,
                           name="AI Solutions").get_json()["id"]
    easy = _term(client, headers, "complexity", "Базовий").get_json()["id"]
    hard = _term(client, headers, "complexity", "Просунутий").get_json()["id"]
    time_saved = _term(client, headers, "business_value", "Економія часу").get_json()["id"]
    quality = _term(client, headers, "business_value", "Якість рішень").get_json()["id"]

    _resource(client, headers, name="Альфа", folder_id=fid, complexity_id=easy,
              business_value_id=time_saved, reuse_level="ready", tools="Copilot",
              owner="Іваненко", tags="RAG", category="Аналітика")
    _resource(client, headers, name="Бета", folder_id=other_folder,
              complexity_id=hard, business_value_id=quality, reuse_level="reference",
              tools="Power Automate", owner="Петренко", tags="Автоматизація",
              category="Автоматизація")
    return {"section": sid, "folder": fid, "complexity": easy,
            "business_value": time_saved, "other_section": other_section}


def test_filter_by_folder(client):
    h = _admin(client)
    ids = _filter_fixture(client, h)
    assert _names(client, h, f"?folder_id={ids['folder']}") == ["Альфа"]


def test_filter_by_section(client):
    h = _admin(client)
    ids = _filter_fixture(client, h)
    assert _names(client, h, f"?section_id={ids['section']}") == ["Альфа"]


def test_filter_by_complexity(client):
    h = _admin(client)
    ids = _filter_fixture(client, h)
    assert _names(client, h, f"?complexity_id={ids['complexity']}") == ["Альфа"]


def test_filter_by_business_value(client):
    h = _admin(client)
    ids = _filter_fixture(client, h)
    assert _names(client, h,
                  f"?business_value_id={ids['business_value']}") == ["Альфа"]


def test_filter_by_reuse_level(client):
    h = _admin(client)
    _filter_fixture(client, h)
    assert _names(client, h, "?reuse_level=ready") == ["Альфа"]
    assert client.get("/api/catalog/resources?reuse_level=maybe",
                      headers=h).status_code == 400


def test_filter_by_tool(client):
    h = _admin(client)
    _filter_fixture(client, h)
    assert _names(client, h, "?tool=copilot") == ["Альфа"]      # без урахування регістру


def test_filter_by_owner(client):
    h = _admin(client)
    _filter_fixture(client, h)
    assert _names(client, h, "?owner=Петренко") == ["Бета"]


def test_filter_by_tag(client):
    h = _admin(client)
    _filter_fixture(client, h)
    tag = next(t for t in _terms_of(client, h, "tag") if t["name"] == "RAG")
    assert _names(client, h, f"?tag_id={tag['id']}") == ["Альфа"]


def test_filter_by_type(client):
    h = _admin(client)
    _filter_fixture(client, h)
    _resource(client, h, name="Посилання", resource_type="link",
              url="https://example.com", body="")
    assert _names(client, h, "?type=link") == ["Посилання"]


def test_filter_by_status(client):
    h = _admin(client)
    _filter_fixture(client, h)
    _resource(client, h, name="Чернетка", status="draft")
    assert _names(client, h, "?status=draft") == ["Чернетка"]
    assert client.get("/api/catalog/resources?status=deleted",
                      headers=h).status_code == 400


def test_plain_user_may_not_filter_hidden_statuses(client):
    h = _admin(client)
    _resource(client, h, name="Чернетка", status="draft")
    user = _login(client, "u1")
    assert client.get("/api/catalog/resources?status=draft",
                      headers=user).status_code == 403
    assert _names(client, user, "?status=published") == []


def test_filter_by_text_query(client):
    h = _admin(client)
    _filter_fixture(client, h)
    assert _names(client, h, "?q=льф") == ["Альфа"]
    assert _names(client, h, "?q=Петренко") == ["Бета"]


def test_filters_combine(client):
    h = _admin(client)
    ids = _filter_fixture(client, h)
    assert _names(client, h,
                  f"?section_id={ids['section']}&reuse_level=ready&tool=Copilot"
                  ) == ["Альфа"]
    # Комбінація, що не має спільного результату, повертає порожній список.
    assert _names(client, h,
                  f"?section_id={ids['section']}&reuse_level=reference") == []


def test_filter_by_unknown_folder_is_not_found(client):
    assert client.get("/api/catalog/resources?folder_id=9999",
                      headers=_admin(client)).status_code == 404


# ------------ Повторне використання та вітрина (AKH-04, AKH-05) ------------

def test_reuse_guidance_saved_and_returned(client):
    h = _admin(client)
    rid = _resource(client, h, reuse_level="adaptable",
                    owner="Марина Кондратенко",
                    owner_contact="hr@example.com",
                    reuse_guidance="### Кроки\n\n1. Зберіть звернення.").get_json()["id"]
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["reuse_guidance"].startswith("### Кроки")
    assert item["owner_contact"] == "hr@example.com"
    assert item["reuse_level"] == "adaptable"


def test_reuse_guidance_cleared_by_empty_value(client):
    h = _admin(client)
    rid = _resource(client, h, reuse_guidance="Текст").get_json()["id"]
    updated = client.patch(f"/api/catalog/resources/{rid}",
                           json={"reuse_guidance": "   "}, headers=h)
    assert updated.get_json()["reuse_guidance"] is None


def test_showcase_blocks_for_plain_user(client):
    h = _admin(client)
    _resource(client, h, name="Рекомендований", is_featured=True)
    _resource(client, h, name="Звичайний")
    blocks = client.get("/api/catalog/showcase", headers=_login(client, "u1")).get_json()

    keys = [b["key"] for b in blocks]
    assert "featured" in keys and "recent" in keys
    assert "needs_update" not in keys          # блок стану контенту — не для всіх
    featured = next(b for b in blocks if b["key"] == "featured")
    assert [i["name"] for i in featured["items"]] == ["Рекомендований"]


def test_showcase_hides_empty_blocks(client):
    """Порожній блок не повертається — фронт не малює рамку без вмісту."""
    h = _admin(client)
    _resource(client, h)
    keys = [b["key"] for b in client.get("/api/catalog/showcase", headers=h).get_json()]
    assert "featured" not in keys              # рекомендованих немає
    assert "popular" not in keys               # відкриттів ще не було
    assert "recent" in keys


def test_showcase_popular_counts_opens(client):
    h = _admin(client)
    rid = _resource(client, h, name="Популярний").get_json()["id"]
    _resource(client, h, name="Непопулярний")
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    blocks = client.get("/api/catalog/showcase", headers=h).get_json()
    popular = next(b for b in blocks if b["key"] == "popular")
    assert [i["name"] for i in popular["items"]] == ["Популярний"]


def test_showcase_needs_update_visible_to_manager(client):
    h = _admin(client)
    rid = _resource(client, h, name="Застарілий").get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "needs_update"}, headers=h)

    blocks = client.get("/api/catalog/showcase", headers=h).get_json()
    stale = next(b for b in blocks if b["key"] == "needs_update")
    assert [i["name"] for i in stale["items"]] == ["Застарілий"]
    assert stale["manager_only"] is True


def test_showcase_skips_drafts(client):
    h = _admin(client)
    _resource(client, h, name="Чернетка", status="draft")
    blocks = client.get("/api/catalog/showcase", headers=h).get_json()
    names = [i["name"] for b in blocks for i in b["items"]]
    assert "Чернетка" not in names
