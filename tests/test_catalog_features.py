"""Тести нових можливостей каталогу: категорії, іконки, вилучення навички."""
import base64
import io
from app.models import Skill, SkillCategory, UserSkill, User
from tests.conftest import login, auth
from tests.test_catalog_mgmt import make_zip, upload, MD_FULL, MAIN_PY

# Мінімальний валідний 1×1 PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _summarizer_id():
    return Skill.query.filter_by(name="Summarizer").first().id


# ---------- Вилучення (self-deactivate) ----------

def test_install_then_remove_skill(client):
    sid = _summarizer_id()
    u1 = login(client, "u1", "pass")

    res = client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    assert res.status_code == 200
    assert res.get_json()["activations_count"] == 1

    res = client.post(f"/api/skills/{sid}/deactivate", headers=auth(u1))
    assert res.status_code == 200
    assert res.get_json()["activations_count"] == 0

    uid = User.query.filter_by(username="u1").first().id
    assert UserSkill.query.filter_by(user_id=uid, skill_id=sid,
                                     is_active=True).first() is None


def test_remove_when_not_installed_is_noop(client):
    sid = _summarizer_id()
    u1 = login(client, "u1", "pass")
    res = client.post(f"/api/skills/{sid}/deactivate", headers=auth(u1))
    assert res.status_code == 200
    assert res.get_json()["activations_count"] == 0


# ---------- Категорії ----------

def test_category_crud_and_backfill(client):
    sm = login(client, "sm", "pass")
    # Завантажуємо навичку з категорією «Мова» — GET має її «підхопити».
    upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY}))

    res = client.get("/api/categories", headers=auth(sm))
    assert res.status_code == 200
    names = {c["name"] for c in res.get_json()}
    assert "Мова" in names  # бекфіл із наявних категорій навичок

    # Додаємо нову категорію.
    res = client.post("/api/categories", headers=auth(sm), json={"name": "Аналітика"})
    assert res.status_code == 201
    cat_id = res.get_json()["id"]

    # Дублікат (без урахування регістру) — відхиляється.
    assert client.post("/api/categories", headers=auth(sm),
                       json={"name": "аналітика"}).status_code == 409
    # Порожня назва — помилка.
    assert client.post("/api/categories", headers=auth(sm),
                       json={"name": "  "}).status_code == 400

    # Видалення.
    assert client.delete(f"/api/categories/{cat_id}", headers=auth(sm)).status_code == 200
    assert SkillCategory.query.get(cat_id) is None


def test_category_requires_manager_role(client):
    u1 = login(client, "u1", "pass")
    # Звичайний користувач бачить список, але не керує ним.
    assert client.get("/api/categories", headers=auth(u1)).status_code == 200
    assert client.post("/api/categories", headers=auth(u1),
                       json={"name": "X"}).status_code == 403


# ---------- Іконки навичок ----------

def _upload_icon(client, token, sid, data=PNG_BYTES, filename="icon.png"):
    return client.post(f"/api/skills/{sid}/icon", headers=auth(token),
                       data={"file": (io.BytesIO(data), filename)},
                       content_type="multipart/form-data")


def test_icon_upload_get_and_delete(client):
    sm = login(client, "sm", "pass")
    sid = _summarizer_id()

    # До завантаження — has_icon False, GET віддає 404.
    assert client.get(f"/api/skills/{sid}", headers=auth(sm)).get_json()["has_icon"] is False
    assert client.get(f"/api/skills/{sid}/icon").status_code == 404

    res = _upload_icon(client, sm, sid)
    assert res.status_code == 200
    d = res.get_json()
    assert d["has_icon"] is True
    assert d["icon_url"].startswith(f"/api/skills/{sid}/icon")

    # Публічний GET (без токена) віддає PNG.
    res = client.get(f"/api/skills/{sid}/icon")
    assert res.status_code == 200
    assert res.data == PNG_BYTES
    assert res.mimetype == "image/png"

    # Видалення повертає навичку до стандартної іконки.
    res = client.delete(f"/api/skills/{sid}/icon", headers=auth(sm))
    assert res.status_code == 200
    assert res.get_json()["has_icon"] is False
    assert client.get(f"/api/skills/{sid}/icon").status_code == 404


def test_icon_rejects_non_png(client):
    sm = login(client, "sm", "pass")
    sid = _summarizer_id()
    res = _upload_icon(client, sm, sid, data=b"GIF89a-not-a-png", filename="x.png")
    assert res.status_code == 400


def test_icon_requires_manager_role(client):
    u1 = login(client, "u1", "pass")
    sid = _summarizer_id()
    assert _upload_icon(client, u1, sid).status_code == 403


def test_published_at_exposed_in_dict(client):
    sm = login(client, "sm", "pass")
    sid = _summarizer_id()
    d = client.get(f"/api/skills/{sid}", headers=auth(sm)).get_json()
    assert "published_at" in d and "updated_at" in d
