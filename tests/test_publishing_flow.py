"""Пост-модерація: користувач публікує сам, менеджер каталогу модерує.

Користувач публікує матеріал чи навичку одразу — менеджери отримують
сповіщення. Менеджер може відредагувати, зняти з публікації, перенести в
архів чи видалити будь-що — автор дізнається про це сповіщенням із причиною.
"""
import io
import zipfile

from app.extensions import db
from app.models import CatalogResource, Notification, ReviewLog, Skill, SkillFeedback, User


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _material(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт для звітів",
               "description": "Опис", "body": "Текст промпту", "status": "published"}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _skill(client, headers, status="published", name="Протокол наради"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.md", f"---\nname: {name}\ndescription: Опис\nversion: 1.0.0\n---\n# Як\n")
    res = client.post("/api/skills/upload", headers=headers,
                      data={"file": (io.BytesIO(buf.getvalue()), "s.zip"), "status": status},
                      content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _notes(client, headers):
    return client.get("/api/catalog/notifications", headers=headers).get_json()


def _kinds(client, headers):
    return [n["kind"] for n in _notes(client, headers)["items"]]


# ------------------------------- Публікація -------------------------------

def test_user_publication_notifies_every_manager(client):
    u1 = _login(client, "u1")
    rid = _material(client, u1)["id"]
    for manager in (_admin(client), _login(client, "sm")):
        notes = _notes(client, manager)
        assert notes["unread"] == 1
        note = notes["items"][0]
        assert note["kind"] == "content_published"
        assert note["resource_id"] == rid
        assert "Промпт для звітів" in note["body"]
    # Ні сам автор, ні інші користувачі сповіщень не отримують.
    assert _notes(client, u1)["items"] == []
    assert _notes(client, _login(client, "u2"))["items"] == []


def test_draft_is_silent_until_author_publishes_it(client):
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _material(client, u1, status="draft")["id"]
    assert _notes(client, admin)["items"] == []
    res = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "published"}, headers=u1)
    assert res.get_json()["status"] == "published"
    assert _kinds(client, admin) == ["content_published"]


def test_manager_publication_does_not_notify_other_managers(client):
    _material(client, _login(client, "sm"))
    assert _notes(client, _admin(client))["items"] == []


def test_author_edit_of_published_material_notifies_once_a_day(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    client.post("/api/catalog/notifications/read", json={}, headers=sm)
    for text in ("Нова версія промпту", "Ще одна правка"):
        assert client.patch(f"/api/catalog/resources/{rid}", json={"body": text},
                            headers=u1).status_code == 200
    assert _kinds(client, sm).count("content_updated") == 1
    # Правки самого менеджера сповіщень не створюють.
    client.patch(f"/api/catalog/resources/{rid}", json={"body": "Від менеджера"}, headers=sm)
    assert _kinds(client, sm).count("content_updated") == 1


# ------------------------------- Модерація -------------------------------

def test_manager_unpublishes_with_reason_and_author_republishes(client, app):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]

    res = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "draft", "note": "Дублює наявний промпт"}, headers=sm)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "draft"
    assert res.get_json()["review_note"] == "Дублює наявний промпт"
    # Чернетку інші користувачі вже не бачать.
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=_login(client, "u2")).status_code == 403

    note = _notes(client, u1)["items"][0]
    assert note["kind"] == "content_moderated"
    assert note["resource_id"] == rid
    assert "Дублює наявний промпт" in note["body"]

    again = client.post(f"/api/catalog/resources/{rid}/status",
                        json={"status": "published"}, headers=u1)
    assert again.get_json()["status"] == "published"
    assert again.get_json()["review_note"] is None
    assert _kinds(client, sm).count("content_published") == 2

    with app.app_context():
        log = ReviewLog.query.filter_by(item_id=rid).order_by(ReviewLog.id).all()
        assert [(l.from_status, l.to_status) for l in log] == [
            (None, "published"), ("published", "draft"), ("draft", "published")]
        assert log[1].note == "Дублює наявний промпт"


