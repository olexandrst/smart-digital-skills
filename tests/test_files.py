"""Тести файлів користувача: завантаження, звантаження, ізоляція, файли від скіла."""
import io
import zipfile
from tests.conftest import login, auth


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_list_download_delete(client):
    token = login(client, "u1", "pass")
    res = client.post("/api/files", headers=auth(token),
                      data={"file": (io.BytesIO(b"hello data"), "note.txt")},
                      content_type="multipart/form-data")
    assert res.status_code == 201
    fid = res.get_json()["id"]

    # Перелік
    files = client.get("/api/files", headers=auth(token)).get_json()
    assert any(f["id"] == fid for f in files)

    # Звантаження повертає той самий вміст
    dl = client.get(f"/api/files/{fid}/download", headers=auth(token))
    assert dl.status_code == 200
    assert dl.data == b"hello data"

    # Видалення
    assert client.delete(f"/api/files/{fid}", headers=auth(token)).status_code == 200
    assert client.get(f"/api/files/{fid}/download", headers=auth(token)).status_code == 404


def test_files_are_isolated_per_user(client):
    t1 = login(client, "u1", "pass")
    fid = client.post("/api/files", headers=auth(t1),
                      data={"file": (io.BytesIO(b"secret"), "a.txt")},
                      content_type="multipart/form-data").get_json()["id"]

    # Інший користувач не бачить і не може звантажити чужий файл.
    t2 = login(client, "u2", "pass")
    assert client.get(f"/api/files/{fid}/download", headers=auth(t2)).status_code == 404
    assert all(f["id"] != fid for f in client.get("/api/files", headers=auth(t2)).get_json())


def test_users_get_distinct_storage_uid(app):
    from app.models import User
    from app.services import file_service
    u1 = User.query.filter_by(username="u1").first()
    u2 = User.query.filter_by(username="u2").first()
    d1 = file_service.user_dir(u1)
    d2 = file_service.user_dir(u2)
    assert d1 != d2
    assert u1.storage_uid and u2.storage_uid and u1.storage_uid != u2.storage_uid


SKILL_MD = """---
name: File Maker
description: Створює файл result.txt під час виконання.
version: 1.0.0
runtime: python
entrypoint: main.py
inputs:
  - name: text
    required: true
---
# File Maker
"""

MAIN_PY = (
    "import sys, json\n"
    "data = json.load(sys.stdin)\n"
    "text = data.get('inputs', {}).get('text', '')\n"
    "open('result.txt', 'w', encoding='utf-8').write(text)\n"
    "print(json.dumps({'output': 'Файл створено'}, ensure_ascii=False))\n"
)


def test_skill_generated_file_is_saved(client):
    sm = login(client, "sm", "pass")
    sid = client.post(
        "/api/skills/upload", headers=auth(sm),
        data={"file": (io.BytesIO(make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY})),
                       "fm.zip")},
        content_type="multipart/form-data").get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})

    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    res = client.post(f"/api/skills/{sid}/run", headers=auth(u1),
                      json={"inputs": {"text": "вміст файлу"}})
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["files"]) == 1
    assert data["files"][0]["filename"] == "result.txt"

    # Файл реально збережений і доступний для звантаження.
    fid = data["files"][0]["id"]
    dl = client.get(f"/api/files/{fid}/download", headers=auth(u1))
    assert dl.status_code == 200
    assert dl.data.decode("utf-8") == "вміст файлу"

    # І зʼявляється у переліку файлів користувача (зберігається назавжди).
    files = client.get("/api/files", headers=auth(u1)).get_json()
    assert any(f["id"] == fid and f["source"] == "skill_run" for f in files)
