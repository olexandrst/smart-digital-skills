"""Пакети навичок: розбір skill.md, безпека архіву, перелік файлів.

Пакет хостимо, а не виконуємо: важливо, щоб його атрибути читалися з
skill.md, небезпечний архів не потрапляв у каталог, а користувач бачив, що
саме завантажує.
"""
import io
import zipfile
import pytest
from app.services import package_service
from app.core.errors import ApiError
from tests.conftest import login, auth

SKILL_MD = """---
name: Echo Upper
description: Повертає текст у верхньому регістрі.
author: R&D
category: Текст
version: 1.4.0
runtime: python
entrypoint: main.py
inputs:
  - name: text
    required: true
---
# Echo Upper
Запустіть `python main.py` і передайте текст.
"""

MAIN_PY = "import sys, json\nprint(json.dumps({'output': sys.stdin.read().upper()}))\n"


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def upload(client, token, zip_bytes, filename="skill.zip"):
    return client.post(
        "/api/skills/upload", headers=auth(token),
        data={"file": (io.BytesIO(zip_bytes), filename)},
        content_type="multipart/form-data")


# ---------- Розбір ----------

def test_parse_package_reads_frontmatter_and_instructions(app):
    meta = package_service.parse_package(make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY}))
    assert meta == {
        "name": "Echo Upper",
        "description": "Повертає текст у верхньому регістрі.",
        "author": "R&D",
        "category": "Текст",
        "version": "1.4.0",
        "instructions": "# Echo Upper\nЗапустіть `python main.py` і передайте текст.",
    }


def test_package_without_code_is_fine(app):
    """Пакету-«рецепту» з самим skill.md достатньо: entrypoint ніхто не шукає."""
    meta = package_service.parse_package(make_zip({"SKILL.md": SKILL_MD}))
    assert meta["name"] == "Echo Upper"


def test_description_defaults_to_name(app):
    md = "---\nname: Лише назва\n---\nТіло"
    assert package_service.parse_package(make_zip({"SKILL.md": md}))["description"] == "Лише назва"


def test_parse_package_missing_skill_md(app):
    with pytest.raises(ApiError):
        package_service.parse_package(make_zip({"main.py": MAIN_PY}))


def test_parse_package_requires_name(app):
    with pytest.raises(ApiError):
        package_service.parse_package(make_zip({"SKILL.md": "---\ndescription: x\n---\n"}))


def test_not_a_zip_rejected(app):
    with pytest.raises(ApiError):
        package_service.parse_package(b"definitely not a zip")


def test_zip_slip_rejected(app):
    bad = make_zip({"skill.md": SKILL_MD, "../evil.py": "x=1"})
    with pytest.raises(ApiError):
        package_service.parse_package(bad)


def test_zip_bomb_rejected(app):
    app.config["SKILL_PACKAGE_MAX_UNZIPPED"] = 1024
    try:
        big = make_zip({"skill.md": SKILL_MD, "data.txt": "x" * 4096})
        with pytest.raises(ApiError):
            package_service.parse_package(big)
    finally:
        app.config["SKILL_PACKAGE_MAX_UNZIPPED"] = 50 * 1024 * 1024


# ---------- Через API ----------

def test_upload_requires_role(client):
    token = login(client, "u1", "pass")  # звичайний користувач
    res = upload(client, token, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY}))
    assert res.status_code == 403


def test_upload_rejects_wrong_extension(client):
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"skill.md": SKILL_MD}), filename="skill.rar")
    assert res.status_code == 400


def test_upload_package_in_top_level_folder(client):
    """Архів з однією верхньою текою (pkg/skill.md, pkg/main.py) приймається."""
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"pkg/skill.md": SKILL_MD, "pkg/main.py": MAIN_PY}),
                 filename="pkg.skill")
    assert res.status_code == 201
    data = res.get_json()
    assert data["skill_kind"] == "package"
    assert data["package_filename"] == "pkg.skill"
    assert data["instructions"].startswith("# Echo Upper")


def test_list_package_files(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY,
                                       "lib/helper.py": "x=1"})).get_json()["id"]
    res = client.get(f"/api/skills/{sid}/files", headers=auth(sm))
    assert res.status_code == 200
    assert {f["name"] for f in res.get_json()} == {"skill.md", "main.py", "lib/helper.py"}
