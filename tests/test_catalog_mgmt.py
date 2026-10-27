"""Тести менеджменту каталогу навичок: атрибути, публікація, версії, видалення."""
import io
import zipfile
from app.models import Skill
from tests.conftest import login, auth, sm_section_id

MD_FULL = """---
name: Перекладач
description: Перекладає текст.
author: Іван Тестовий
category: Мова
version: 1.2.0
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


def upload(client, token, zip_bytes, skill_id=None, filename="skill.skill", status=None):
    """Завантаження пакета. Нова навичка лягає в розділ менеджера `sm`: там
    він модерує, а інші користувачі подають навички на розгляд."""
    url = f"/api/skills/{skill_id}/upload" if skill_id else "/api/skills/upload"
    data = {"file": (io.BytesIO(zip_bytes), filename)}
    if not skill_id:
        data["section_id"] = str(sm_section_id())
    if status:
        data["status"] = status
    return client.post(url, headers=auth(token), data=data,
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

    # Невідомі статуси не приймаються.
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

def test_upload_new_version_keeps_identity_and_state(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL, "main.py": MAIN_PY})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})
    u1 = login(client, "u1", "pass")
    client.get(f"/api/skills/{sid}/download", headers=auth(u1))

    v2 = make_zip({"SKILL.md": MD_V2, "main.py": MAIN_PY})
    res = upload(client, sm, v2, skill_id=sid)
    assert res.status_code == 200
    d = res.get_json()
    assert d["id"] == sid                      # та сама навичка
    assert d["version"] == "2.0.0"             # нова версія
    assert "(v2)" in d["description"]
    assert d["status"] == "published"          # публікація не скидається
    assert d["downloads_count"] == 1           # історія завантажень збережена
    # Завантажується вже новий архів.
    assert client.get(f"/api/skills/{sid}/download", headers=auth(u1)).data == v2


def test_user_may_not_manage_foreign_skill(client):
    """Користувач завантажує власні навички, але чужими не керує."""
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": MD_FULL})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})
    u1 = login(client, "u1", "pass")
    assert upload(client, u1, make_zip({"SKILL.md": MD_FULL})).status_code == 201
    assert upload(client, u1, make_zip({"SKILL.md": MD_V2}), skill_id=sid).status_code == 403
    assert client.patch(f"/api/skills/{sid}", headers=auth(u1),
                        json={"name": "X"}).status_code == 403
    assert client.post(f"/api/skills/{sid}/status", headers=auth(u1),
                       json={"status": "draft"}).status_code == 403
    assert client.delete(f"/api/skills/{sid}", headers=auth(u1)).status_code == 403
    assert Skill.query.get(sid).name == "Перекладач"