def test_manager_archive_and_needs_update_reach_author(client):
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _material(client, u1)["id"]
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "needs_update"}, headers=admin)
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "archived", "note": "Застаріло"}, headers=admin)
    titles = [n["title"] for n in _notes(client, u1)["items"]]
    assert "Матеріал перенесено в архів" in titles
    assert "Матеріал потребує оновлення" in titles


def test_manager_deletes_with_reason_author_keeps_a_note(client, app):
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _material(client, u1)["id"]
    res = client.delete(f"/api/catalog/resources/{rid}", json={"note": "Порушує політику"},
                        headers=admin)
    assert res.status_code == 200
    with app.app_context():
        assert CatalogResource.query.get(rid) is None
        # Сповіщення про видалений матеріал прибрано, лишилося лише авторове.
        assert Notification.query.filter_by(resource_id=rid).count() == 0
    note = _notes(client, u1)["items"][0]
    assert note["title"] == "Матеріал видалено"
    assert note["resource_id"] is None
    assert "Порушує політику" in note["body"] and "Промпт для звітів" in note["body"]


def test_author_deleting_own_material_notifies_nobody(client):
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _material(client, u1, status="draft")["id"]
    assert client.delete(f"/api/catalog/resources/{rid}", headers=u1).status_code == 200
    assert _notes(client, admin)["items"] == []
    assert _notes(client, u1)["items"] == []


# ------------------------------- Навички -------------------------------

def test_user_publishes_skill_and_managers_are_notified(client):
    u1 = _login(client, "u1")
    skill = _skill(client, u1)
    assert skill["status"] == "published" and skill["created_by"]
    note = _notes(client, _admin(client))["items"][0]
    assert note["kind"] == "content_published"
    assert note["skill_id"] == skill["id"]
    assert note["skill_name"] == "Протокол наради"
    # Опубліковану навичку бачать усі.
    assert client.get(f"/api/skills/{skill['id']}",
                      headers=_login(client, "u2")).status_code == 200


def test_skill_draft_visible_to_author_and_managers_only(client):
    u1 = _login(client, "u1")
    sid = _skill(client, u1, status="draft")["id"]
    assert client.get(f"/api/skills/{sid}", headers=u1).status_code == 200
    assert client.get(f"/api/skills/{sid}", headers=_admin(client)).status_code == 200
    assert client.get(f"/api/skills/{sid}", headers=_login(client, "u2")).status_code == 404
    mine = client.get("/api/skills?mine=1", headers=u1).get_json()
    assert [s["id"] for s in mine] == [sid]
    assert sid not in [s["id"] for s in client.get("/api/skills", headers=u1).get_json()]


