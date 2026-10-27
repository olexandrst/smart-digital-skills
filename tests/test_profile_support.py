"""Хвилі 3 і 4 фідбеку тестування: позначка «Експерт», рівень лише від
адміністратора, посилання на AI SD агента й «Зворотний зв'язок»."""
import io
import logging
import zipfile

from app.extensions import db
from app.models import ContactMessage, Notification, User
from tests.conftest import sm_section_id


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _uid(username):
    return User.query.filter_by(username=username).one().id


def _set_expert(client, username, value=True):
    res = client.patch(f"/api/users/{_uid(username)}", json={"is_expert": value},
                       headers=_admin(client))
    assert res.status_code == 200, res.get_json()


def _material(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published", "section_id": sm_section_id()}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _approve(client, rid, kind="catalog/resources"):
    res = client.post(f"/api/{kind}/{rid}/status", json={"status": "published"},
                      headers=_login(client, "sm"))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _levels(client, app, headers):
    from app.core.schema import seed_catalog_terms
    with app.app_context():
        seed_catalog_terms()
    rows = client.get("/api/catalog/terms?kind=maturity", headers=headers).get_json()
    return {r["name"]: r["id"] for r in rows}


# ----------------------------- «Експерт» -----------------------------

def test_expert_label_taken_at_publication(client):
    """Позначка — ознака автора на момент публікації: зняту згодом галочку
    вже опубліковане не відчуває, а нове — так."""
    _set_expert(client, "u1")
    u1 = _login(client, "u1")
    first = _material(client, u1, name="Від експерта")
    assert first["status"] == "review" and first["author_is_expert"] is False
    assert _approve(client, first["id"])["author_is_expert"] is True

    _set_expert(client, "u1", False)
    again = client.get(f"/api/catalog/resources/{first['id']}", headers=u1).get_json()
    assert again["author_is_expert"] is True
    second = _material(client, u1, name="Після зняття галочки")
    assert _approve(client, second["id"])["author_is_expert"] is False


def test_expert_label_survives_republication(client):
    """Повторна публікація з архіву позначку не перераховує."""
    _set_expert(client, "u1")
    rid = _material(client, _login(client, "u1"))["id"]
    _approve(client, rid)
    _set_expert(client, "u1", False)
    sm = _login(client, "sm")
    assert client.post(f"/api/catalog/resources/{rid}/status", json={"status": "archived"},
                       headers=sm).status_code == 200
    assert _approve(client, rid)["author_is_expert"] is True


def test_expert_label_on_skills(client):
    _set_expert(client, "u1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.md", "---\nname: Протокол\ndescription: Опис\nversion: 1.0.0\n---\n# Як\n")
    res = client.post("/api/skills/upload", headers=_login(client, "u1"),
                      data={"file": (io.BytesIO(buf.getvalue()), "s.zip"), "status": "published",
                            "section_id": str(sm_section_id())},
                      content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    sid = res.get_json()["id"]
    assert _approve(client, sid, kind="skills")["author_is_expert"] is True


def test_moderator_publishing_own_material_gets_label(client):
    """Менеджер, що публікує своє одразу, теж отримує позначку, якщо він експерт."""
    _set_expert(client, "sm")
    item = _material(client, _login(client, "sm"))
    assert item["status"] == "published" and item["author_is_expert"] is True


# ------------------------- Рівень від адміністратора -------------------------

def test_user_cannot_change_own_level(client, app):
    h = _login(client, "u1")
    lv = _levels(client, app, _admin(client))
    res = client.patch("/api/catalog/profile", json={"maturity_level_id": lv["User"]}, headers=h)
    assert res.status_code == 403 and res.get_json()["error"] == "level_locked"
    assert client.get("/api/catalog/maturity", headers=h).get_json()["current"] is None
    # Підрозділ і знайомство людина й далі змінює сама.
    assert client.patch("/api/catalog/profile", json={"department": "Закупівлі"},
                        headers=h).status_code == 200


def test_admin_sets_level_and_date(client, app):
    admin = _admin(client)
    lv = _levels(client, app, admin)
    uid = _uid("u1")
    res = client.patch(f"/api/users/{uid}", headers=admin,
                       json={"maturity_level_id": lv["Integrator"],
                             "maturity_assessed_at": "2026-09-15"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["maturity_assessed_at"].startswith("2026-09-15")
    data = client.get("/api/catalog/maturity", headers=_login(client, "u1")).get_json()
    assert data["current"]["name"] == "Integrator"
    assert data["assessed_at"].startswith("2026-09-15")

    # Лише дата — рівень той самий.
    res = client.patch(f"/api/users/{uid}", headers=admin,
                       json={"maturity_assessed_at": "2026-10-01"})
    assert res.get_json()["maturity_assessed_at"].startswith("2026-10-01")
    # Без рівня немає й дати.
    res = client.patch(f"/api/users/{uid}", headers=admin, json={"maturity_level_id": None})
    assert res.get_json()["maturity_level_id"] is None
    assert res.get_json()["maturity_assessed_at"] is None


def test_admin_level_without_date_uses_today(client, app):
    admin = _admin(client)
    lv = _levels(client, app, admin)
    res = client.patch(f"/api/users/{_uid('u1')}", headers=admin,
                       json={"maturity_level_id": lv["Aware"]})
    assert res.get_json()["maturity_assessed_at"]


def test_level_validation(client, app):
    admin = _admin(client)
    _levels(client, app, admin)
    uid = _uid("u1")
    assert client.patch(f"/api/users/{uid}", headers=admin,
                        json={"maturity_level_id": 99999}).status_code == 400
    lv = _levels(client, app, admin)
    assert client.patch(f"/api/users/{uid}", headers=admin,
                        json={"maturity_level_id": lv["User"],
                              "maturity_assessed_at": "15.09.2026"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", headers=admin,
                        json={"maturity_level_id": lv["User"],
                              "maturity_assessed_at": "2999-01-01"}).status_code == 400


def test_only_admin_sets_levels(client, app):
    lv = _levels(client, app, _admin(client))
    for who in ("sm", "u1"):
        res = client.patch(f"/api/users/{_uid('u2')}", headers=_login(client, who),
                           json={"maturity_level_id": lv["User"]})
        assert res.status_code == 403


# ----------------------------- AI SD агент -----------------------------

def test_support_agent_link_default_and_change(client):
    user = _login(client, "u1")
    url = client.get("/api/catalog/settings", headers=user).get_json()["support_agent_url"]
    assert url.startswith("https://teams.microsoft.com/")

    admin = _admin(client)
    assert client.post("/api/catalog/settings", json={"support_agent_url": "teams"},
                       headers=admin).status_code == 400
    assert client.post("/api/catalog/settings",
                       json={"support_agent_url": "https://example.org/agent"},
                       headers=user).status_code == 403
    assert client.post("/api/catalog/settings",
                       json={"support_agent_url": " https://example.org/agent "},
                       headers=admin).status_code == 200
    assert client.get("/api/catalog/settings", headers=user).get_json()[
        "support_agent_url"] == "https://example.org/agent"
    # Порожнє значення ховає посилання.
    client.post("/api/catalog/settings", json={"support_agent_url": ""}, headers=admin)
    assert client.get("/api/catalog/settings", headers=user).get_json()["support_agent_url"] == ""


# --------------------------- Зворотний зв'язок ---------------------------

def test_contact_message_saved_with_author_and_page(client, caplog):
    u1 = _login(client, "u1")
    user = User.query.filter_by(username="u1").one()
    user.full_name, user.email = "Олена Коваль", "olena@example.org"
    admin = User.query.filter_by(username="admin").one()
    admin.email = "admin@example.org"
    db.session.commit()

    with caplog.at_level(logging.INFO, logger="app.services.mail_service"):
        res = client.post("/api/contact", headers=u1,
                          json={"message": "  Як отримати доступ до розділу?  ",
                                "page": "Мій розвиток"})
    assert res.status_code == 201, res.get_json()
    msg = res.get_json()
    assert msg["author_name"] == "Олена Коваль"
    assert msg["author_email"] == "olena@example.org"
    assert msg["message"] == "Як отримати доступ до розділу?"
    assert msg["page"] == "Мій розвиток" and msg["status"] == "new"
    # Лист поки заглушка: записується в журнал, адресат — адміністратор.
    assert "admin@example.org" in caplog.text and "Олена Коваль" in caplog.text

    note = Notification.query.filter_by(kind="contact_message").one()
    assert note.user_id == admin.id and "Олена Коваль" in note.body
    # Менеджер розділу й інші користувачі сповіщення не отримують.
    assert Notification.query.filter_by(kind="contact_message").count() == 1


def test_contact_message_validation(client):
    u1 = _login(client, "u1")
    assert client.post("/api/contact", json={"message": "   "}, headers=u1).status_code == 400
    assert client.post("/api/contact", json={"message": "x" * 5001},
                       headers=u1).status_code == 400
    assert client.post("/api/contact", json={"message": "Питання"}).status_code == 401


def test_contact_rate_limit(client):
    u1 = _login(client, "u1")
    for i in range(10):
        assert client.post("/api/contact", json={"message": f"Питання {i}"},
                           headers=u1).status_code == 201
    assert client.post("/api/contact", json={"message": "Ще одне"},
                       headers=u1).status_code == 429


def test_admin_lists_and_handles_messages(client):
    u1 = _login(client, "u1")
    mid = client.post("/api/contact", json={"message": "Перше"}, headers=u1).get_json()["id"]
    client.post("/api/contact", json={"message": "Друге"}, headers=u1)
    admin = _admin(client)

    data = client.get("/api/contact", headers=admin).get_json()
    assert [m["message"] for m in data["items"]] == ["Друге", "Перше"]
    assert data["counts"] == {"new": 2, "done": 0}

    res = client.patch(f"/api/contact/{mid}", json={"status": "done"}, headers=admin)
    assert res.status_code == 200
    assert res.get_json()["handled_at"] and res.get_json()["handled_by_name"] == "admin"
    new = client.get("/api/contact?status=new", headers=admin).get_json()
    assert [m["message"] for m in new["items"]] == ["Друге"]
    assert new["counts"] == {"new": 1, "done": 1}

    back = client.patch(f"/api/contact/{mid}", json={"status": "new"}, headers=admin).get_json()
    assert back["handled_at"] is None and back["handled_by_name"] is None
    assert client.patch(f"/api/contact/{mid}", json={"status": "x"},
                        headers=admin).status_code == 400


def test_only_admin_sees_messages(client):
    client.post("/api/contact", json={"message": "Питання"}, headers=_login(client, "u1"))
    for who in ("sm", "u1"):
        h = _login(client, who)
        assert client.get("/api/contact", headers=h).status_code == 403
        assert client.patch("/api/contact/1", json={"status": "done"},
                            headers=h).status_code == 403


def test_message_survives_author_deletion(client):
    client.post("/api/contact", json={"message": "Питання"}, headers=_login(client, "u2"))
    admin = _admin(client)
    assert client.delete(f"/api/users/{_uid('u2')}", headers=admin).status_code == 200
    msg = ContactMessage.query.one()
    assert msg.user_id is None and msg.author_name == "u2"
