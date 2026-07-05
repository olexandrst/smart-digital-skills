"""Тести менеджменту користувачів (Admin): видалення, групи користувача."""
from app.models import User, Group, GroupMembership, UserSkill, ChatSession
from app.extensions import db
from app.services import group_service, skill_service
from tests.conftest import login, auth


def _uid(username):
    return User.query.filter_by(username=username).first().id


def test_create_edit_delete_user(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/users", headers=auth(token), json={
        "username": "temp", "full_name": "Тимчасовий", "password": "pass12"})
    assert res.status_code == 201
    uid = res.get_json()["id"]

    res = client.patch(f"/api/users/{uid}", headers=auth(token),
                       json={"full_name": "Оновлений", "email": "t@e.com"})
    assert res.get_json()["full_name"] == "Оновлений"

    res = client.delete(f"/api/users/{uid}", headers=auth(token))
    assert res.status_code == 200
    assert User.query.get(uid) is None


def test_delete_user_cleans_memberships(client):
    token = login(client, "admin", "Admin123!")
    gid = client.post("/api/groups", headers=auth(token), json={"name": "DG"}).get_json()["id"]
    uid = _uid("u2")
    client.post(f"/api/groups/{gid}/members", headers=auth(token), json={"user_id": uid})
    assert GroupMembership.query.filter_by(user_id=uid).count() >= 1

    client.delete(f"/api/users/{uid}", headers=auth(token))
    assert GroupMembership.query.filter_by(user_id=uid).count() == 0


def test_cannot_delete_self_or_system_admin(client):
    token = login(client, "admin", "Admin123!")
    admin_id = _uid("admin")
    res = client.delete(f"/api/users/{admin_id}", headers=auth(token))
    assert res.status_code == 400  # і self, і system_admin


def test_user_groups_list_and_remove(client):
    token = login(client, "admin", "Admin123!")
    gid = client.post("/api/groups", headers=auth(token), json={"name": "UG"}).get_json()["id"]
    uid = _uid("u1")
    client.post(f"/api/groups/{gid}/members", headers=auth(token), json={"user_id": uid})

    res = client.get(f"/api/users/{uid}/groups", headers=auth(token))
    assert res.status_code == 200
    assert gid in [g["id"] for g in res.get_json()]

    res = client.delete(f"/api/users/{uid}/groups/{gid}", headers=auth(token))
    assert res.status_code == 200
    res = client.get(f"/api/users/{uid}/groups", headers=auth(token))
    assert gid not in [g["id"] for g in res.get_json()]


def test_non_admin_cannot_manage_users(client):
    token = login(client, "u1", "pass")
    uid = _uid("u2")
    assert client.delete(f"/api/users/{uid}", headers=auth(token)).status_code == 403
    assert client.get(f"/api/users/{uid}/groups", headers=auth(token)).status_code == 403
