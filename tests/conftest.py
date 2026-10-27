import os
import pytest
from app import create_app
from app.config import BaseConfig
from app.extensions import db as _db
from app.core.security import hash_password
from app.models import Role, User, UserRole, Skill, CatalogSection


import tempfile
_TMP = tempfile.mkdtemp(prefix="sph_tests_")


class TestConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    JWT_SECRET_KEY = "test"
    SECRET_KEY = "test"
    AUTO_MIGRATE = False  # тести керують схемою самостійно
    SYNC_REFERENCE_DATA = False  # довідники наповнюються явно в тестах
    ENABLE_SCHEDULER = False  # без фонового планувальника у тестах
    # Артефакти тестів — у тимчасову теку, не у instance/.
    SKILL_PACKAGES_DIR = os.path.join(_TMP, "skill_packages")
    SKILL_ICONS_DIR = os.path.join(_TMP, "skill_icons")
    USER_FILES_DIR = os.path.join(_TMP, "user_files")


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.create_all()
        _seed_minimal()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# Розділ, який модерує менеджер `sm` (див. app/core/access.py): його id
# тести передають у section_id, коли менеджер має діяти як модератор.
SM_SECTION = "Розділ менеджера"


def _seed_minimal():
    admin_role = Role(code="admin", name="Admin")
    sm_role = Role(code="section_manager", name="Менеджер розділу")
    _db.session.add_all([admin_role, sm_role])
    section = CatalogSection(name=SM_SECTION)
    _db.session.add(section)
    _db.session.flush()

    admin = User(username="admin", password_hash=hash_password("Admin123!"),
                 is_system_admin=True, is_active=True)
    sm = User(username="sm", password_hash=hash_password("pass"), is_active=True)
    u1 = User(username="u1", password_hash=hash_password("pass"), is_active=True)
    u2 = User(username="u2", password_hash=hash_password("pass"), is_active=True)
    _db.session.add_all([admin, sm, u1, u2])
    _db.session.flush()
    _db.session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
    _db.session.add(UserRole(user_id=sm.id, role_id=sm_role.id))
    sm.managed_sections = [section]

    _db.session.add(Skill(name="Summarizer", description="desc", status="published",
                          prompt_template="{text}", section_id=section.id))
    _db.session.commit()


def login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    return res.get_json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def sm_section_id():
    """id розділу, який модерує менеджер `sm`."""
    return CatalogSection.query.filter_by(name=SM_SECTION).one().id
