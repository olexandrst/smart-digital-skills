"""Тести груп без ролей: CRUD групи, членство, пошук користувачів."""
from app.extensions import db
from app.models import User, Group, GroupMembership, UserSkill, Skill
from app.services import group_service, skill_service
from tests.conftest import login, auth


def _uid(username):
    return User.query.filter_by(username=username).first().id


# ---------- CRUD групи ----------

def test_group_create_rename_delete(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/groups", headers=auth(token), json={"name": "Alpha"})
    assert res.status_code == 201
    gid = res.get_json()["id"]
    # Створення НЕ додає творця автоматично (група порожня).
    assert res.get_json()["members"] == []

    res = client.patch(f"/api/groups/{gid}", headers=auth(token),
                       json={"name": "Beta", "description": "opis"})
    assert res.status_code == 200
    assert res.get_json()["name"] == "Beta"

    res = client.delete(f"/api/groups/{gid}", headers=auth(token))
    assert res.status_code == 200
    assert Group.query.get(gid) is None


def test_rename_duplicate_name_conflict(client):
    token = login(client, "admin", "Admin123!")
    client.post("/api/groups", headers=auth(token), json={"name": "G-A"})
    gid = client.post("/api/groups", headers=auth(token), json={"name": "G-B"}).get_json()["id"]
    res = client.patch(f"/api/groups/{gid}", headers=auth(token), json={"name": "G-A"})
    assert res.status_code == 409


# ---------- Членство (без ролей) ----------

def test_add_and_remove_member_no_roles(client):
    token = login(client, "admin", "Admin123!")
    gid = client.post("/api/groups", headers=auth(token), json={"name": "Team"}).get_json()["id"]
    u1 = _uid("u1")

    res = client.post(f"/api/groups/{gid}/members", headers=auth(token),
                      json={"user_id": u1})
    assert res.status_code == 201
    body = res.get_json()
    assert "role" not in body  # ролей більше немає у відповіді
    assert body["status"] == "active"

    # Повторне додавання — конфлікт.
    assert client.post(f"/api/groups/{gid}/members", headers=auth(token),
                       json={"user_id": u1}).status_code == 409

    res = client.delete(f"/api/groups/{gid}/members/{u1}", headers=auth(token))
    assert res.status_code == 200
    assert GroupMembership.query.filter_by(group_id=gid, user_id=u1,
                                           status="active").first() is None


def test_remove_member_does_not_promote_admin(app):
    """Правило «менеджера» прибрано — видалення учасника нікого не «підвищує»."""
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()
    group = Group(name="NoPromote", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id, status="active"))
    db.session.commit()

    group_service.remove_member(group.id, u1.id)
    # Admin НЕ додається автоматично.
    assert GroupMembership.query.filter_by(group_id=group.id, user_id=admin.id).first() is None


def test_delete_group_deactivates_skill_access(app):
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()
    skill_id = Skill.query.filter_by(name="Summarizer").first().id

    group = Group(name="Skilled", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id, status="active"))
    db.session.commit()
    skill_service.assign_to_group(group.id, skill_id, admin.id)
    assert UserSkill.query.filter_by(user_id=u1.id, skill_id=skill_id,
                                     is_active=True).first()

    group_service.delete_group(group.id)
    assert Group.query.get(group.id) is None
    # Доступ до навички, наданий групою, деактивовано.
    us = UserSkill.query.filter_by(user_id=u1.id, skill_id=skill_id).first()
    assert us is None or us.is_active is False


# ---------- Пошук користувачів ----------

def test_user_search(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/users/search?q=u1", headers=auth(token))
    assert res.status_code == 200
    names = [u["username"] for u in res.get_json()]
    assert "u1" in names

    # Порожній q повертає перелік (до 20).
    assert client.get("/api/users/search", headers=auth(token)).status_code == 200
    # Не-admin — заборонено.
    assert client.get("/api/users/search?q=u",
                      headers=auth(login(client, "u1", "pass"))).status_code == 403
