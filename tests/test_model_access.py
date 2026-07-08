"""Тести доступу до моделей через групи (матриця «групи × моделі»)
та автоматичного вибору моделі в чаті."""
from app.models import Model, User
from tests.conftest import login, auth


def _new_model(client, admin, name, deployment="dep"):
    return client.post("/api/models", headers=auth(admin), json={
        "name": name, "provider": "azure", "deployment_name": deployment}).get_json()["id"]


def _group_with(client, admin, name, usernames):
    gid = client.post("/api/groups", headers=auth(admin), json={"name": name}).get_json()["id"]
    for u in usernames:
        uid = User.query.filter_by(username=u).first().id
        client.post(f"/api/groups/{gid}/members", headers=auth(admin), json={"user_id": uid})
    return gid


def _grant(client, admin, gid, mid, granted=True):
    return client.post("/api/models/access", headers=auth(admin),
                       json={"group_id": gid, "model_id": mid, "granted": granted})


# ---------- Системна модель — усім ----------

def test_system_model_available_to_all(client):
    u1 = login(client, "u1", "pass")
    res = client.get("/api/models/mine", headers=auth(u1)).get_json()
    sys_id = Model.query.filter_by(name="gpt-4o").first().id
    ids = [m["id"] for m in res["models"]]
    assert sys_id in ids
    assert res["default_id"] == sys_id  # системна має пріоритет


# ---------- Доступ через групу ----------

def test_group_grant_gives_and_revokes_access(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    mid = _new_model(client, admin, "Beta")
    gid = _group_with(client, admin, "G", ["u1"])

    # До призначення — недоступна.
    ids = [m["id"] for m in client.get("/api/models/mine", headers=auth(u1)).get_json()["models"]]
    assert mid not in ids

    # Активація чекбокса — доступ зʼявляється.
    assert _grant(client, admin, gid, mid, True).status_code == 200
    ids = [m["id"] for m in client.get("/api/models/mine", headers=auth(u1)).get_json()["models"]]
    assert mid in ids

    # Деактивація чекбокса — доступ зникає.
    _grant(client, admin, gid, mid, False)
    ids = [m["id"] for m in client.get("/api/models/mine", headers=auth(u1)).get_json()["models"]]
    assert mid not in ids


def test_model_available_to_several_groups(client):
    admin = login(client, "admin", "Admin123!")
    u1, u2 = login(client, "u1", "pass"), login(client, "u2", "pass")
    mid = _new_model(client, admin, "Shared")
    g1 = _group_with(client, admin, "G1", ["u1"])
    g2 = _group_with(client, admin, "G2", ["u2"])
    _grant(client, admin, g1, mid, True)
    _grant(client, admin, g2, mid, True)
    for tok in (u1, u2):
        ids = [m["id"] for m in client.get("/api/models/mine", headers=auth(tok)).get_json()["models"]]
        assert mid in ids


# ---------- Правила вибору моделі за замовчуванням ----------

def test_default_prefers_system(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    mid = _new_model(client, admin, "Alpha")  # alphanumeric раніше за gpt-4o
    gid = _group_with(client, admin, "G", ["u1"])
    _grant(client, admin, gid, mid, True)
    res = client.get("/api/models/mine", headers=auth(u1)).get_json()
    sys_id = Model.query.filter_by(name="gpt-4o").first().id
    assert res["default_id"] == sys_id  # системна попри «Alpha»


def test_default_alphanumeric_when_no_system(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    # Прибираємо системну з доступу u1: деактивуємо seed-модель.
    sys_id = Model.query.filter_by(name="gpt-4o").first().id
    client.post(f"/api/models/{sys_id}/activate", headers=auth(admin), json={"is_active": False})
    m_b = _new_model(client, admin, "Bravo")
    m_9 = _new_model(client, admin, "9-Model")  # цифри 1-9 раніше за A-z
    gid = _group_with(client, admin, "G", ["u1"])
    _grant(client, admin, gid, m_b, True)
    _grant(client, admin, gid, m_9, True)
    res = client.get("/api/models/mine", headers=auth(u1)).get_json()
    assert res["default_id"] == m_9
    assert [m["id"] for m in res["models"]] == [m_9, m_b]


def test_single_model_is_default(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    res = client.get("/api/models/mine", headers=auth(u1)).get_json()
    assert len(res["models"]) == 1  # лише системна
    assert res["default_id"] == res["models"][0]["id"]


# ---------- Права на матрицю ----------

def test_access_matrix_admin_only(client):
    admin = login(client, "admin", "Admin123!")
    _new_model(client, admin, "Gamma")
    _group_with(client, admin, "Gr", ["u1"])
    mx = client.get("/api/models/access-matrix", headers=auth(admin)).get_json()
    assert any(g["name"] == "Gr" for g in mx["groups"])
    assert any(m["name"] == "Gamma" for m in mx["models"])
    assert "grants" in mx

    u1 = login(client, "u1", "pass")
    assert client.get("/api/models/access-matrix", headers=auth(u1)).status_code == 403
    assert client.post("/api/models/access", headers=auth(u1),
                       json={"group_id": 1, "model_id": 1, "granted": True}).status_code == 403


def test_cannot_grant_system_model(client):
    admin = login(client, "admin", "Admin123!")
    sys_id = Model.query.filter_by(name="gpt-4o").first().id
    gid = _group_with(client, admin, "G", [])
    assert _grant(client, admin, gid, sys_id, True).status_code == 400


# ---------- Чат: автоматичний вибір і зміна моделі ----------

def test_chat_create_uses_default_model(client):
    u1 = login(client, "u1", "pass")
    res = client.post("/api/chat/sessions", headers=auth(u1), json={})
    assert res.status_code == 201
    sys_id = Model.query.filter_by(name="gpt-4o").first().id
    assert res.get_json()["model_id"] == sys_id


def test_chat_create_rejects_inaccessible_model(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    mid = _new_model(client, admin, "Secret")  # u1 без груп → недоступна
    res = client.post("/api/chat/sessions", headers=auth(u1), json={"model_id": mid})
    assert res.status_code == 403


def test_patch_session_model(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    mid = _new_model(client, admin, "Delta")
    gid = _group_with(client, admin, "G", ["u1"])
    _grant(client, admin, gid, mid, True)

    sid = client.post("/api/chat/sessions", headers=auth(u1), json={}).get_json()["id"]
    res = client.patch(f"/api/chat/sessions/{sid}", headers=auth(u1), json={"model_id": mid})
    assert res.status_code == 200 and res.get_json()["model_id"] == mid

    # Зняли доступ → перемкнути на цю модель більше не можна.
    _grant(client, admin, gid, mid, False)
    assert client.patch(f"/api/chat/sessions/{sid}", headers=auth(u1),
                        json={"model_id": mid}).status_code == 403
