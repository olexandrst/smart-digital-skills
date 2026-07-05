"""Тести автентифікації та скоупу прав (Skill Manager не лізе до користувачів)."""
from tests.conftest import login, auth


def test_login_success(client):
    res = client.post("/api/auth/login",
                      json={"username": "admin", "password": "Admin123!"})
    assert res.status_code == 200
    assert "access_token" in res.get_json()


def test_login_bad_credentials(client):
    res = client.post("/api/auth/login",
                      json={"username": "admin", "password": "wrong"})
    assert res.status_code == 401


def test_skill_manager_cannot_list_users(client):
    token = login(client, "sm", "pass")
    res = client.get("/api/users", headers=auth(token))
    assert res.status_code == 403


def test_admin_can_list_users(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/users", headers=auth(token))
    assert res.status_code == 200


def test_member_sees_only_published_catalog(client):
    token = login(client, "u1", "pass")
    res = client.get("/api/skills", headers=auth(token))
    assert res.status_code == 200
    assert all(s["status"] == "published" for s in res.get_json())