def test_author_edits_own_skill_and_new_version_notifies(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    client.post("/api/catalog/notifications/read", json={}, headers=sm)
    res = client.patch(f"/api/skills/{sid}", json={"description": "Краще"}, headers=u1)
    assert res.status_code == 200 and res.get_json()["description"] == "Краще"
    unread = [n["kind"] for n in _notes(client, sm)["items"] if not n["is_read"]]
    assert unread == ["content_updated"]
    # Чужою навичкою інший користувач не керує.
    u2 = _login(client, "u2")
    assert client.patch(f"/api/skills/{sid}", json={"name": "X"}, headers=u2).status_code == 403
    assert client.delete(f"/api/skills/{sid}", headers=u2).status_code == 403


def test_manager_moderates_user_skill(client, app):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    res = client.post(f"/api/skills/{sid}/status",
                      json={"status": "draft", "note": "Немає інструкції"}, headers=sm)
    assert res.get_json()["status"] == "draft"
    note = _notes(client, u1)["items"][0]
    assert note["skill_id"] == sid and "Немає інструкції" in note["body"]

    assert client.delete(f"/api/skills/{sid}", json={"note": "Дубль"}, headers=sm).status_code == 200
    with app.app_context():
        assert db.session.get(Skill, sid) is None
        assert Notification.query.filter_by(skill_id=sid).count() == 0
    assert _notes(client, u1)["items"][0]["title"] == "Матеріал видалено"


# ------------------------------- Коментарі -------------------------------

def test_comments_are_public_and_author_is_notified(client):
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    rid = _material(client, u1)["id"]
    res = client.post(f"/api/catalog/resources/{rid}/feedback",
                      json={"message": "Додайте приклад", "rating": 4}, headers=u2)
    assert res.status_code == 201
    assert res.get_json()["mine"] is True
    for viewer in (u1, u2, _admin(client)):
        thread = client.get(f"/api/catalog/resources/{rid}/feedback", headers=viewer).get_json()
        assert [c["message"] for c in thread["items"]] == ["Додайте приклад"]
        assert thread["rating_avg"] == 4.0 and thread["rating_count"] == 1
    note = _notes(client, u1)["items"][0]
    assert note["kind"] == "comment_added" and note["resource_id"] == rid
    assert "Додайте приклад" in note["body"]


def test_one_rating_per_user_and_rating_without_text_stays_out_of_thread(client):
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    rid = _material(client, u1)["id"]
    url = f"/api/catalog/resources/{rid}/feedback"
    client.post(url, json={"rating": 2}, headers=u2)
    client.post(url, json={"rating": 5}, headers=u2)
    thread = client.get(url, headers=u2).get_json()
    assert thread["rating_count"] == 1 and thread["rating_avg"] == 5.0
    assert thread["my_rating"] == 5
    assert thread["items"] == []
    # Оцінка без тексту авторові сповіщень не шле.
    assert _notes(client, u1)["items"] == []


def test_comment_deletion_rights(client):
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    rid = _material(client, u1)["id"]
    url = f"/api/catalog/resources/{rid}/feedback"
    cid = client.post(url, json={"message": "Мій коментар", "rating": 3},
                      headers=u2).get_json()["id"]
    other = client.post(url, json={"message": "Від автора"}, headers=u1).get_json()["id"]
    # Чужий коментар не видалити; свій — так, і оцінка при цьому лишається.
    assert client.delete(f"/api/catalog/feedback/{other}", headers=u2).status_code == 403
    assert client.delete(f"/api/catalog/feedback/{cid}", headers=u2).status_code == 200
    thread = client.get(url, headers=u2).get_json()
    assert [c["message"] for c in thread["items"]] == ["Від автора"]
    assert thread["my_rating"] == 3
    # Менеджер видаляє будь-який коментар.
    assert client.delete(f"/api/catalog/feedback/{other}",
                         headers=_login(client, "sm")).status_code == 200
    assert client.get(url, headers=u2).get_json()["items"] == []


def test_private_legacy_feedback_is_not_shown(client, app):
    u1 = _login(client, "u1")
    rid = _material(client, u1)["id"]
    with app.app_context():
        db.session.add(SkillFeedback(item_type="resource", resource_id=rid, skill_name="x",
                                     message="Лише для команди", is_public=False,
                                     user_id=User.query.filter_by(username="u2").first().id))
        db.session.commit()
    thread = client.get(f"/api/catalog/resources/{rid}/feedback", headers=u1).get_json()
    assert thread["items"] == []
    # Команда в стрічці бачить і такі відгуки.
    inbox = client.get("/api/skills/feedback", headers=_admin(client)).get_json()["items"]
    assert any(f["message"] == "Лише для команди" and f["is_public"] is False for f in inbox)


def test_skill_comments_and_hidden_draft(client):
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    sid = _skill(client, u1)["id"]
    assert client.post(f"/api/skills/{sid}/feedback", json={"message": "Працює"},
                       headers=u2).status_code == 201
    thread = client.get(f"/api/skills/{sid}/feedback", headers=u1).get_json()
    assert [c["author"] for c in thread["items"]] == ["u2"]
    draft = _skill(client, u1, status="draft", name="Чернетка")["id"]
    assert client.post(f"/api/skills/{draft}/feedback", json={"message": "?"},
                       headers=u2).status_code == 404
