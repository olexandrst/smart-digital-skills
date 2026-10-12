"""Навички як матеріали каталогу: перегляд, завантаження пакета, лічильник.

Навички більше не запускаються й не «встановлюються» — їх хостимо: будь-хто
бачить опубліковані й завантажує пакет, менеджери керують чернетками.
"""
import io
import zipfile

import pytest

from app.models import Skill, CatalogFavorite, SkillFeedback, User
from app.extensions import db
from tests.conftest import login, auth

SKILL_MD = """---
name: Аналіз договорів
description: Знаходить ризикові пункти в договорах.
author: Юридичний департамент
category: Право
version: 1.0.0
---
# Аналіз договорів

1. Завантажте договір.
2. Попросіть знайти ризикові пункти.
"""


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _upload(client, token, data):
    return client.post("/api/skills/upload", headers=auth(token),
                       data={"file": (io.BytesIO(data), "contracts.skill")},
                       content_type="multipart/form-data")


@pytest.fixture
def package(client):
    """Навичка-пакет у статусі чернетки + байти її архіву."""
    data = _zip({"SKILL.md": SKILL_MD, "prompts/risk.md": "Знайди ризики."})
    sid = _upload(client, login(client, "sm", "pass"), data).get_json()["id"]
    return sid, data


def _publish(client, sid):
    res = client.post(f"/api/skills/{sid}/status", headers=auth(login(client, "sm", "pass")),
                      json={"status": "published"})
    assert res.status_code == 200


# ---------------------------- Видимість ----------------------------

def test_draft_visible_only_to_managers(client, package):
    sid, _ = package
    u1 = auth(login(client, "u1", "pass"))
    assert client.get(f"/api/skills/{sid}", headers=u1).status_code == 404
    assert sid not in [s["id"] for s in client.get("/api/skills", headers=u1).get_json()]
    assert client.get(f"/api/skills/{sid}/download", headers=u1).status_code == 404
    assert client.get(f"/api/skills/{sid}/files", headers=u1).status_code == 404

    sm = auth(login(client, "sm", "pass"))
    assert client.get(f"/api/skills/{sid}", headers=sm).status_code == 200


def test_published_skill_shows_instructions_and_files(client, package):
    sid, _ = package
    _publish(client, sid)
    u1 = auth(login(client, "u1", "pass"))
    data = client.get(f"/api/skills/{sid}", headers=u1).get_json()
    assert data["skill_kind"] == "package"
    assert data["has_package"] is True
    assert "Попросіть знайти ризикові пункти" in data["instructions"]
    assert data["downloads_count"] == 0
    # Полів запуску в картці немає.
    for gone in ("model_id", "runtime", "entrypoint", "inputs", "activations_count"):
        assert gone not in data

    files = client.get(f"/api/skills/{sid}/files", headers=u1).get_json()
    assert {f["name"] for f in files} == {"SKILL.md", "prompts/risk.md"}


# ---------------------------- Завантаження ----------------------------

def test_any_user_downloads_published_package_and_it_is_counted(client, package):
    sid, data = package
    _publish(client, sid)
    for name in ("u1", "u2"):
        res = client.get(f"/api/skills/{sid}/download",
                         headers=auth(login(client, name, "pass")))
        assert res.status_code == 200
        assert res.data == data            # віддається той самий архів
        assert res.headers["Content-Disposition"].startswith("attachment")
    assert Skill.query.get(sid).downloads_count == 2


def test_manager_checking_a_draft_is_not_counted(client, package):
    sid, data = package
    res = client.get(f"/api/skills/{sid}/download", headers=auth(login(client, "sm", "pass")))
    assert res.status_code == 200
    assert res.data == data
    assert Skill.query.get(sid).downloads_count == 0


def test_skill_without_package_cannot_be_downloaded(client):
    sid = Skill.query.filter_by(name="Summarizer").first().id
    res = client.get(f"/api/skills/{sid}/download", headers=auth(login(client, "u1", "pass")))
    assert res.status_code == 404
    assert res.get_json()["error"] == "package_missing"


def test_download_requires_auth(client, package):
    sid, _ = package
    _publish(client, sid)
    assert client.get(f"/api/skills/{sid}/download").status_code == 401


# ---------------------------- Що прибрано ----------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/api/skills/{sid}/activate"),
    ("post", "/api/skills/{sid}/deactivate"),
    ("post", "/api/skills/{sid}/run"),
    ("get", "/api/skills/mine"),
    ("get", "/api/skills/settings"),
    ("get", "/api/skills/access-matrix"),
    ("post", "/api/skills/access"),
    ("get", "/api/chat/sessions"),
    ("get", "/api/models"),
    ("get", "/api/groups"),
    ("get", "/api/usage/me"),
    ("get", "/api/files"),
    ("get", "/api/users/search"),
    ("post", "/api/users/{uid}/token-limit"),
])
def test_removed_endpoints_are_gone(client, method, path):
    sid = Skill.query.filter_by(name="Summarizer").first().id
    uid = User.query.filter_by(username="u1").first().id
    res = getattr(client, method)(path.format(sid=sid, uid=uid),
                                  headers=auth(login(client, "admin", "Admin123!")))
    assert res.status_code in (404, 405)


# ---------------------------- Видалення ----------------------------

def test_delete_removes_favorites_keeps_feedback(client, package):
    sid, _ = package
    _publish(client, sid)
    u1 = auth(login(client, "u1", "pass"))
    client.post("/api/catalog/favorites", headers=u1,
                json={"item_type": "skill", "item_id": sid})
    client.post(f"/api/skills/{sid}/feedback", headers=u1,
                json={"message": "Корисно", "rating": 5})

    res = client.delete(f"/api/skills/{sid}", headers=auth(login(client, "admin", "Admin123!")))
    assert res.status_code == 200
    assert db.session.get(Skill, sid) is None
    assert CatalogFavorite.query.filter_by(item_type="skill", item_id=sid).count() == 0
    fb = SkillFeedback.query.filter_by(skill_name="Аналіз договорів").one()
    assert fb.skill_id is None and fb.rating == 5   # знімок назви лишився
