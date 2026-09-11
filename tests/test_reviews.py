"""Нагадування власникам контенту про перегляд (AKH-18)."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Notification, CatalogResource, User
from app.services import review_service


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _resource(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published", "owner": "u1"}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _set_review_date(app, resource_id, days_from_now):
    with app.app_context():
        CatalogResource.query.filter_by(id=resource_id).update(
            {CatalogResource.next_review_at:
             datetime.utcnow() + timedelta(days=days_from_now)})
        db.session.commit()


# ------------------------- Створення нагадувань -------------------------

def test_reminder_created_a_week_before_due(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, 5)
    with app.app_context():
        assert review_service.create_reminders() == 1
        note = Notification.query.one()
        assert note.kind == "review_due"
        assert "Скоро перегляд" in note.title


def test_reminder_created_when_overdue(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, -3)
    with app.app_context():
        review_service.create_reminders()
        assert Notification.query.one().kind == "review_overdue"


def test_no_reminder_long_before_due(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, 60)
    with app.app_context():
        assert review_service.create_reminders() == 0


def test_no_reminder_without_owner(client, app):
    h = _admin(client)
    rid = _resource(client, h, owner="")["id"]
    _set_review_date(app, rid, 2)
    with app.app_context():
        assert review_service.create_reminders() == 0


def test_reminder_not_duplicated_across_runs(client, app):
    """Планувальник у кількох процесах не має слати те саме двічі."""
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, 2)
    with app.app_context():
        review_service.create_reminders()
        review_service.create_reminders()
        review_service.create_reminders()
        assert Notification.query.count() == 1


def test_draft_material_does_not_remind(client, app):
    h = _admin(client)
    rid = _resource(client, h, status="draft")["id"]
    _set_review_date(app, rid, 1)
    with app.app_context():
        assert review_service.create_reminders() == 0


# --------------------------- Перегляд списку ---------------------------

def test_user_sees_own_notifications(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, -1)
    with app.app_context():
        review_service.create_reminders()

    data = client.get("/api/catalog/notifications", headers=_login(client, "u1")).get_json()
    assert data["unread"] == 1
    assert data["items"][0]["resource_name"] == "Промпт"
    # Чужі сповіщення не видно.
    assert client.get("/api/catalog/notifications",
                      headers=_login(client, "u2")).get_json()["unread"] == 0


def test_notifications_marked_read(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, -1)
    with app.app_context():
        review_service.create_reminders()
    u1 = _login(client, "u1")
    client.post("/api/catalog/notifications/read", headers=u1)
    assert client.get("/api/catalog/notifications", headers=u1).get_json()["unread"] == 0


def test_my_content_lists_owned_materials(client, app):
    h = _admin(client)
    overdue = _resource(client, h, name="Прострочений")["id"]
    soon = _resource(client, h, name="Скоро")["id"]
    _resource(client, h, name="Чужий", owner="хтось інший")
    _set_review_date(app, overdue, -5)
    _set_review_date(app, soon, 3)

    data = client.get("/api/catalog/my-content", headers=_login(client, "u1")).get_json()
    names = {i["name"] for i in data["items"]}
    assert names == {"Прострочений", "Скоро"}
    assert data["overdue"] == 1 and data["soon"] == 1


# ----------------------------- Дії власника -----------------------------

def test_owner_confirms_material_is_current(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, -10)

    res = client.post(f"/api/catalog/resources/{rid}/confirm-review",
                      json={"months": 12}, headers=_login(client, "u1"))
    assert res.status_code == 200
    data = res.get_json()
    assert data["review_overdue"] is False
    assert data["reviewed_at"]


def test_confirm_clears_needs_update(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    client.post(f"/api/catalog/resources/{rid}/status",
                json={"status": "needs_update"}, headers=h)
    res = client.post(f"/api/catalog/resources/{rid}/confirm-review",
                      headers=_login(client, "u1"))
    assert res.get_json()["status"] == "published"


def test_confirm_writes_to_review_log(client, app):
    from app.models import ReviewLog
    h = _admin(client)
    rid = _resource(client, h)["id"]
    client.post(f"/api/catalog/resources/{rid}/confirm-review", headers=_login(client, "u1"))
    with app.app_context():
        notes = [r.note for r in ReviewLog.query.filter_by(item_id=rid)]
    assert any(n and "Актуальність підтверджено" in n for n in notes)


def test_stranger_may_not_confirm(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    assert client.post(f"/api/catalog/resources/{rid}/confirm-review",
                       headers=_login(client, "u2")).status_code == 403


def test_manager_may_confirm_any_material(client, app):
    h = _admin(client)
    rid = _resource(client, h, owner="хтось інший")["id"]
    assert client.post(f"/api/catalog/resources/{rid}/confirm-review",
                       headers=h).status_code == 200


def test_owner_can_archive_from_the_list(client, app):
    """Зі списку власного контенту доступні й інші дії життєвого циклу."""
    h = _admin(client)
    rid = _resource(client, h)["id"]
    assert client.post(f"/api/catalog/resources/{rid}/status",
                       json={"status": "archived"}, headers=h).status_code == 200


# ------------------------- Поштовий підсумок -------------------------

def test_email_digest_on_by_default_and_switchable(client, app):
    u1 = _login(client, "u1")
    assert client.get("/api/catalog/my-content", headers=u1).get_json()["email_digest"] is True
    assert client.post("/api/catalog/my-content/email-digest",
                       json={"enabled": False}, headers=u1).get_json()["email_digest"] is False
    assert client.get("/api/catalog/my-content", headers=u1).get_json()["email_digest"] is False


def test_digest_skips_users_who_switched_it_off(client, app):
    h = _admin(client)
    rid = _resource(client, h)["id"]
    _set_review_date(app, rid, -2)
    with app.app_context():
        user = User.query.filter_by(username="u1").first()
        user.email = "u1@example.com"
        db.session.commit()
        assert len(review_service.send_weekly_digests()) == 1
        review_service.set_email_digest(user, False)
        assert review_service.send_weekly_digests() == []


def test_digest_text_separates_overdue_and_upcoming(client, app):
    h = _admin(client)
    late = _resource(client, h, name="Прострочений")["id"]
    soon = _resource(client, h, name="Скоро")["id"]
    _set_review_date(app, late, -4)
    _set_review_date(app, soon, 3)
    with app.app_context():
        user = User.query.filter_by(username="u1").first()
        text = review_service.weekly_digest(user)
    assert "Перегляд прострочено" in text and "Прострочений" in text
    assert "найближчим часом" in text and "Скоро" in text
