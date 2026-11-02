"""Доопрацювання модерації й ролей (DEV-0 … DEV-07).

- пакет навички без SKILL.md відхиляється зрозумілою помилкою ще до збереження;
- у розділу модератор і заступник з однаковими правами, журнал фіксує, хто
  з них ухвалив рішення і коли;
- правки модератора до поданого матеріалу погоджуються з автором перед
  публікацією;
- модератор архівує й повертає з архіву матеріали й навички свого розділу;
- ролі й «Експерт» беруться з груп корпоративного входу;
- рівень не обмежує доступ, а лише впорядковує рекомендації.
"""
import io
import zipfile

import pytest

from app.extensions import db
from app.models import (CatalogResource, CatalogResourceMaturity, CatalogSection,
                        CatalogTerm, Notification, ReviewLog, Role, User, UserRole)
from tests.conftest import sm_section_id


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


SKILL_MD = "---\nname: Протокол наради\ndescription: Опис\nversion: 1.2.0\n---\n# Як\n"


def _post_zip(client, headers, url, files, filename="s.zip", **form):
    data = {"file": (io.BytesIO(_zip(files)), filename), **form}
    return client.post(url, headers=headers, data=data, content_type="multipart/form-data")


def _material(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт для звітів", "description": "Опис",
               "body": "Текст промпту", "status": "published", "section_id": sm_section_id()}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _status(client, headers, rid, status, kind="catalog/resources", **extra):
    return client.post(f"/api/{kind}/{rid}/status", headers=headers,
                       json={"status": status, **extra})


def _make_deputy(client, username="u2"):
    uid = User.query.filter_by(username=username).one().id
    res = client.patch(f"/api/users/{uid}", headers=_admin(client), json={
        "roles": ["section_manager"], "managed_section_ids": [sm_section_id()],
        "deputy_section_ids": [sm_section_id()]})
    assert res.status_code == 200, res.get_json()
    return res.get_json()


# ------------------------------ DEV-0: SKILL.md ------------------------------

def test_package_without_skill_md_is_rejected_with_clear_message(client):
    u1 = _login(client, "u1")
    for url in ("/api/skills/inspect", "/api/skills/upload"):
        res = _post_zip(client, u1, url, {"README.md": "# Навичка"})
        assert res.status_code == 400
        body = res.get_json()
        assert body["error"] == "missing_skill_md"
        assert "SKILL.md" in body["message"]


def test_inspect_reads_package_without_saving(client):
    u1 = _login(client, "u1")
    before = client.get("/api/skills?mine=1", headers=u1).get_json()
    res = _post_zip(client, u1, "/api/skills/inspect", {"pack/SKILL.md": SKILL_MD})
    assert res.status_code == 200
    assert res.get_json()["name"] == "Протокол наради"
    assert res.get_json()["version"] == "1.2.0"
    assert client.get("/api/skills?mine=1", headers=u1).get_json() == before


def test_skill_md_name_is_case_insensitive_and_macos_junk_ignored(client):
    u1 = _login(client, "u1")
    assert _post_zip(client, u1, "/api/skills/inspect",
                     {"Skill.md": SKILL_MD}).status_code == 200
    res = _post_zip(client, u1, "/api/skills/inspect",
                    {"__MACOSX/._SKILL.md": "x", "notes.txt": "x"})
    assert res.get_json()["error"] == "missing_skill_md"


def test_inspect_rejects_wrong_extension(client):
    res = _post_zip(client, _login(client, "u1"), "/api/skills/inspect",
                    {"SKILL.md": SKILL_MD}, filename="s.rar")
    assert res.status_code == 400


# ------------------------- DEV-01/02: модератор і заступник -------------------------

def test_section_has_moderator_and_deputy(client):
    _make_deputy(client)
    sections = client.get("/api/catalog/sections", headers=_login(client, "u1")).get_json()
    sec = next(s for s in sections if s["id"] == sm_section_id())
    assert sec["moderator"]["name"] == "sm"
    assert sec["deputy"]["name"] == "u2"


