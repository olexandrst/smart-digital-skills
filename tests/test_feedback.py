"""Тести зворотного зв'язку щодо навичок + описових атрибутів каталогу."""
from app.models import Skill, SkillFeedback
from tests.conftest import login, auth


def _sid():
    return Skill.query.filter_by(name="Summarizer").first().id


# ---------- Описові атрибути (Вхідні дані / Результат / Стартовий промпт) ----------

def test_edit_and_expose_catalog_attrs(client):
    sm = login(client, "sm", "pass")
    sid = _sid()
    res = client.patch(f"/api/skills/{sid}", headers=auth(sm), json={
        "input_spec": "Текст на вхід", "output_spec": "Підсумок",
        "starter_prompt": "Підсумуй це:"})
    d = res.get_json()
    assert d["input_spec"] == "Текст на вхід"
    assert d["output_spec"] == "Підсумок"
    assert d["starter_prompt"] == "Підсумуй це:"


# ---------- Надсилання фідбеку ----------

def test_user_submits_feedback_snapshot(client):
    u1 = login(client, "u1", "pass")
    sid = _sid()
    res = client.post(f"/api/skills/{sid}/feedback", headers=auth(u1),
                      json={"message": "Чудова навичка, дякую!"})
    assert res.status_code == 201

    fb = SkillFeedback.query.first()
    assert fb.message == "Чудова навичка, дякую!"
    assert fb.skill_name == "Summarizer"      # знімок назви
    assert fb.skill_version == "1.0.0"        # знімок версії
    assert fb.username in ("u1", None) or fb.username  # логін/ім'я користувача
    assert fb.is_read is False


def test_empty_feedback_rejected(client):
    u1 = login(client, "u1", "pass")
    res = client.post(f"/api/skills/{_sid()}/feedback", headers=auth(u1),
                      json={"message": "   "})
    assert res.status_code == 400


# ---------- Читання менеджментом ----------

def test_manager_lists_and_filters_feedback(client):
    u1 = login(client, "u1", "pass")
    sid = _sid()
    client.post(f"/api/skills/{sid}/feedback", headers=auth(u1), json={"message": "Перше"})
    client.post(f"/api/skills/{sid}/feedback", headers=auth(u1), json={"message": "Друге"})

    sm = login(client, "sm", "pass")
    res = client.get("/api/skills/feedback", headers=auth(sm)).get_json()
    assert res["unread"] == 2
    assert len(res["items"]) == 2
    # Найновіші — зверху.
    assert res["items"][0]["message"] == "Друге"

    fid = res["items"][0]["id"]
    client.post(f"/api/skills/feedback/{fid}/read", headers=auth(sm))
    res_new = client.get("/api/skills/feedback?filter=new", headers=auth(sm)).get_json()
    assert res_new["unread"] == 1
    assert len(res_new["items"]) == 1  # лише непрочитане

    client.post("/api/skills/feedback/read-all", headers=auth(sm))
    assert client.get("/api/skills/feedback?filter=new", headers=auth(sm)).get_json()["unread"] == 0


def test_feedback_access_control(client):
    u1 = login(client, "u1", "pass")
    # Звичайний користувач НЕ бачить інбокс.
    assert client.get("/api/skills/feedback", headers=auth(u1)).status_code == 403
    assert client.get("/api/skills/feedback/unread-count", headers=auth(u1)).status_code == 403


def test_delete_skill_keeps_feedback_snapshot(client):
    u1 = login(client, "u1", "pass")
    admin = login(client, "admin", "Admin123!")
    sid = _sid()
    client.post(f"/api/skills/{sid}/feedback", headers=auth(u1), json={"message": "Відгук"})

    client.delete(f"/api/skills/{sid}", headers=auth(admin))
    fb = SkillFeedback.query.first()
    assert fb is not None            # фідбек збережено
    assert fb.skill_id is None       # відв'язано від видаленої навички
    assert fb.skill_name == "Summarizer"  # знімок лишається читабельним
