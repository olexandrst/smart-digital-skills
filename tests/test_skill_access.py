"""Тести доступу до навичок за групами (матриця) та індивідуального доступу."""
from app.models import Skill, User
from tests.conftest import login, auth


def _skill_id():
    return Skill.query.filter_by(name="Summarizer").first().id


def _group_with(client, admin, name, usernames):
    gid = client.post("/api/groups", headers=auth(admin), json={"name": name}).get_json()["id"]
    for u in usernames:
        uid = User.query.filter_by(username=u).first().id
        client.post(f"/api/groups/{gid}/members", headers=auth(admin), json={"user_id": uid})
    return gid


def _grant(client, admin, gid, sid, granted=True):
    return client.post("/api/skills/access", headers=auth(admin),
                       json={"group_id": gid, "skill_id": sid, "granted": granted})


def _mine_ids(client, token):
    return [s["id"] for s in client.get("/api/skills/mine", headers=auth(token)).get_json()]


# ---------- Матриця: видача та відкликання ----------

def test_group_grant_gives_and_revokes_skill(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    sid = _skill_id()
    gid = _group_with(client, admin, "G", ["u1"])

    assert sid not in _mine_ids(client, u1)
    assert _grant(client, admin, gid, sid, True).status_code == 200
    assert sid in _mine_ids(client, u1)
    _grant(client, admin, gid, sid, False)
    assert sid not in _mine_ids(client, u1)


def test_group_granted_skill_runnable_in_chat(client):
    """Доступ за групою (без самоактивації) дозволяє запускати навичку в чаті."""
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    sid = _skill_id()
    gid = _group_with(client, admin, "G", ["u1"])
    _grant(client, admin, gid, sid, True)

    chat = client.post("/api/chat/sessions", headers=auth(u1), json={}).get_json()
    res = client.post(f"/api/chat/sessions/{chat['id']}/messages", headers=auth(u1),
                      json={"content": "стисни це", "skill_id": sid})
    assert res.status_code == 200


def test_self_remove_keeps_group_access(client):
    """Самостійне вилучення не прибирає доступ, наданий групою (правило 8)."""
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    sid = _skill_id()
    gid = _group_with(client, admin, "G", ["u1"])
    _grant(client, admin, gid, sid, True)

    # Користувач "видаляє у себе" навичку — але групою вона все одно надана.
    assert client.post(f"/api/skills/{sid}/deactivate",
                       headers=auth(u1)).status_code == 200
    assert sid in _mine_ids(client, u1)


def test_access_matrix_admin_only(client):
    admin = login(client, "admin", "Admin123!")
    _group_with(client, admin, "Gr", [])
    mx = client.get("/api/skills/access-matrix", headers=auth(admin)).get_json()
    assert any(s["name"] == "Summarizer" for s in mx["skills"])
    assert any(g["name"] == "Gr" for g in mx["groups"])
    assert mx["individual_access"] is True

    u1 = login(client, "u1", "pass")
    assert client.get("/api/skills/access-matrix", headers=auth(u1)).status_code == 403
    assert client.post("/api/skills/access", headers=auth(u1),
                       json={"group_id": 1, "skill_id": 1, "granted": True}).status_code == 403


# ---------- Індивідуальний доступ (перемикач) ----------

def test_individual_access_default_on(client):
    u1 = login(client, "u1", "pass")
    st = client.get("/api/skills/settings", headers=auth(u1)).get_json()
    assert st["individual_access"] is True
    # Самоактивація працює.
    assert client.post(f"/api/skills/{_skill_id()}/activate",
                       headers=auth(u1)).status_code == 200


def test_individual_access_off_blocks_self_service(client):
    admin = login(client, "admin", "Admin123!")
    u1 = login(client, "u1", "pass")
    res = client.post("/api/skills/access-settings", headers=auth(admin),
                      json={"individual_access": False})
    assert res.get_json()["individual_access"] is False

    sid = _skill_id()
    assert client.post(f"/api/skills/{sid}/activate", headers=auth(u1)).status_code == 403
    assert client.post(f"/api/skills/{sid}/deactivate", headers=auth(u1)).status_code == 403
    # Налаштування видно будь-якому користувачу (для приховання Каталогу).
    st = client.get("/api/skills/settings", headers=auth(u1)).get_json()
    assert st["individual_access"] is False
    # Admin не блокується.
    assert client.post(f"/api/skills/{sid}/activate", headers=auth(admin)).status_code == 200
    # Перемикач можна повернути.
    res = client.post("/api/skills/access-settings", headers=auth(admin),
                      json={"individual_access": True})
    assert res.get_json()["individual_access"] is True
    assert client.post(f"/api/skills/{sid}/activate", headers=auth(u1)).status_code == 200


def test_access_settings_admin_only(client):
    u1 = login(client, "u1", "pass")
    assert client.post("/api/skills/access-settings", headers=auth(u1),
                       json={"individual_access": False}).status_code == 403