def test_second_moderator_or_deputy_for_section_is_refused(client):
    _make_deputy(client)
    admin = _admin(client)
    uid = User.query.filter_by(username="u1").one().id
    for deputy in ([], [sm_section_id()]):
        res = client.patch(f"/api/users/{uid}", headers=admin, json={
            "roles": ["section_manager"], "managed_section_ids": [sm_section_id()],
            "deputy_section_ids": deputy})
        assert res.status_code == 409
        assert res.get_json()["error"] == "section_slot_taken"


def test_deputy_has_same_rights_and_log_records_who_acted(client):
    _make_deputy(client)
    u1, deputy = _login(client, "u1"), _login(client, "u2")
    item = _material(client, u1)
    assert item["status"] == "review"
    # Подане видно заступнику в черзі.
    queue = client.get("/api/catalog/moderation", headers=deputy).get_json()
    assert any(x["item"]["id"] == item["id"] for x in queue["items"])
    assert _status(client, deputy, item["id"], "published").status_code == 200
    row = (ReviewLog.query.filter_by(item_type="resource", item_id=item["id"],
                                     to_status="published").one())
    assert row.actor_role == "deputy" and row.actor_name == "u2" and row.created_at
    submit = ReviewLog.query.filter_by(item_id=item["id"], to_status="review").one()
    assert submit.actor_role == "author"
    # Автор бачить, хто й коли погодив.
    card = client.get(f"/api/catalog/resources/{item['id']}", headers=u1).get_json()
    assert card["last_moderation"]["by"] == "u2"
    assert card["last_moderation"]["role"] == "deputy"


def test_moderator_role_recorded_for_main_moderator(client):
    item = _material(client, _login(client, "u1"))
    _status(client, _login(client, "sm"), item["id"], "published")
    row = ReviewLog.query.filter_by(item_id=item["id"], to_status="published").one()
    assert row.actor_role == "moderator"


