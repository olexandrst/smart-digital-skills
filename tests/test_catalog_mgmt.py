"""Тести менеджменту каталогу навичок: атрибути, публікація, версії, видалення."""
import io
import zipfile
from app.models import Skill, UserSkill, GroupSkill, Group, GroupMembership, User
from app.extensions import db
from app.services import skill_service
from tests.conftest import login, auth

MD_FULL = """---
name: Перекладач
description: Перекладає текст.
author: Іван Тестовий
category: Мова
version: 1.2.0
runtime: python
entrypoint: main.py
inputs:
  - name: text
    required: true
---
# Перекладач
"""

MD_V2 = MD_FULL.replace("version: 1.2.0", "version: 2.0.0").replace(
    "Перекладає текст.", "Перекладає текст (v2).")

MD_MINIMAL = """---
name: Мінімальна
---
# Мінімальна навичка без автора й категорії
"""

MAIN_PY = (
    "import sys, json\n"
    "d = json.load(sys.stdin)\n"
    "print(json.dumps({'output': d.get('inputs', {}).get('text', '').upper()}, ensure_ascii=False))\n"
)


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def upload(client, token, zip_bytes, skill_id=None, filename="skill.skill"):
    url = f"/api/skills/{skill_id}/upload" if skill_id else "/api/skills/upload"
    return client.post(url, headers=auth(token),
                       data={"file": (io.BytesIO(zip_bytes), filename)},
                       content_type="multipart/form-data")


# ---------- Атрибути зі skill.md та статус нової навички ----------

def test_upload_parses_attributes_and_is_unpublished(client):
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY}))
    assert res.status_code == 201
    d = res.get_json()
    assert d["name"] == "Перекладач"
    assert d["version"] == "1.2.0"
    assert d["author"] == "Іван Тестовий"
    assert d["category"] == "Мова"
    assert d["status"] == "draft"  # «Не опублікована»


def test_upload_minimal_gets_defaults(client):
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"SKILL.md": MD_MINIMAL}))
    assert res.status_code == 201
    d = res.get_json()
    assert d["category"] == "Загальне"
    assert d["author"]  # ім'я завантажувача за замовчуванням


# ---------- Публікація / зняття з публікації ----------

def test_publish_and_unpublish(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})).get_json()["id"]

    res = client.post(f"/api/skills/{sid}/status", headers=auth(sm),
                      json={"status": "published"})
    assert res.get_json()["status"] == "published"

    res = client.post(f"/api/skills/{sid}/status", headers=auth(sm),
                      json={"status": "draft"})
    assert res.get_json()["status"] == "draft"

    # Інші статуси більше не приймаються.
    res = client.post(f"/api/skills/{sid}/status", headers=auth(sm),
                      json={"status": "testing"})
    assert res.status_code == 400


# ---------- Редагування атрибутів ----------

def test_edit_attributes(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})).get_json()["id"]
    res = client.patch(f"/api/skills/{sid}", headers=auth(sm), json={
        "name": "Перекладач Pro", "version": "1.3.0",
        "author": "Команда R&D", "category": "Переклад",
        "description": "Оновлений опис."})
    d = res.get_json()
    assert (d["name"], d["version"], d["author"], d["category"]) == \
        ("Перекладач Pro", "1.3.0", "Команда R&D", "Переклад")

    # Порожня назва — помилка.
    res = client.patch(f"/api/skills/{sid}", headers=auth(sm), json={"name": " "})
    assert res.status_code == 400


# ---------- Ручного створення через форму немає ----------

def test_manual_create_endpoint_removed(client):
    sm = login(client, "sm", "pass")
    res = client.post("/api/skills", headers=auth(sm),
                      json={"name": "X", "description": "Y", "model_id": 1})
    assert res.status_code == 405  # ендпоінт прибрано


# ---------- Нова версія в контексті навички ----------

def test_upload_new_version_keeps_identity_and_links(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})

    # u1 активує навичку — зв'язок має пережити оновлення версії.
    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))

    res = upload(client, sm, make_zip({"SKILL.md": MD_V2, "main.py": MAIN_PY}), skill_id=sid)
    assert res.status_code == 200
    d = res.get_json()
    assert d["id"] == sid                      # та сама навичка
    assert d["version"] == "2.0.0"             # нова версія
    assert "(v2)" in d["description"]
    assert d["activations_count"] == 1         # активації збережено

    uid = User.query.filter_by(username="u1").first().id
    assert UserSkill.query.filter_by(user_id=uid, skill_id=sid, is_active=True).first()


# ---------- Скачування пакета ----------

def test_download_package(client):
    sm = login(client, "sm", "pass")
    zip_bytes = make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})
    sid = upload(client, sm, zip_bytes).get_json()["id"]
    res = client.get(f"/api/skills/{sid}/download", headers=auth(sm))
    assert res.status_code == 200
    assert res.data == zip_bytes  # віддається той самий архів

    # Звичайному користувачу скачування менеджменту недоступне.
    u1 = login(client, "u1", "pass")
    assert client.get(f"/api/skills/{sid}/download", headers=auth(u1)).status_code == 403


# ---------- Видалення чистить усі зв'язки ----------

def test_delete_removes_all_links(client, app):
    sm = login(client, "sm", "pass")
    admin = login(client, "admin", "Admin123!")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})

    # Створюємо групу, призначаємо навичку групі; u2 активує самостійно.
    admin_user = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()
    group = Group(name="DelG", created_by=admin_user.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id,
                                   role="manager", status="active"))
    db.session.commit()
    skill_service.assign_to_group(group.id, sid, admin_user.id)
    u2 = login(client, "u2", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u2))

    assert UserSkill.query.filter_by(skill_id=sid).count() >= 2
    assert GroupSkill.query.filter_by(skill_id=sid).count() == 1

    res = client.delete(f"/api/skills/{sid}", headers=auth(admin))
    assert res.status_code == 200

    # Усі зв'язки видалено — повторне встановлення вимагатиме нової активації.
    assert Skill.query.get(sid) is None
    assert UserSkill.query.filter_by(skill_id=sid).count() == 0
    assert GroupSkill.query.filter_by(skill_id=sid).count() == 0
