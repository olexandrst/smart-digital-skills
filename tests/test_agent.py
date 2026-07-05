"""Тести стандартних (агентних) скілів: формат, пісочниця, агентний цикл."""
import io
import zipfile
from app.services import package_service as ps
from app.models import Skill, User
from tests.conftest import login, auth


AGENT_MD = """---
name: Demo Agent Skill
description: Демонстраційний агентний скіл (без entrypoint).
---
# Demo
Ти агент. За потреби виконуй Python-код у пісочниці.
"""


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def upload(client, token, zip_bytes, filename="skill.skill"):
    return client.post("/api/skills/upload", headers=auth(token),
                       data={"file": (io.BytesIO(zip_bytes), filename)},
                       content_type="multipart/form-data")


def test_agent_skill_format(client):
    sm = login(client, "sm", "pass")
    res = upload(client, sm, make_zip({"SKILL.md": AGENT_MD,
                                       "references/notes.md": "# нотатки"}))
    assert res.status_code == 201
    data = res.get_json()
    assert data["skill_kind"] == "package"
    assert data["entrypoint"] is None  # стандартний агентний скіл
    assert ps.is_agent_skill(Skill.query.get(data["id"]))


def test_extract_run_python():
    assert ps.extract_run_python("текст ```run-python\nprint(1)\n``` далі") == "print(1)"
    assert ps.extract_run_python("```python\nx=1\n```") == "x=1"
    assert ps.extract_run_python("просто відповідь без коду") is None


def test_sandbox_exec_captures_file_utf8(client, app):
    """Пісочниця виконує код, кирилиця у виводі/файлі не падає, файл захоплюється."""
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": AGENT_MD})).get_json()["id"]
    skill = Skill.query.get(sid)
    user = User.query.filter_by(username="u1").first()

    sandbox = ps.prepare_sandbox(skill)
    try:
        ex = ps.exec_in_sandbox(
            sandbox,
            "open('звіт.txt','w',encoding='utf-8').write('Привіт ✓')\nprint('готово ✓')")
        assert ex["returncode"] == 0
        assert "готово" in ex["stdout"]
        files = ps.finalize_sandbox(sandbox, user, skill.id)
    finally:
        ps.cleanup_sandbox(sandbox)
    assert any("звіт" in f["filename"] for f in files)


def test_sandbox_redirects_tmp(client, app):
    """Запис у абсолютний /tmp перенаправляється у пісочницю й захоплюється."""
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": AGENT_MD})).get_json()["id"]
    skill = Skill.query.get(sid)
    user = User.query.filter_by(username="u1").first()
    sandbox = ps.prepare_sandbox(skill)
    try:
        ex = ps.exec_in_sandbox(
            sandbox, "open('/tmp/result.txt','w').write('data')\nprint('saved')")
        assert ex["returncode"] == 0
        files = ps.finalize_sandbox(sandbox, user, skill.id)
    finally:
        ps.cleanup_sandbox(sandbox)
    assert any(f["filename"] == "result.txt" for f in files)


def test_agent_skill_in_chat_mock(client):
    """Агентний скіл у чаті: мок-модель повертає текст (без блоку коду) → фінал."""
    sm = login(client, "sm", "pass")
    sid = upload(client, sm, make_zip({"SKILL.md": AGENT_MD})).get_json()["id"]
    client.post(f"/api/skills/{sid}/status", headers=auth(sm), json={"status": "published"})

    u1 = login(client, "u1", "pass")
    client.post(f"/api/skills/{sid}/activate", headers=auth(u1))
    from app.models import Model
    mid = Model.query.filter_by(name="gpt-4o").first().id
    chat = client.post("/api/chat/sessions", headers=auth(u1),
                       json={"model_id": mid}).get_json()
    res = client.post(f"/api/chat/sessions/{chat['id']}/messages", headers=auth(u1),
                      json={"content": "оціни проект", "skill_id": sid})
    assert res.status_code == 200
    data = res.get_json()
    assert data["content"]  # модель щось відповіла
    assert "usage" in data
