"""Ресурси каталогу (промпти, інструкції, агенти, посилання) та «Обране»."""
from app.extensions import db
from app.models import CatalogResource, CatalogFavorite, Skill, User


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _make(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт аналітика",
               "description": "Розбір тендерної документації",
               "body": "Проаналізуй документ і виділи ризики.",
               "category": "Аналітика", "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=headers)


# ------------------------------- Створення -------------------------------

def test_admin_creates_prompt(client):
    h = _admin(client)
    res = _make(client, h)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    assert data["resource_type"] == "prompt"
    assert data["item_type"] == "resource"
    assert data["status"] == "published"
    assert data["published_at"]
    assert data["author"]  # автор проставляється автоматично


def test_skill_manager_may_create_plain_user_may_not(client):
    res = _make(client, _login(client, "sm"))
    assert res.status_code == 201
    res = _make(client, _login(client, "u1"))
    assert res.status_code == 403


def test_prompt_requires_body(client):
    res = _make(client, _admin(client), body="")
    assert res.status_code == 400
    assert res.get_json()["error"] == "validation_error"


def test_unknown_type_rejected(client):
    res = _make(client, _admin(client), resource_type="video")
    assert res.status_code == 400


def test_link_requires_url(client):
    h = _admin(client)
    res = _make(client, h, resource_type="link", name="Сервіс", body=None, url="")
    assert res.status_code == 400


def test_link_url_must_be_http_or_internal(client):
    h = _admin(client)
    bad = _make(client, h, resource_type="agent", name="Агент",
                url="javascript:alert(1)")
    assert bad.status_code == 400

    ok = _make(client, h, resource_type="agent", name="Агент",
               url="https://agents.example.com/a1")
    assert ok.status_code == 201
    assert ok.get_json()["link_scope"] == "external"

    internal = _make(client, h, resource_type="link", name="Портал",
                     url="/portal/ai", link_scope="internal")
    assert internal.status_code == 201
    assert internal.get_json()["link_scope"] == "internal"


def test_tags_normalized(client):
    res = _make(client, _admin(client), tags="RAG, , аналітика, RAG")
    assert res.get_json()["tags"] == ["RAG", "аналітика"]


# -------------------------------- Перегляд --------------------------------

def test_plain_user_sees_only_published(client):
    h = _admin(client)
    _make(client, h, name="Опублікований")
    _make(client, h, name="Чернетка", status="draft")

    names = [r["name"] for r in
             client.get("/api/catalog/resources", headers=_login(client, "u1")).get_json()]
    assert names == ["Опублікований"]

    all_names = {r["name"] for r in
                 client.get("/api/catalog/resources", headers=h).get_json()}
    assert all_names == {"Опублікований", "Чернетка"}


def test_type_filter(client):
    h = _admin(client)
    _make(client, h, name="П1")
    _make(client, h, resource_type="instruction", name="І1", body="# Як користуватись")

    items = client.get("/api/catalog/resources?type=instruction", headers=h).get_json()
    assert [r["name"] for r in items] == ["І1"]

    bad = client.get("/api/catalog/resources?type=nope", headers=h)
    assert bad.status_code == 400


def test_featured_first(client):
    h = _admin(client)
    _make(client, h, name="Звичайний")
    _make(client, h, name="Рекомендований", is_featured=True)
    items = client.get("/api/catalog/resources", headers=h).get_json()
    assert items[0]["name"] == "Рекомендований"


def test_draft_hidden_from_plain_user_detail(client):
    h = _admin(client)
    rid = _make(client, h, status="draft").get_json()["id"]
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=_login(client, "u1")).status_code == 403
    assert client.get(f"/api/catalog/resources/{rid}", headers=h).status_code == 200


# ----------------------- Редагування, публікація, видалення ----------------

