"""Попередня модерація й права за розділами (фідбек тестування, п. 11–12).

Матеріал користувача потрапляє в каталог лише після схвалення менеджером
свого розділу (або адміністратором). Менеджер модерує тільки призначені
розділи; хто модерує розділ, публікує одразу. Зміни автора до опублікованого
чекають схвалення, не ховаючи опубліковану версію.
"""
import io
import zipfile

import pytest

from app.extensions import db
from app.models import (CatalogResource, CatalogSection, Notification, ReviewLog, Skill,
                        SkillFeedback, User, UserFile)
from tests.conftest import sm_section_id

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
       b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
       b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _material(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт для звітів",
               "description": "Опис", "body": "Текст промпту", "status": "published",
               "section_id": sm_section_id()}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _skill(client, headers, status="published", name="Протокол наради", section=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.md", f"---\nname: {name}\ndescription: Опис\nversion: 1.0.0\n---\n# Як\n")
    data = {"file": (io.BytesIO(buf.getvalue()), "s.zip"), "status": status}
    if section:
        data["section_id"] = str(sm_section_id())
    res = client.post("/api/skills/upload", headers=headers, data=data,
                      content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _zip(name, version):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.md", f"---\nname: {name}\ndescription: Опис\nversion: {version}\n---\n# Як\n")
    return buf.getvalue()


def _status(client, headers, rid, status, note=None, kind="catalog/resources"):
    return client.post(f"/api/{kind}/{rid}/status", headers=headers,
                       json={"status": status, "note": note})


def _notes(client, headers):
    return client.get("/api/catalog/notifications", headers=headers).get_json()["items"]


def _ids(client, headers, url="/api/catalog/resources"):
    return [r["id"] for r in client.get(url, headers=headers).get_json()]


@pytest.fixture
def other_section(app):
    """Розділ, якого менеджер `sm` не модерує."""
    section = CatalogSection(name="Чужий розділ")
    db.session.add(section)
    db.session.commit()
    return section.id


# ------------------------------- Подання -------------------------------

def test_submission_waits_for_section_manager(client):
    u1, u2, sm = _login(client, "u1"), _login(client, "u2"), _login(client, "sm")
    res = _material(client, u1)
    assert res["status"] == "review" and res["published_at"] is None
    rid = res["id"]
    # Поза каталогом: інший користувач не бачить, у переліку й пошуку немає.
    assert client.get(f"/api/catalog/resources/{rid}", headers=u2).status_code == 403
    assert rid not in _ids(client, u2)
    # Менеджер розділу бачить подання й отримує сповіщення; адміністратор —
    # ні: у розділу є свій менеджер.
    assert rid in _ids(client, sm)
    notes = _notes(client, sm)
    assert notes[0]["kind"] == "review_requested" and notes[0]["resource_id"] == rid
    assert _notes(client, _admin(client)) == []
    queue = client.get("/api/catalog/moderation", headers=sm).get_json()
    assert queue["count"] == 1 and queue["items"][0]["kind"] == "submitted"
    assert queue["items"][0]["item"]["id"] == rid


def test_section_without_manager_goes_to_admins(client, other_section):
    u1 = _login(client, "u1")
    rid = _material(client, u1, section_id=other_section)["id"]
    assert [n["resource_id"] for n in _notes(client, _admin(client))] == [rid]
    assert _notes(client, _login(client, "sm")) == []


def test_manager_approves_and_author_becomes_owner(client, app):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    res = _status(client, sm, rid, "published").get_json()
    assert res["status"] == "published" and res["published_at"]
    assert res["owner"] == "u1"                 # автор, а не менеджер
    assert rid in _ids(client, _login(client, "u2"))
    note = _notes(client, u1)[0]
    assert note["kind"] == "content_moderated" and note["title"] == "Матеріал опубліковано"
    with app.app_context():
        log = ReviewLog.query.filter_by(item_id=rid).order_by(ReviewLog.id.desc()).first()
        assert (log.from_status, log.to_status) == ("review", "published")


def test_manager_returns_with_reason(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    res = _status(client, sm, rid, "draft", "Додайте приклад використання").get_json()
    assert res["status"] == "draft"
    assert res["review_note"] == "Додайте приклад використання"
    note = _notes(client, u1)[0]
    assert note["title"] == "Матеріал повернуто на доопрацювання"
    assert "Додайте приклад використання" in note["body"]
    # Автор подає знову — причина знімається.
    res = _status(client, u1, rid, "published").get_json()
    assert res["status"] == "review" and res["review_note"] is None


def test_manager_of_other_section_cannot_moderate(client, other_section):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1, section_id=other_section)["id"]
    assert client.get(f"/api/catalog/resources/{rid}", headers=sm).status_code == 403
    assert _status(client, sm, rid, "published").status_code == 403
    assert client.patch(f"/api/catalog/resources/{rid}", headers=sm,
                        json={"name": "Х"}).status_code == 403
    assert client.delete(f"/api/catalog/resources/{rid}", headers=sm).status_code == 403
    assert client.get("/api/catalog/moderation", headers=sm).get_json()["count"] == 0
    # Адміністратор модерує будь-який розділ.
    assert _status(client, _admin(client), rid, "published").get_json()["status"] == "published"


def test_manager_cannot_move_material_out_of_own_sections(client, other_section):
    sm = _login(client, "sm")
    rid = _material(client, sm)["id"]
    res = client.patch(f"/api/catalog/resources/{rid}", headers=sm,
                       json={"section_id": other_section})
    assert res.status_code == 403
    assert db.session.get(CatalogResource, rid).section_id == sm_section_id()


def test_plain_user_cannot_use_moderation_queue(client):
    assert client.get("/api/catalog/moderation",
                      headers=_login(client, "u1")).status_code == 403


def test_resubmission_to_new_section_notifies_its_moderators(client, other_section):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1, section_id=other_section)["id"]
    assert _notes(client, sm) == []
    client.patch(f"/api/catalog/resources/{rid}", headers=u1,
                 json={"section_id": sm_section_id()})
    assert [n["resource_id"] for n in _notes(client, sm)] == [rid]


# ---------------------- Зміни до опублікованого ----------------------

def _published(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    _status(client, sm, rid, "published")
    return u1, sm, rid


def test_author_changes_wait_for_approval(client):
    u1, sm, rid = _published(client)
    res = client.patch(f"/api/catalog/resources/{rid}", headers=u1,
                       json={"name": "Нова назва", "body": "Новий текст", "tags": "звіти"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["name"] == "Промпт для звітів"           # опублікована версія лишилась
    assert data["status"] == "published" and data["has_pending"] is True
    assert data["pending"]["data"]["name"] == "Нова назва"
    # Інші бачать опубліковану версію й не бачать пропозиції.
    other = client.get(f"/api/catalog/resources/{rid}", headers=_login(client, "u2")).get_json()
    assert other["name"] == "Промпт для звітів" and other["pending"] is None
    # Менеджер бачить зміни в черзі.
    queue = client.get("/api/catalog/moderation", headers=sm).get_json()
    assert [(x["kind"], x["item"]["id"]) for x in queue["items"]] == [("changes", rid)]
    assert queue["items"][0]["item"]["pending"]["data"]["body"] == "Новий текст"
    assert _notes(client, sm)[0]["title"] == "Зміни чекають схвалення"

    res = client.post(f"/api/catalog/resources/{rid}/changes", headers=sm,
                      json={"action": "approve"}).get_json()
    assert res["name"] == "Нова назва" and res["body"] == "Новий текст"
    assert res["tags"] == ["звіти"] and res["has_pending"] is False
    assert _notes(client, u1)[0]["title"] == "Зміни схвалено"


def test_rejected_changes_keep_published_version(client):
    u1, sm, rid = _published(client)
    client.patch(f"/api/catalog/resources/{rid}", headers=u1, json={"name": "Гірша назва"})
    res = client.post(f"/api/catalog/resources/{rid}/changes", headers=sm,
                      json={"action": "reject", "note": "Назва не описує задачу"}).get_json()
    assert res["name"] == "Промпт для звітів" and res["has_pending"] is False
    note = _notes(client, u1)[0]
    assert note["title"] == "Зміни відхилено" and "Назва не описує задачу" in note["body"]


def test_author_withdraws_changes_and_cannot_approve_them(client):
    u1, sm, rid = _published(client)
    client.patch(f"/api/catalog/resources/{rid}", headers=u1, json={"name": "Х"})
    assert client.post(f"/api/catalog/resources/{rid}/changes", headers=u1,
                       json={"action": "approve"}).status_code == 403
    res = client.post(f"/api/catalog/resources/{rid}/changes", headers=u1,
                      json={"action": "withdraw"})
    assert res.status_code == 200 and res.get_json()["has_pending"] is False
    assert client.post(f"/api/catalog/resources/{rid}/changes", headers=sm,
                       json={"action": "approve"}).status_code == 400


def test_invalid_changes_are_rejected_at_once(client):
    u1, _sm, rid = _published(client)
    res = client.patch(f"/api/catalog/resources/{rid}", headers=u1, json={"name": "  "})
    assert res.status_code == 400
    assert db.session.get(CatalogResource, rid).pending_data is None


def test_manager_edits_published_directly(client):
    sm = _login(client, "sm")
    rid = _material(client, sm)["id"]
    res = client.patch(f"/api/catalog/resources/{rid}", headers=sm,
                       json={"name": "Одразу"}).get_json()
    assert res["name"] == "Одразу" and res["has_pending"] is False


def test_unpublished_material_takes_author_changes_as_draft(client):
    u1, _sm, rid = _published(client)
    client.patch(f"/api/catalog/resources/{rid}", headers=u1, json={"name": "Остання версія"})
    res = _status(client, u1, rid, "draft").get_json()
    assert res["status"] == "draft" and res["name"] == "Остання версія"
    assert res["has_pending"] is False


def test_images_of_pending_changes_live_until_decision(client):
    u1, sm, rid = _published(client)
    up = client.post("/api/catalog/images", headers=u1,
                     data={"file": (io.BytesIO(PNG), "схема.png")},
                     content_type="multipart/form-data").get_json()
    client.patch(f"/api/catalog/resources/{rid}", headers=u1,
                 json={"body": f"Текст\n\n![]({up['ref']})"})
    image = db.session.get(UserFile, up["id"])
    assert image.resource_id == rid                    # прив'язано, не загубиться
    client.post(f"/api/catalog/resources/{rid}/changes", headers=sm,
                json={"action": "reject"})
    assert db.session.get(UserFile, up["id"]) is None   # зміни відхилено — картинки немає


def test_file_added_to_published_material_notifies_manager(client):
    u1, sm, rid = _published(client)
    res = client.post(f"/api/catalog/resources/{rid}/files", headers=u1,
                      data={"file": (io.BytesIO(b"txt"), "guide.txt")},
                      content_type="multipart/form-data")
    assert res.status_code == 201
    assert _notes(client, sm)[0]["kind"] == "content_updated"


# ------------------------------ Видалення ------------------------------

def test_manager_deletes_with_reason(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    assert client.delete(f"/api/catalog/resources/{rid}", headers=sm,
                         json={"note": "Дублікат"}).status_code == 200
    note = _notes(client, u1)[0]
    assert note["title"] == "Матеріал видалено" and "Дублікат" in note["body"]
    assert note["resource_id"] is None


def test_author_deleting_own_material_notifies_nobody(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1, status="draft")["id"]
    assert client.delete(f"/api/catalog/resources/{rid}", headers=u1).status_code == 200
    assert _notes(client, sm) == []


# ------------------------------- Навички -------------------------------

def test_skill_submission_and_approval(client):
    u1, u2, sm = _login(client, "u1"), _login(client, "u2"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    assert db.session.get(Skill, sid).status == "review"
    assert client.get(f"/api/skills/{sid}", headers=u2).status_code == 404
    assert _notes(client, sm)[0]["skill_id"] == sid
    queue = client.get("/api/catalog/moderation", headers=sm).get_json()
    assert [(x["type"], x["item"]["id"]) for x in queue["items"]] == [("skill", sid)]
    res = _status(client, sm, sid, "published", kind="skills").get_json()
    assert res["status"] == "published" and res["owner"] == "u1"
    assert client.get(f"/api/skills/{sid}", headers=u2).status_code == 200


def test_skill_submission_requires_section(client):
    u1 = _login(client, "u1")
    buf = _zip("Без розділу", "1.0.0")
    res = client.post("/api/skills/upload", headers=u1,
                      data={"file": (io.BytesIO(buf), "s.zip"), "status": "published"},
                      content_type="multipart/form-data")
    assert res.status_code == 400 and res.get_json()["error"] == "section_required"


def test_skill_returned_with_reason(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    res = _status(client, sm, sid, "draft", "Немає інструкції", kind="skills").get_json()
    assert res["status"] == "draft" and res["review_note"] == "Немає інструкції"
    assert _notes(client, u1)[0]["title"] == "Матеріал повернуто на доопрацювання"


def test_new_version_of_published_skill_waits_for_approval(client, app):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    _status(client, sm, sid, "published", kind="skills")
    v2 = _zip("Протокол наради", "2.0.0")
    res = client.post(f"/api/skills/{sid}/upload", headers=u1,
                      data={"file": (io.BytesIO(v2), "v2.zip")},
                      content_type="multipart/form-data").get_json()
    assert res["version"] == "1.0.0" and res["has_pending"] is True
    assert res["pending"]["data"]["package"]["meta"]["version"] == "2.0.0"
    assert "path" not in res["pending"]["data"]["package"]
    # Опублікований архів лишається старим; нову версію можна переглянути.
    assert client.get(f"/api/skills/{sid}/download?pending=1", headers=sm).data == v2
    assert client.get(f"/api/skills/{sid}/download?pending=1",
                      headers=_login(client, "u2")).status_code == 404
    assert client.get(f"/api/skills/{sid}/files?pending=1", headers=sm).get_json()

    res = client.post(f"/api/skills/{sid}/changes", headers=sm,
                      json={"action": "approve"}).get_json()
    assert res["version"] == "2.0.0" and res["has_pending"] is False
    assert client.get(f"/api/skills/{sid}/download", headers=u1).data == v2


def test_rejected_skill_version_is_removed(client, app):
    import os
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    _status(client, sm, sid, "published", kind="skills")
    client.post(f"/api/skills/{sid}/upload", headers=u1,
                data={"file": (io.BytesIO(_zip("Протокол наради", "2.0.0")), "v2.zip")},
                content_type="multipart/form-data")
    pending = os.path.join(app.config["SKILL_PACKAGES_DIR"], f"skill_{sid}.pending.zip")
    assert os.path.exists(pending)
    res = client.post(f"/api/skills/{sid}/changes", headers=sm,
                      json={"action": "reject", "note": "Зламаний архів"}).get_json()
    assert res["version"] == "1.0.0" and not os.path.exists(pending)


def test_skill_attribute_changes_wait_for_approval(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    _status(client, sm, sid, "published", kind="skills")
    res = client.patch(f"/api/skills/{sid}", headers=u1,
                       json={"description": "Кращий опис"}).get_json()
    assert res["description"] == "Опис" and res["pending"]["data"]["description"] == "Кращий опис"
    res = client.post(f"/api/skills/{sid}/changes", headers=sm,
                      json={"action": "approve"}).get_json()
    assert res["description"] == "Кращий опис"


def test_manager_of_other_section_cannot_moderate_skill(client, other_section):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    sid = _skill(client, u1, section=False, status="draft")["id"]
    client.patch(f"/api/skills/{sid}", headers=u1, json={"section_id": other_section})
    _status(client, u1, sid, "published", kind="skills")
    assert client.get(f"/api/skills/{sid}", headers=sm).status_code == 404
    assert _status(client, sm, sid, "published", kind="skills").status_code == 404


# ------------------------------ Коментарі ------------------------------

def _public(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _material(client, u1)["id"]
    _status(client, sm, rid, "published")
    return rid


def test_comments_are_public_and_author_is_notified(client):
    rid = _public(client)
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    res = client.post(f"/api/catalog/resources/{rid}/feedback",
                      json={"message": "Додайте приклад", "rating": 4}, headers=u2)
    assert res.status_code == 201 and res.get_json()["mine"] is True
    for viewer in (u1, u2, _admin(client)):
        thread = client.get(f"/api/catalog/resources/{rid}/feedback", headers=viewer).get_json()
        assert [c["message"] for c in thread["items"]] == ["Додайте приклад"]
        assert thread["rating_avg"] == 4.0 and thread["rating_count"] == 1
    note = _notes(client, u1)[0]
    assert note["kind"] == "comment_added" and note["resource_id"] == rid


def test_one_rating_per_user_and_rating_without_text_stays_out_of_thread(client):
    rid = _public(client)
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    url = f"/api/catalog/resources/{rid}/feedback"
    before = len(_notes(client, u1))
    client.post(url, json={"rating": 2}, headers=u2)
    client.post(url, json={"rating": 5}, headers=u2)
    thread = client.get(url, headers=u2).get_json()
    assert thread["rating_count"] == 1 and thread["rating_avg"] == 5.0
    assert thread["my_rating"] == 5 and thread["items"] == []
    assert len(_notes(client, u1)) == before        # оцінка без тексту не сповіщає


def test_comment_deletion_rights(client, other_section):
    rid = _public(client)
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    url = f"/api/catalog/resources/{rid}/feedback"
    cid = client.post(url, json={"message": "Мій коментар", "rating": 3},
                      headers=u2).get_json()["id"]
    other = client.post(url, json={"message": "Від автора"}, headers=u1).get_json()["id"]
    assert client.delete(f"/api/catalog/feedback/{other}", headers=u2).status_code == 403
    assert client.delete(f"/api/catalog/feedback/{cid}", headers=u2).status_code == 200
    thread = client.get(url, headers=u2).get_json()
    assert [c["message"] for c in thread["items"]] == ["Від автора"]
    assert thread["my_rating"] == 3
    # Менеджер розділу видаляє будь-який коментар у своєму розділі…
    sm = _login(client, "sm")
    assert client.get(url, headers=sm).get_json()["items"][0]["can_delete"] is True
    assert client.delete(f"/api/catalog/feedback/{other}", headers=sm).status_code == 200
    # …а в чужому — ні.
    rid2 = _material(client, _admin(client), section_id=other_section)["id"]
    url2 = f"/api/catalog/resources/{rid2}/feedback"
    c2 = client.post(url2, json={"message": "Тут"}, headers=u2).get_json()["id"]
    assert client.delete(f"/api/catalog/feedback/{c2}", headers=sm).status_code == 403


def test_private_legacy_feedback_is_not_shown(client, app):
    rid = _public(client)
    u1 = _login(client, "u1")
    db.session.add(SkillFeedback(item_type="resource", resource_id=rid, skill_name="x",
                                 message="Лише для команди", is_public=False,
                                 user_id=User.query.filter_by(username="u2").first().id))
    db.session.commit()
    thread = client.get(f"/api/catalog/resources/{rid}/feedback", headers=u1).get_json()
    assert thread["items"] == []
    inbox = client.get("/api/skills/feedback", headers=_admin(client)).get_json()["items"]
    assert any(f["message"] == "Лише для команди" and f["is_public"] is False for f in inbox)


def test_skill_comments_and_hidden_draft(client):
    u1, u2, sm = _login(client, "u1"), _login(client, "u2"), _login(client, "sm")
    sid = _skill(client, u1)["id"]
    _status(client, sm, sid, "published", kind="skills")
    assert client.post(f"/api/skills/{sid}/feedback", json={"message": "Працює"},
                       headers=u2).status_code == 201
    thread = client.get(f"/api/skills/{sid}/feedback", headers=u1).get_json()
    assert [c["author"] for c in thread["items"]] == ["u2"]
    draft = _skill(client, u1, status="draft", name="Чернетка")["id"]
    assert client.post(f"/api/skills/{draft}/feedback", json={"message": "?"},
                       headers=u2).status_code == 404


# ------------------------- Ролі й адміністрування -------------------------

def test_structure_and_settings_are_admin_only(client):
    sm = _login(client, "sm")
    assert client.post("/api/catalog/sections", headers=sm,
                       json={"name": "Новий"}).status_code == 403
    assert client.post("/api/catalog/terms", headers=sm,
                       json={"kind": "tag", "name": "x"}).status_code == 403
    assert client.post("/api/catalog/settings", headers=sm,
                       json={"strict_publish": True}).status_code == 403
    assert client.post("/api/paths", headers=sm, json={"name": "М"}).status_code == 403
    # Аналітика менеджеру розділу доступна.
    assert client.get("/api/catalog/kpi", headers=sm).status_code == 200


def test_ideas_are_for_ideas_owners(client, app):
    sm, u1 = _login(client, "sm"), _login(client, "u1")
    assert client.get("/api/ideas/stats", headers=sm).status_code in (403, 404)
    user = User.query.filter_by(username="u1").one()
    user.is_ideas_owner = True
    db.session.commit()
    ideas = client.get("/api/ideas", headers=u1)
    assert ideas.status_code == 200


def test_admin_assigns_sections_and_cannot_demote_self(client, other_section):
    admin = _admin(client)
    uid = User.query.filter_by(username="u2").one().id
    res = client.patch(f"/api/users/{uid}", headers=admin, json={
        "roles": ["section_manager"], "managed_section_ids": [other_section],
        "is_ideas_owner": True})
    assert res.status_code == 200
    data = res.get_json()
    assert data["managed_section_ids"] == [other_section] and data["is_ideas_owner"]
    # Без ролі менеджера розділи знімаються.
    data = client.patch(f"/api/users/{uid}", headers=admin, json={"roles": []}).get_json()
    assert data["managed_section_ids"] == []
    assert client.patch(f"/api/users/{uid}", headers=admin,
                        json={"roles": ["skill_manager"]}).status_code == 400
    admin_id = User.query.filter_by(username="admin").one().id
    # Системного адміністратора роль «admin» не тримає — він лишається адміністратором.
    assert client.patch(f"/api/users/{admin_id}", headers=admin,
                        json={"roles": []}).status_code == 200


def test_deleting_section_drops_manager_assignment(client, other_section):
    admin = _admin(client)
    uid = User.query.filter_by(username="u2").one().id
    client.patch(f"/api/users/{uid}", headers=admin, json={
        "roles": ["section_manager"], "managed_section_ids": [other_section]})
    assert client.delete(f"/api/catalog/sections/{other_section}",
                         headers=admin).status_code == 200
    assert client.get(f"/api/users/{uid}", headers=admin).get_json()["managed_section_ids"] == []