def test_material_without_section_goes_to_admin_only(client):
    u1 = _login(client, "u1")
    res = client.post("/api/catalog/resources", headers=u1, json={
        "resource_type": "prompt", "name": "Без розділу", "description": "Опис",
        "body": "Текст", "status": "published"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "section_required"


def test_unapproved_material_hidden_from_regular_users(client):
    item = _material(client, _login(client, "u1"))
    u2 = _login(client, "u2")
    assert item["id"] not in [r["id"] for r in
                              client.get("/api/catalog/resources", headers=u2).get_json()]
    assert client.get(f"/api/catalog/resources/{item['id']}", headers=u2).status_code in (403, 404)


# ------------------------ DEV-03: погодження правок з автором ------------------------

def test_moderator_edits_require_author_agreement_before_publishing(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    User.query.filter_by(username="u1").one().email = "u1@example.com"
    db.session.commit()
    item = _material(client, u1)
    res = client.patch(f"/api/catalog/resources/{item['id']}", headers=sm,
                       json={"description": "Опис, який виправив модератор"})
    assert res.status_code == 200
    card = res.get_json()
    assert card["moderator_edit"]["by"] == "sm"
    assert card["author_contact"] == {"name": "u1", "email": "u1@example.com"}

    res = _status(client, sm, item["id"], "published")
    assert res.status_code == 409
    assert res.get_json()["error"] == "author_agreement_required"
    assert CatalogResource.query.get(item["id"]).status == "review"

    res = _status(client, sm, item["id"], "published", author_agreed=True)
    assert res.status_code == 200 and res.get_json()["status"] == "published"
    assert res.get_json()["moderator_edit"] is None
    row = ReviewLog.query.filter_by(item_id=item["id"], to_status="published").one()
    assert "погоджено з автором" in row.note


def test_approval_without_moderator_edits_needs_no_agreement(client):
    item = _material(client, _login(client, "u1"))
    assert _status(client, _login(client, "sm"), item["id"], "published").status_code == 200


def test_returning_material_clears_moderator_edit(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    item = _material(client, u1)
    client.patch(f"/api/catalog/resources/{item['id']}", headers=sm, json={"description": "Нове"})
    _status(client, sm, item["id"], "draft", note="Додайте приклад")
    assert CatalogResource.query.get(item["id"]).mod_edit_at is None


def test_author_contact_not_shown_to_author(client):
    u1 = _login(client, "u1")
    item = _material(client, u1)
    card = client.get(f"/api/catalog/resources/{item['id']}", headers=u1).get_json()
    assert "author_contact" not in card and "moderator_edit" not in card


def test_skill_moderator_edit_requires_agreement(client):
    u1, sm = _login(client, "u1"), _login(client, "sm")
    res = _post_zip(client, u1, "/api/skills/upload", {"SKILL.md": SKILL_MD},
                    status="published", section_id=str(sm_section_id()))
    skill = res.get_json()
    assert skill["status"] == "review"
    client.patch(f"/api/skills/{skill['id']}", headers=sm, json={"description": "Краще"})
    assert _status(client, sm, skill["id"], "published", kind="skills").status_code == 409
    assert _status(client, sm, skill["id"], "published", kind="skills",
                   author_agreed=True).status_code == 200


# ------------------------------ DEV-04: архів ------------------------------

def test_moderator_archives_and_restores_material(client):
    sm, u1 = _login(client, "sm"), _login(client, "u1")
    item = _material(client, u1)
    _status(client, sm, item["id"], "published")
    assert _status(client, sm, item["id"], "archived", note="Застаріло").status_code == 200
    assert item["id"] not in [r["id"] for r in
                              client.get("/api/catalog/resources", headers=u1).get_json()]
    res = _status(client, sm, item["id"], "published")
    assert res.status_code == 200 and res.get_json()["status"] == "published"
    kinds = [n.title for n in Notification.query.filter_by(kind="content_moderated")]
    assert "Матеріал повернуто з архіву" in kinds


def test_moderator_archives_skill_and_author_cannot(client):
    sm, u1, u2 = _login(client, "sm"), _login(client, "u1"), _login(client, "u2")
    res = _post_zip(client, u1, "/api/skills/upload", {"SKILL.md": SKILL_MD},
                    status="published", section_id=str(sm_section_id()))
    sid = res.get_json()["id"]
    _status(client, sm, sid, "published", kind="skills")
    assert _status(client, u1, sid, "archived", kind="skills").status_code == 400
    assert _status(client, sm, sid, "archived", kind="skills").status_code == 200
    assert sid not in [s["id"] for s in client.get("/api/skills", headers=u2).get_json()]
    assert client.get(f"/api/skills/{sid}", headers=u2).status_code == 404
    assert sid in [s["id"] for s in client.get("/api/skills", headers=sm).get_json()]
    assert _status(client, sm, sid, "published", kind="skills").status_code == 200
    assert client.get(f"/api/skills/{sid}", headers=u2).status_code == 200


def test_skill_review_log_for_moderators_only(client):
    sm, u1 = _login(client, "sm"), _login(client, "u1")
    sid = _post_zip(client, u1, "/api/skills/upload", {"SKILL.md": SKILL_MD},
                    status="published", section_id=str(sm_section_id())).get_json()["id"]
    assert client.get(f"/api/skills/{sid}/review-log", headers=u1).status_code == 403
    rows = client.get(f"/api/skills/{sid}/review-log", headers=sm).get_json()
    assert rows[0]["to_status"] == "review" and rows[0]["actor_role"] == "author"


# ------------------------- DEV-05: ролі з груп Entra -------------------------

@pytest.fixture
def entra_groups(app):
    app.config.update(ENTRA_TENANT_ID="t", ENTRA_CLIENT_ID="c",
                      ENTRA_GROUP_ADMIN="grp-admins", ENTRA_GROUP_EXPERT="grp-experts, AI.Expert",
                      ENTRA_GROUP_SECTION_MANAGER="grp-moderators")
    return app


def _claims(**over):
    out = {"oid": "oid-1", "preferred_username": "olena@example.com", "name": "Олена"}
    out.update(over)
    return out


def test_groups_grant_and_revoke_roles(client, entra_groups):
    from app.api.auth import upsert_entra_user
    from app.services import entra_service
    claims = _claims(groups=["GRP-ADMINS", "grp-experts"])
    user = upsert_entra_user(claims, entra_service.member_of(claims))
    assert "admin" in user.role_codes and user.is_expert
    assert user.role_sources == {"admin": "entra"} and user.expert_source == "entra"
    claims = _claims(groups=[])
    user = upsert_entra_user(claims, entra_service.member_of(claims))
    assert user.role_codes == [] and not user.is_expert


def test_app_roles_claim_counts_like_groups(client, entra_groups):
    from app.api.auth import upsert_entra_user
    from app.services import entra_service
    claims = _claims(roles=["AI.Expert"])
    assert upsert_entra_user(claims, entra_service.member_of(claims)).is_expert


def test_manual_roles_survive_group_sync(client, entra_groups):
    from app.api.auth import upsert_entra_user
    user = upsert_entra_user(_claims(), set())
    admin_role = Role.query.filter_by(code="admin").one()
    db.session.add(UserRole(user_id=user.id, role_id=admin_role.id, source="manual"))
    user.is_expert, user.expert_source = True, "manual"
    db.session.commit()
    user = upsert_entra_user(_claims(), set())
    assert "admin" in user.role_codes and user.is_expert


def test_unknown_membership_changes_nothing(client, entra_groups):
    from app.api.auth import upsert_entra_user
    from app.services import entra_service
    user = upsert_entra_user(_claims(), {"grp-admins"})
    assert "admin" in user.role_codes
    # Груп забагато, токена для Graph немає — ролі лишаються як були.
    overage = _claims(_claim_names={"groups": "src1"})
    assert entra_service.member_of(overage) is None
    user = upsert_entra_user(overage, None)
    assert "admin" in user.role_codes


def test_admin_edit_keeps_group_source(client, entra_groups):
    from app.api.auth import upsert_entra_user
    user = upsert_entra_user(_claims(), {"grp-moderators"})
    res = client.patch(f"/api/users/{user.id}", headers=_admin(client), json={
        "roles": ["section_manager"], "managed_section_ids": [],
        "full_name": "Олена Модераторка"})
    assert res.status_code == 200
    assert res.get_json()["role_sources"] == {"section_manager": "entra"}


def test_without_group_settings_login_keeps_roles(client, app):
    from app.api.auth import upsert_entra_user
    app.config.update(ENTRA_TENANT_ID="t", ENTRA_CLIENT_ID="c")
    user = upsert_entra_user(_claims(groups=["grp-admins"]), {"grp-admins"})
    assert user.role_codes == []


# ------------------------- DEV-07: рівень і доступ -------------------------

def _levels():
    terms = []
    for pos, name in enumerate(("User", "Integrator", "Creator")):
        t = CatalogTerm(kind="maturity", name=name, name_norm=name.lower(), position=pos)
        db.session.add(t)
        terms.append(t)
    db.session.flush()
    return terms


def _published(name, level=None, featured=False):
    r = CatalogResource(resource_type="prompt", name=name, description="Опис", body="Текст",
                        status="published", section_id=sm_section_id(), is_featured=featured)
    db.session.add(r)
    db.session.flush()
    if level is not None:
        db.session.add(CatalogResourceMaturity(resource_id=r.id, term_id=level.id))
    return r


def test_level_does_not_limit_access(client):
    user_lvl, integrator, creator = _levels()
    hard = _published("Для творців", creator)
    u1 = User.query.filter_by(username="u1").one()
    u1.maturity_level_id = user_lvl.id
    db.session.commit()
    headers = _login(client, "u1")
    assert hard.id in [r["id"] for r in
                       client.get("/api/catalog/resources", headers=headers).get_json()]
    assert client.get(f"/api/catalog/resources/{hard.id}", headers=headers).status_code == 200


def test_recommendations_put_current_then_next_level_first(client):
    user_lvl, integrator, creator = _levels()
    _published("Популярне без рівня", featured=True)
    _published("На рівень вище", integrator)
    _published("Для творців", creator)
    _published("Мій рівень", user_lvl)
    u1 = User.query.filter_by(username="u1").one()
    u1.maturity_level_id = user_lvl.id
    db.session.commit()
    items = client.get("/api/catalog/recommendations",
                       headers=_login(client, "u1")).get_json()["items"]
    names = [i["name"] for i in items]
    assert names[:2] == ["Мій рівень", "На рівень вище"]
    assert items[1]["recommend_reason"] == "Допоможе перейти на рівень «Integrator»"
    assert "Для творців" in names
