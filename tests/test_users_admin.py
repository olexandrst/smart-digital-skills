"""Тести менеджменту користувачів (Admin): CRUD і повнота видалення."""
from app.extensions import db
from app.models import (
    User, CatalogFavorite, Notification, Idea, SkillFeedback, Skill, UserRole,
)
from tests.conftest import login, auth


def _uid(username):
    return User.query.filter_by(username=username).first().id


def test_create_edit_delete_user(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/users", headers=auth(token), json={
        "username": "temp", "full_name": "Тимчасовий", "password": "pass12",
        "roles": ["skill_manager"]})
    assert res.status_code == 201
    uid = res.get_json()["id"]
    assert res.get_json()["roles"] == ["skill_manager"]

    res = client.patch(f"/api/users/{uid}", headers=auth(token),
                       json={"full_name": "Оновлений", "email": "t@e.com"})
    assert res.get_json()["full_name"] == "Оновлений"

    res = client.delete(f"/api/users/{uid}", headers=auth(token))
    assert res.status_code == 200
    assert db.session.get(User, uid) is None


def test_user_list_has_no_quota_fields(client):
    rows = client.get("/api/users", headers=auth(login(client, "admin", "Admin123!"))).get_json()
    assert rows and all(not ({"used_cost", "custom_limit", "effective_limit"} & r.keys())
                        for r in rows)


def test_delete_user_removes_own_data_and_keeps_history(client):
    """Особисте (обране, сповіщення, ролі) зникає; історія (ідеї, відгуки)
    лишається, лише відв'язана від користувача."""
    u2 = login(client, "u2", "pass")
    uid = _uid("u2")
    sid = Skill.query.filter_by(name="Summarizer").first().id
    client.post("/api/catalog/favorites", headers=auth(u2),
                json={"item_type": "skill", "item_id": sid})
    client.post(f"/api/skills/{sid}/feedback", headers=auth(u2), json={"message": "Добре"})
    client.post("/api/ideas", headers=auth(u2), json={
        "title": "Ідея", "body": "Опис", "problem": "Проблема"})
    db.session.add(Notification(user_id=uid, kind="review_due", title="t",
                                dedupe_key="k1"))
    db.session.commit()

    admin = login(client, "admin", "Admin123!")
    assert client.delete(f"/api/users/{uid}", headers=auth(admin)).status_code == 200

    assert CatalogFavorite.query.filter_by(user_id=uid).count() == 0
    assert Notification.query.filter_by(user_id=uid).count() == 0
    assert UserRole.query.filter_by(user_id=uid).count() == 0
    fb = SkillFeedback.query.filter_by(message="Добре").one()
    assert fb.user_id is None
    idea = Idea.query.filter_by(title="Ідея").one()
    assert idea.author_id is None


def test_cannot_delete_self_or_system_admin(client):
    token = login(client, "admin", "Admin123!")
    admin_id = _uid("admin")
    res = client.delete(f"/api/users/{admin_id}", headers=auth(token))
    assert res.status_code == 400  # і self, і system_admin


def test_non_admin_cannot_manage_users(client):
    token = login(client, "u1", "pass")
    uid = _uid("u2")
    assert client.get("/api/users", headers=auth(token)).status_code == 403
    assert client.delete(f"/api/users/{uid}", headers=auth(token)).status_code == 403