def test_update_and_status_flow(client):
    h = _admin(client)
    rid = _make(client, h, status="draft").get_json()["id"]

    upd = client.patch(f"/api/catalog/resources/{rid}",
                       json={"name": "Нова назва", "category": "Закупівлі"}, headers=h)
    assert upd.status_code == 200
    assert upd.get_json()["name"] == "Нова назва"
    assert upd.get_json()["category"] == "Закупівлі"

    pub = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "published"}, headers=h)
    assert pub.status_code == 200
    assert pub.get_json()["published_at"]

    # «archived» — валідний статус життєвого циклу (BR-12); неіснуючий — ні.
    archived = client.post(f"/api/catalog/resources/{rid}/status",
                           json={"status": "archived"}, headers=h)
    assert archived.status_code == 200
    assert archived.get_json()["status"] == "archived"

    bad = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "retired"}, headers=h)
    assert bad.status_code == 400


def test_update_requires_manager(client):
    rid = _make(client, _admin(client)).get_json()["id"]
    res = client.patch(f"/api/catalog/resources/{rid}",
                       json={"name": "Хак"}, headers=_login(client, "u1"))
    assert res.status_code == 403


def test_delete_removes_resource_and_favorites(client):
    h = _admin(client)
    rid = _make(client, h).get_json()["id"]
    u1 = _login(client, "u1")
    client.post("/api/catalog/favorites",
                json={"item_type": "resource", "item_id": rid}, headers=u1)
    assert CatalogFavorite.query.filter_by(item_type="resource", item_id=rid).count() == 1

    assert client.delete(f"/api/catalog/resources/{rid}", headers=h).status_code == 200
    assert CatalogResource.query.get(rid) is None
    assert CatalogFavorite.query.filter_by(item_type="resource", item_id=rid).count() == 0


def test_open_counter(client):
    h = _admin(client)
    rid = _make(client, h, resource_type="link", name="Сервіс", body=None,
                url="https://example.com").get_json()["id"]
    u1 = _login(client, "u1")
    client.post(f"/api/catalog/resources/{rid}/open", headers=u1)
    res = client.post(f"/api/catalog/resources/{rid}/open", headers=u1)
    assert res.get_json()["opens_count"] == 2


# --------------------------------- Обране ---------------------------------

def test_favorite_toggle_for_resource_and_skill(client):
    h = _admin(client)
    rid = _make(client, h).get_json()["id"]
    skill_id = Skill.query.filter_by(name="Summarizer").first().id
    u1 = _login(client, "u1")

    on = client.post("/api/catalog/favorites",
                     json={"item_type": "resource", "item_id": rid}, headers=u1)
    assert on.get_json()["favorited"] is True
    client.post("/api/catalog/favorites",
                json={"item_type": "skill", "item_id": skill_id}, headers=u1)

    favs = client.get("/api/catalog/favorites", headers=u1).get_json()
    assert favs == {"skill": [skill_id], "resource": [rid]}

    off = client.post("/api/catalog/favorites",
                      json={"item_type": "resource", "item_id": rid}, headers=u1)
    assert off.get_json()["favorited"] is False
    assert client.get("/api/catalog/favorites", headers=u1).get_json()["resource"] == []


def test_favorites_are_per_user(client):
    h = _admin(client)
    rid = _make(client, h).get_json()["id"]
    client.post("/api/catalog/favorites",
                json={"item_type": "resource", "item_id": rid},
                headers=_login(client, "u1"))
    assert client.get("/api/catalog/favorites",
                      headers=_login(client, "u2")).get_json()["resource"] == []


def test_favorite_validation(client):
    u1 = _login(client, "u1")
    assert client.post("/api/catalog/favorites",
                       json={"item_type": "book", "item_id": 1},
                       headers=u1).status_code == 400
    assert client.post("/api/catalog/favorites",
                       json={"item_type": "resource", "item_id": 9999},
                       headers=u1).status_code == 404


def test_deleting_skill_clears_its_favorites(client):
    h = _admin(client)
    skill_id = Skill.query.filter_by(name="Summarizer").first().id
    client.post("/api/catalog/favorites",
                json={"item_type": "skill", "item_id": skill_id},
                headers=_login(client, "u1"))
    assert client.delete(f"/api/skills/{skill_id}", headers=h).status_code == 200
    assert CatalogFavorite.query.filter_by(item_type="skill",
                                           item_id=skill_id).count() == 0


def test_anonymous_access_denied(client):
    assert client.get("/api/catalog/resources").status_code == 401
    assert client.get("/api/catalog/favorites").status_code == 401
