"""Розгляд поданих матеріалів: подати → опублікувати / повернути, зі сповіщеннями.

Звичайний користувач не публікує сам: він подає чернетку на розгляд, менеджери
отримують сповіщення, а рішення (публікація або повернення з коментарем)
приходить авторові також сповіщенням.
"""
from app.extensions import db
from app.models import CatalogResource, Notification, ReviewLog, User


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _draft(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт для звітів",
               "description": "Опис", "body": "Текст промпту"}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _notes(client, headers):
    return client.get("/api/catalog/notifications", headers=headers).get_json()


def _user_id(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).first().id


# ------------------------------- Подання -------------------------------

def test_submit_moves_draft_to_review_and_notifies_managers(client, app):
    u1 = _login(client, "u1")
    rid = _draft(client, u1)["id"]

    res = client.post(f"/api/catalog/resources/{rid}/submit", headers=u1)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "review"
    assert res.get_json()["submitted_at"]

    # Обидва менеджери (admin і skill_manager) отримали сповіщення, автор — ні.
    for manager in (_admin(client), _login(client, "sm")):
        notes = _notes(client, manager)
        assert notes["unread"] == 1
        note = notes["items"][0]
        assert note["kind"] == "review_requested"
        assert note["resource_id"] == rid
        assert "Промпт для звітів" in note["body"]
    assert _notes(client, u1)["items"] == []
    assert _notes(client, _login(client, "u2"))["items"] == []

    with app.app_context():
        log = ReviewLog.query.filter_by(item_id=rid).order_by(ReviewLog.id).all()
        assert [(l.from_status, l.to_status) for l in log] == [("draft", "review")]


def test_wizard_can_submit_on_create(client):
    u1 = _login(client, "u1")
    data = _draft(client, u1, submit=True)
    assert data["status"] == "review"
    assert _notes(client, _admin(client))["unread"] == 1


def test_only_draft_can_be_submitted_and_only_by_author_or_manager(client):
    u1, u2 = _login(client, "u1"), _login(client, "u2")
    rid = _draft(client, u1)["id"]
    assert client.post(f"/api/catalog/resources/{rid}/submit", headers=u2).status_code == 403
    assert client.post(f"/api/catalog/resources/{rid}/submit", headers=u1).status_code == 200
    # Повторне подання того, що вже на розгляді, — конфлікт стану.
    assert client.post(f"/api/catalog/resources/{rid}/submit", headers=u1).status_code == 409


def test_author_cannot_edit_while_in_review_but_can_withdraw(client):
    u1 = _login(client, "u1")
    rid = _draft(client, u1, submit=True)["id"]

    assert client.patch(f"/api/catalog/resources/{rid}",
                        json={"name": "Нова"}, headers=u1).status_code == 403
    assert client.delete(f"/api/catalog/resources/{rid}", headers=u1).status_code == 403

    back = client.post(f"/api/catalog/resources/{rid}/withdraw", headers=u1)
    assert back.status_code == 200
    assert back.get_json()["status"] == "draft"
    assert client.patch(f"/api/catalog/resources/{rid}",
                        json={"name": "Нова"}, headers=u1).status_code == 200
    # Чужий користувач відкликати не може; відкликати можна лише з розгляду.
    assert client.post(f"/api/catalog/resources/{rid}/withdraw",
                       headers=_login(client, "u2")).status_code == 403
    assert client.post(f"/api/catalog/resources/{rid}/withdraw", headers=u1).status_code == 409


# ------------------------------- Рішення -------------------------------

def test_manager_publishes_from_review_and_author_is_notified(client, app):
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _draft(client, u1, submit=True)["id"]

    res = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "published"}, headers=admin)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["status"] == "published"
    assert res.get_json()["published_at"]

    notes = _notes(client, u1)
    assert [n["kind"] for n in notes["items"]] == ["review_published"]
    assert notes["unread"] == 1
    # Опублікований матеріал видно всім.
    assert client.get(f"/api/catalog/resources/{rid}",
                      headers=_login(client, "u2")).status_code == 200


