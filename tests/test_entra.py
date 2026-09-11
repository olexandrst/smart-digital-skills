"""Корпоративний вхід через Entra ID (AKH-16). Тести не ходять у мережу."""
import pytest

from app.extensions import db
from app.models import User, Role, UserRole
from app.services import entra_service


def _login(client, username, password="pass"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


@pytest.fixture
def entra_app(app):
    """Застосунок із налаштованим каталогом — без звернень до мережі."""
    app.config.update(
        ENTRA_TENANT_ID="tenant-123",
        ENTRA_CLIENT_ID="client-abc",
        ENTRA_REDIRECT_URI="https://hub.example.com/api/auth/entra/callback",
        ENTRA_ADMIN_GROUPS="grp-admin",
        ENTRA_MANAGER_GROUPS="grp-managers,grp-knowledge",
    )
    return app


def _claims(**over):
    payload = {
        "oid": "entra-user-1",
        "sub": "subject-1",
        "preferred_username": "olena@metinvest.example",
        "name": "Олена Мембер",
        "department": "Закупівлі",
        "groups": [],
    }
    payload.update(over)
    return payload


# --------------------------- Налаштування ---------------------------

def test_config_reports_available_methods(client, app):
    data = client.get("/api/auth/config").get_json()
    assert data["entra_enabled"] is False      # за замовчуванням вимкнено
    assert data["password_enabled"] is True


def test_config_reports_entra_when_configured(client, entra_app):
    assert client.get("/api/auth/config").get_json()["entra_enabled"] is True


def test_entra_start_rejected_when_not_configured(client, app):
    assert client.get("/api/auth/entra/start").status_code == 400


def test_entra_start_redirects_to_directory(client, entra_app):
    res = client.get("/api/auth/entra/start")
    assert res.status_code == 302
    target = res.headers["Location"]
    assert target.startswith("https://login.microsoftonline.com/tenant-123/v2.0/authorize")
    assert "client_id=client-abc" in target
    assert "code_challenge_method=S256" in target   # PKCE обов'язковий
    assert "state=" in target


def test_callback_rejects_foreign_state(client, entra_app):
    """Підроблене повернення без збігу state вхід не завершує."""
    client.get("/api/auth/entra/start")
    res = client.get("/api/auth/entra/callback?code=abc&state=підроблений")
    assert res.status_code == 400
    assert "сесія входу" in res.get_json()["message"].lower()


def test_callback_without_code_rejected(client, entra_app):
    with client.session_transaction() as sess:
        sess["entra_state"] = "s1"
        sess["entra_verifier"] = "v1"
    assert client.get("/api/auth/entra/callback?state=s1").status_code == 400


def test_callback_reports_directory_error(client, entra_app):
    res = client.get("/api/auth/entra/callback?error=access_denied"
                     "&error_description=Користувач+скасував")
    assert res.status_code == 401


# ------------------- Створення й оновлення користувача -------------------

def test_first_login_creates_user_from_directory(client, entra_app):
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        user = upsert_entra_user(_claims())
        assert user.external_id == "entra-user-1"
        assert user.auth_provider == "entra"
        assert user.email == "olena@metinvest.example"
        assert user.full_name == "Олена Мембер"
        assert user.department == "Закупівлі"
        assert user.password_hash is None       # пароль такому акаунту не потрібен


def test_group_membership_grants_role(client, entra_app):
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        user = upsert_entra_user(_claims(groups=["grp-managers"]))
        assert user.role_codes == ["skill_manager"]
        assert user.is_system_admin is False


def test_admin_group_grants_system_admin(client, entra_app):
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        user = upsert_entra_user(_claims(groups=["grp-admin"]))
        assert "admin" in user.role_codes and user.is_system_admin is True


def test_role_revoked_when_group_membership_ends(client, entra_app):
    """Членство в групі — єдине джерело правди: вихід із групи знімає доступ."""
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        upsert_entra_user(_claims(groups=["grp-admin"]))
        user = upsert_entra_user(_claims(groups=[]))
        assert user.role_codes == [] and user.is_system_admin is False


def test_existing_password_account_is_linked_not_duplicated(client, entra_app):
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        before = User.query.count()
        existing = User.query.filter_by(username="u1").first()
        existing.email = "olena@metinvest.example"
        db.session.commit()

        user = upsert_entra_user(_claims())
        assert user.id == existing.id
        assert User.query.count() == before      # другий запис не з'явився


def test_repeat_login_does_not_duplicate_user(client, entra_app):
    from app.api.auth import upsert_entra_user
    with entra_app.app_context():
        before = User.query.count()
        upsert_entra_user(_claims())
        upsert_entra_user(_claims())
        assert User.query.count() == before + 1


def test_claims_without_subject_rejected(client, entra_app):
    from app.api.auth import upsert_entra_user
    from app.core.errors import ApiError
    with entra_app.app_context():
        with pytest.raises(ApiError):
            upsert_entra_user({"name": "Без ідентифікатора"})


# ------------------------- Парольний вхід -------------------------

def test_password_login_works_by_default(client, app):
    assert _login(client, "u1").status_code == 200


def test_password_login_can_be_switched_off(client, entra_app):
    """Вимкнений парольний вхід має бути справді недоступним."""
    entra_app.config["ENTRA_ALLOW_PASSWORD_LOGIN"] = False
    res = _login(client, "u1")
    assert res.status_code == 403
    assert res.get_json()["error"] == "password_login_disabled"
    assert client.get("/api/auth/config").get_json()["password_enabled"] is False


def test_service_account_still_enters_when_password_allowed(client, entra_app):
    entra_app.config["ENTRA_ALLOW_PASSWORD_LOGIN"] = True
    assert _login(client, "admin", "Admin123!").status_code == 200


# ----------------------------- Вихід -----------------------------

def test_logout_url_ends_corporate_session(client, entra_app):
    token = _login(client, "u1").get_json()["access_token"]
    res = client.get("/api/auth/entra/logout",
                     headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "login.microsoftonline.com/tenant-123/v2.0/logout" in res.get_json()["logout_url"]


def test_logout_url_absent_without_entra(client, app):
    token = _login(client, "u1").get_json()["access_token"]
    assert client.get("/api/auth/entra/logout",
                      headers={"Authorization": f"Bearer {token}"}
                      ).get_json()["logout_url"] is None


# --------------------------- Службові ---------------------------

def test_pkce_pair_is_unique_and_well_formed(app):
    with app.app_context():
        v1, c1 = entra_service.new_pkce_pair()
        v2, _c2 = entra_service.new_pkce_pair()
        assert v1 != v2 and len(v1) >= 43
        assert "=" not in c1 and "+" not in c1 and "/" not in c1


def test_role_mapping_reads_both_groups_and_roles_claims(entra_app):
    with entra_app.app_context():
        assert entra_service.role_codes_for({"roles": ["grp-knowledge"]}) \
            == ["skill_manager"]
        assert entra_service.role_codes_for({"groups": ["невідома"]}) == []
