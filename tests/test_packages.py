"""Тести скілів-пакетів: розбір, завантаження, реальне виконання коду, безпека."""
import io
import zipfile
import pytest
from app.services import package_service
from app.core.errors import ApiError
from tests.conftest import login, auth

SKILL_MD = """---
name: Echo Upper
description: Повертає текст у верхньому регістрі (виконується як код).
version: 1.0.0
runtime: python
entrypoint: main.py
inputs:
  - name: text
    label: Текст
    required: true
---
# Echo Upper
Демо-пакет.
"""

MAIN_PY = (
    "import sys, json\n"
    "data = json.load(sys.stdin)\n"
    "text = data.get('inputs', {}).get('text', '')\n"
    "print(json.dumps({'output': text.upper(), "
    "'usage': {'prompt_tokens': len(text), 'completion_tokens': len(text)}}, "
    "ensure_ascii=False))\n"
)


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

def test_parse_package_reads_frontmatter(app):
    meta = package_service.parse_package(make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY}))
    assert meta["name"] == "Echo Upper"
    assert meta["entrypoint"] == "main.py"
    assert meta["runtime"] == "python"
    assert [i["name"] for i in meta["inputs"]] == ["text"]


def test_parse_package_missing_skill_md(app):
    with pytest.raises(ApiError):
        package_service.parse_package(make_zip({"main.py": MAIN_PY}))


def test_parse_package_missing_entrypoint(app):
    with pytest.raises(ApiError):
        package_service.parse_package(make_zip({"skill.md": SKILL_MD}))


def test_zip_slip_rejected(app):
    bad = make_zip({"skill.md": SKILL_MD, "../evil.py": "x=1", "main.py": MAIN_PY})
    with pytest.raises(ApiError):
        package_service.parse_package(bad)


# ---------- Завантаження та виконання через API ----------

def test_upload_requires_role(client):
    token = login(client, "u1", "pass")  # звичайний користувач
    res = upload(client, token, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY}))
    assert res.status_code == 403


def test_upload_and_execute_package(client):
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY}))
    assert res.status_code == 201
    skill = res.get_json()
    assert skill["skill_kind"] == "package"
    sid = skill["id"]

    # Публікуємо та активуємо для u1, потім запускаємо (реальне виконання коду).
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})
    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    res = client.post(f"/api/skills/{sid}/run", headers=auth(u1),
                      json={"inputs": {"text": "hello світ"}})
    assert res.status_code == 200
    data = res.get_json()
    assert data["content"] == "HELLO СВІТ"
    assert data["usage"]["total_tokens"] > 0


def test_list_package_files(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY,
                                       "lib/helper.py": "x=1"})).get_json()["id"]
    res = client.get(f"/api/skills/{sid}/files", headers=auth(sm))
    assert res.status_code == 200
    names = {f["name"] for f in res.get_json()}
    assert "main.py" in names and "lib/helper.py" in names


def test_exec_disabled(client, app):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})
    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))

    app.config["SKILL_EXEC_ENABLED"] = False
    try:
        res = client.post(f"/api/skills/{sid}/run", headers=auth(u1),
                          json={"inputs": {"text": "hi"}})
        assert res.status_code == 403
    finally:
        app.config["SKILL_EXEC_ENABLED"] = True


def test_package_skill_in_chat(client):
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"skill.md": SKILL_MD, "main.py": MAIN_PY})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})

    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    from app.models import Model
    mid = Model.query.filter_by(name="gpt-4o").first().id
    chat = client.post("/api/chat/sessions", headers=auth(u1), json={"model_id": mid}).get_json()
    res = client.post(f"/api/chat/sessions/{chat['id']}/messages", headers=auth(u1),
                      json={"content": "привіт", "skill_id": sid})
    assert res.status_code == 200
    # У чаті застосовано код-пакет → відповідь = вивід коду (верхній регістр).
    assert res.get_json()["content"] == "ПРИВІТ"


# ---------- Гнучке визначення entrypoint ----------

SKILL_MD_NO_EP = """---
name: No Entry
description: Без явного entrypoint.
version: 1.0.0
runtime: python
inputs:
  - name: text
    required: true
---
# No Entry
"""


def test_entrypoint_in_top_level_folder(client):
    """Архів з однією верхньою текою (pkg/skill.md, pkg/main.py) має працювати."""
    sm = login(client, "sm", "pass")
    z = make_zip({"pkg/skill.md": SKILL_MD, "pkg/main.py": MAIN_PY})
    res = upload(client, sm, z, filename="pkg.skill")
    assert res.status_code == 201
    sid = res.get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})
    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    res = client.post(f"/api/skills/{sid}/run", headers=auth(u1),
                      json={"inputs": {"text": "hi"}})
    assert res.status_code == 200
    assert res.get_json()["content"] == "HI"


def test_entrypoint_auto_detected_single_py(client):
    """Без entrypoint у skill.md і з єдиним .py — він визначається автоматично."""
    sm = login(client, "sm", "pass")
    z = make_zip({"skill.md": SKILL_MD_NO_EP, "estimate.py": MAIN_PY})
    res = upload(client, sm, z)
    assert res.status_code == 201
    assert res.get_json()["entrypoint"] == "estimate.py"


def test_entrypoint_unresolvable_lists_py_files(app):
    """Кілька .py без entrypoint і без main.py → зрозуміла помилка."""
    import pytest
    with pytest.raises(ApiError) as exc:
        package_service.parse_package(make_zip({
            "skill.md": SKILL_MD_NO_EP, "a.py": "x=1", "b.py": "y=2"}))
    assert "entrypoint" in str(exc.value).lower()