def test_manager_rejects_with_note_and_author_is_notified(client, app):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    rid = _draft(client, u1, submit=True)["id"]

    assert client.post(f"/api/catalog/resources/{rid}/reject",
                       json={"note": "   "}, headers=sm).status_code == 400

    res = client.post(f"/api/catalog/resources/{rid}/reject",
                      json={"note": "Додайте приклад вхідних даних"}, headers=sm)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["status"] == "draft"
    assert data["review_note"] == "Додайте приклад вхідних даних"

    notes = _notes(client, u1)
    assert notes["items"][0]["kind"] == "review_rejected"
    assert "Додайте приклад вхідних даних" in notes["items"][0]["body"]

    # Автор виправляє й подає знову: коментар знімається, менеджери отримують
    # нове сповіщення (ключ проти повторів включає час подання).
    assert client.patch(f"/api/catalog/resources/{rid}",
                        json={"body": "Текст промпту. Приклад: ..."}, headers=u1).status_code == 200
    again = client.post(f"/api/catalog/resources/{rid}/submit", headers=u1)
    assert again.status_code == 200
    assert again.get_json()["review_note"] is None
    assert _notes(client, sm)["unread"] == 2

    with app.app_context():
        log = ReviewLog.query.filter_by(item_id=rid).order_by(ReviewLog.id).all()
        assert [(l.from_status, l.to_status) for l in log] == [
            ("draft", "review"), ("review", "draft"), ("draft", "review")]
        assert log[1].note == "Додайте приклад вхідних даних"


def test_reject_requires_manager_and_review_status(client):
    u1 = _login(client, "u1")
    rid = _draft(client, u1)["id"]
    assert client.post(f"/api/catalog/resources/{rid}/reject",
                       json={"note": "x"}, headers=u1).status_code == 403
    assert client.post(f"/api/catalog/resources/{rid}/reject",
                       json={"note": "Ще чернетка"}, headers=_admin(client)).status_code == 409
    assert client.post(f"/api/catalog/resources/{rid}/status",
                       json={"status": "published"}, headers=u1).status_code == 403


def test_status_change_from_review_other_than_publish_counts_as_return(client):
    """Менеджер перевів з розгляду у чернетку через загальний ендпоінт статусу —
    автор однаково має дізнатися."""
    u1, admin = _login(client, "u1"), _admin(client)
    rid = _draft(client, u1, submit=True)["id"]
    res = client.post(f"/api/catalog/resources/{rid}/status",
                      json={"status": "draft", "note": "Не той розділ"}, headers=admin)
    assert res.status_code == 200
    assert res.get_json()["review_note"] == "Не той розділ"
    assert _notes(client, u1)["items"][0]["kind"] == "review_rejected"


def test_manager_sees_review_queue_plain_user_does_not(client):
    u1 = _login(client, "u1")
    rid = _draft(client, u1, submit=True)["id"]
    queue = client.get("/api/catalog/resources?status=review", headers=_admin(client)).get_json()
    assert [r["id"] for r in queue] == [rid]
    assert client.get("/api/catalog/resources?status=review",
                      headers=_login(client, "u2")).status_code == 403
    # В автора — у списку «мої» зі статусом review, у каталозі — ні.
    mine = client.get("/api/catalog/resources?mine=1", headers=u1).get_json()
    assert mine[0]["status"] == "review"
    assert rid not in [r["id"] for r in client.get("/api/catalog/resources", headers=u1).get_json()]


def test_self_submission_by_manager_does_not_notify_self(client):
    admin = _admin(client)
    _draft(client, admin, status="draft", submit=True)
    assert _notes(client, admin)["items"] == []
    assert _notes(client, _login(client, "sm"))["unread"] == 1
