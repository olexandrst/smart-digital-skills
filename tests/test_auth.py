"""Тести автентифікації та скоупу прав (Skill Manager не лізе до користувачів)."""
import pytest

from app.core.security import hash_password
from app.extensions import db
from app.models import User
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


# --- Регістр у полі входу ---
#
# Корпоративні логіни — це пошта виду Ім'я.Прізвище@домен, і люди вводять її
# то з великої, то з малої літери, то з пробілом із буфера обміну. Усі ці
# варіанти мають вести в той самий обліковий запис.

@pytest.fixture
def corp_user(app):
    with app.app_context():
        u = User(username="Oleksandr.Stasiuk@metinvest.digital",
                 email="Oleksandr.Stasiuk@metinvest.digital",
                 password_hash=hash_password("pass"), is_active=True)
        db.session.add(u)
        db.session.commit()
    return "Oleksandr.Stasiuk@metinvest.digital"


@pytest.mark.parametrize("typed", [
    "Oleksandr.Stasiuk@metinvest.digital",     # точно як у базі
    "oleksandr.stasiuk@metinvest.digital",     # усе з малої
    "OLEKSANDR.STASIUK@METINVEST.DIGITAL",     # усе з великої
    "  Oleksandr.Stasiuk@metinvest.digital ",  # з пробілами з буфера
])
def test_login_ignores_case_and_spaces(client, corp_user, typed):
    res = client.post("/api/auth/login",
                      json={"username": typed, "password": "pass"})
    assert res.status_code == 200, res.get_data(as_text=True)


def test_login_case_insensitive_for_plain_username(client):
    res = client.post("/api/auth/login",
                      json={"username": "ADMIN", "password": "Admin123!"})
    assert res.status_code == 200


def test_login_unknown_user_still_rejected(client):
    res = client.post("/api/auth/login",
                      json={"username": "nobody@metinvest.digital",
                            "password": "pass"})
    assert res.status_code == 401


def test_exact_match_wins_over_case_folded(client, app):
    """Якщо в базі історично є два записи, що різняться лише регістром,
    точний збіг має вести у свій обліковий запис, а не в чужий."""
    with app.app_context():
        db.session.add_all([
            User(username="Twin", password_hash=hash_password("one"),
                 is_active=True),
            User(username="twin", password_hash=hash_password("two"),
                 is_active=True),
        ])
        db.session.commit()

    assert client.post("/api/auth/login",
                       json={"username": "Twin", "password": "one"}
                       ).status_code == 200
    assert client.post("/api/auth/login",
                       json={"username": "twin", "password": "two"}
                       ).status_code == 200


def test_cannot_create_user_differing_only_by_case(client):
    """Нові дублікати за регістром більше не створюються: інакше вхід став би
    непередбачуваним."""
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/users", headers=auth(token),
                      json={"username": "ADMIN", "password": "pass"})
    assert res.status_code == 409


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
