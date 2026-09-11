"""Рівні зрілості, навчальні маршрути та рекомендації (AKH-13…AKH-15)."""
from app.extensions import db
from app.models import LearningProgress, LearningPathStep, User


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _seed_terms(app):
    from app.core.schema import seed_catalog_terms
    with app.app_context():
        seed_catalog_terms()


def _levels(client, headers):
    rows = client.get("/api/catalog/terms?kind=maturity", headers=headers).get_json()
    return {r["name"]: r["id"] for r in rows}


def _resource(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published"}
    payload.update(over)
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _path(client, headers, **over):
    payload = {"name": "Старт з AI", "description": "Перші кроки"}
    payload.update(over)
    return client.post("/api/paths", json=payload, headers=headers)


# -------------------------- Рівні зрілості --------------------------

def test_maturity_levels_seeded_in_order(client, app):
    _seed_terms(app)
    rows = client.get("/api/catalog/terms?kind=maturity",
                      headers=_admin(client)).get_json()
    assert [r["name"] for r in rows] == ["Aware", "User", "Integrator",
                                         "Innovator", "Transformer"]


def test_resource_takes_several_levels(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    item = _resource(client, h, maturity_ids=[lv["Aware"], lv["User"]])
    assert item["maturity_names"] == ["Aware", "User"]


def test_maturity_outside_dictionary_rejected(client, app):
    _seed_terms(app)
    h = _admin(client)
    assert client.post("/api/catalog/resources",
                       json={"resource_type": "prompt", "name": "X", "body": "t",
                             "description": "d", "maturity_ids": [9999]},
                       headers=h).status_code == 400


def test_filter_by_maturity(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    _resource(client, h, name="Для новачка", maturity_ids=[lv["Aware"]])
    _resource(client, h, name="Для просунутих", maturity_ids=[lv["Innovator"]])
    _resource(client, h, name="Без рівня")

    found = client.get(f"/api/catalog/resources?maturity_id={lv['Aware']}",
                       headers=h).get_json()
    assert [i["name"] for i in found] == ["Для новачка"]


def test_material_without_level_stays_visible(client, app):
    _seed_terms(app)
    h = _admin(client)
    _resource(client, h, name="Без рівня")
    names = [i["name"] for i in client.get("/api/catalog/resources",
                                           headers=h).get_json()]
    assert "Без рівня" in names


def test_profile_level_saved_and_next_level_suggested(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    assert client.patch("/api/catalog/profile",
                        json={"maturity_level_id": lv["User"],
                              "department": "Закупівлі"},
                        headers=h).status_code == 200

    data = client.get("/api/catalog/maturity", headers=h).get_json()
    assert data["current"]["name"] == "User"
    assert data["next"]["name"] == "Integrator"
    assert data["next_hint"]


def test_top_level_has_no_next(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    client.patch("/api/catalog/profile",
                 json={"maturity_level_id": lv["Transformer"]}, headers=h)
    assert client.get("/api/catalog/maturity", headers=h).get_json()["next"] is None


def test_maturity_selection_matches_user_level(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    _resource(client, h, name="Для Aware", maturity_ids=[lv["Aware"]])
    _resource(client, h, name="Для Innovator", maturity_ids=[lv["Innovator"]])
    client.patch("/api/catalog/profile", json={"maturity_level_id": lv["Aware"]},
                 headers=h)

    data = client.get("/api/catalog/maturity", headers=h).get_json()
    assert [i["name"] for i in data["items"]] == ["Для Aware"]


def test_merging_maturity_moves_materials(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    rid = _resource(client, h, maturity_ids=[lv["Aware"]])["id"]
    assert client.post(f"/api/catalog/terms/{lv['Aware']}/merge",
                       json={"into": lv["User"]}, headers=h).status_code == 200
    item = client.get(f"/api/catalog/resources/{rid}", headers=h).get_json()
    assert item["maturity_names"] == ["User"]


# ------------------------ Навчальні маршрути ------------------------

def test_path_crud(client):
    h = _admin(client)
    created = _path(client, h)
    assert created.status_code == 201, created.get_json()
    pid = created.get_json()["id"]
    assert client.patch(f"/api/paths/{pid}", json={"name": "Старт"},
                        headers=h).get_json()["name"] == "Старт"
    assert client.delete(f"/api/paths/{pid}", headers=h).status_code == 200


def test_duplicate_path_rejected(client):
    h = _admin(client)
    _path(client, h)
    assert _path(client, h, name="старт з ai").status_code == 409


def test_plain_user_may_not_create_path(client):
    assert _path(client, _login(client, "u1")).status_code == 403


def test_step_needs_target(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    assert client.post(f"/api/paths/{pid}/steps", json={"title": "Крок"},
                       headers=h).status_code == 400


def test_external_step_needs_title(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    assert client.post(f"/api/paths/{pid}/steps",
                       json={"external_url": "https://e.com"},
                       headers=h).status_code == 400


def test_step_points_to_existing_material(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    rid = _resource(client, h)["id"]
    step = client.post(f"/api/paths/{pid}/steps", json={"resource_id": rid},
                       headers=h)
    assert step.status_code == 201
    assert step.get_json()["resource_name"] == "Промпт"
    assert step.get_json()["available"] is True

    assert client.post(f"/api/paths/{pid}/steps", json={"resource_id": 9999},
                       headers=h).status_code == 404


def test_progress_saved_and_percentage_correct(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    first = client.post(f"/api/paths/{pid}/steps",
                        json={"resource_id": _resource(client, h, name="A")["id"]},
                        headers=h).get_json()["id"]
    client.post(f"/api/paths/{pid}/steps",
                json={"resource_id": _resource(client, h, name="B")["id"]},
                headers=h)

    u1 = _login(client, "u1")
    done = client.post(f"/api/paths/{pid}/steps/{first}/complete", headers=u1)
    assert done.status_code == 200
    data = done.get_json()
    assert data["steps_done"] == 1 and data["progress_pct"] == 50


def test_progress_is_per_user(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    step = client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h)["id"]},
                       headers=h).get_json()["id"]
    u1 = _login(client, "u1")
    client.post(f"/api/paths/{pid}/steps/{step}/complete", headers=u1)

    assert client.get(f"/api/paths/{pid}", headers=u1).get_json()["steps_done"] == 1
    assert client.get(f"/api/paths/{pid}",
                      headers=_login(client, "u2")).get_json()["steps_done"] == 0


def test_progress_can_be_undone(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    step = client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h)["id"]},
                       headers=h).get_json()["id"]
    u1 = _login(client, "u1")
    client.post(f"/api/paths/{pid}/steps/{step}/complete", headers=u1)
    undone = client.post(f"/api/paths/{pid}/steps/{step}/complete",
                         json={"done": False}, headers=u1)
    assert undone.get_json()["steps_done"] == 0


def test_next_step_points_where_user_stopped(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    ids = [client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h, name=f"M{i}")["id"]},
                       headers=h).get_json()["id"] for i in range(3)]
    u1 = _login(client, "u1")
    client.post(f"/api/paths/{pid}/steps/{ids[0]}/complete", headers=u1)
    assert client.get(f"/api/paths/{pid}",
                      headers=u1).get_json()["next_step_id"] == ids[1]


def test_deleting_material_keeps_path_intact(client, app):
    """Крок лишається на місці й позначається недоступним — прогрес не зсувається."""
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    rid = _resource(client, h, name="Зникне")["id"]
    step = client.post(f"/api/paths/{pid}/steps", json={"resource_id": rid},
                       headers=h).get_json()["id"]
    client.post(f"/api/paths/{pid}/steps",
                json={"resource_id": _resource(client, h, name="Лишиться")["id"]},
                headers=h)
    u1 = _login(client, "u1")
    client.post(f"/api/paths/{pid}/steps/{step}/complete", headers=u1)

    assert client.delete(f"/api/catalog/resources/{rid}", headers=h).status_code == 200
    data = client.get(f"/api/paths/{pid}", headers=u1).get_json()
    assert data["steps_total"] == 2                # крок не зник
    assert data["steps"][0]["available"] is False  # але позначений недоступним
    assert data["steps_done"] == 1                 # прогрес збережено


def test_steps_reordered(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    ids = [client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h, name=f"M{i}")["id"]},
                       headers=h).get_json()["id"] for i in range(3)]
    res = client.post(f"/api/paths/{pid}/steps/reorder",
                      json={"order": list(reversed(ids))}, headers=h)
    assert res.status_code == 200
    assert [s["id"] for s in res.get_json()["steps"]] == list(reversed(ids))


def test_reorder_requires_all_steps(client):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    step = client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h)["id"]},
                       headers=h).get_json()["id"]
    client.post(f"/api/paths/{pid}/steps",
                json={"resource_id": _resource(client, h, name="B")["id"]}, headers=h)
    assert client.post(f"/api/paths/{pid}/steps/reorder", json={"order": [step]},
                       headers=h).status_code == 400


def test_deleting_step_renumbers_the_rest(client, app):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    ids = [client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h, name=f"M{i}")["id"]},
                       headers=h).get_json()["id"] for i in range(3)]
    client.delete(f"/api/paths/{pid}/steps/{ids[0]}", headers=h)
    with app.app_context():
        positions = [s.position for s in LearningPathStep.query
                     .filter_by(path_id=pid).order_by(LearningPathStep.position)]
    assert positions == [0, 1]


def test_mine_filter_shows_started_paths(client):
    h = _admin(client)
    started = _path(client, h).get_json()["id"]
    _path(client, h, name="Інший маршрут")
    step = client.post(f"/api/paths/{started}/steps",
                       json={"resource_id": _resource(client, h)["id"]},
                       headers=h).get_json()["id"]
    u1 = _login(client, "u1")
    client.post(f"/api/paths/{started}/steps/{step}/complete", headers=u1)

    assert [p["id"] for p in client.get("/api/paths?mine=1",
                                        headers=u1).get_json()] == [started]


def test_starter_path_filter(client):
    h = _admin(client)
    _path(client, h, is_starter=True)
    _path(client, h, name="Просунутий")
    rows = client.get("/api/paths?starter=1", headers=_login(client, "u1")).get_json()
    assert [p["name"] for p in rows] == ["Старт з AI"]


def test_inactive_path_hidden_from_user(client):
    h = _admin(client)
    pid = _path(client, h, is_active=False).get_json()["id"]
    assert client.get(f"/api/paths/{pid}",
                      headers=_login(client, "u1")).status_code == 403
    assert client.get("/api/paths", headers=_login(client, "u1")).get_json() == []
    assert client.get(f"/api/paths/{pid}", headers=h).status_code == 200


def test_deleting_path_clears_progress(client, app):
    h = _admin(client)
    pid = _path(client, h).get_json()["id"]
    step = client.post(f"/api/paths/{pid}/steps",
                       json={"resource_id": _resource(client, h)["id"]},
                       headers=h).get_json()["id"]
    client.post(f"/api/paths/{pid}/steps/{step}/complete", headers=_login(client, "u1"))
    client.delete(f"/api/paths/{pid}", headers=h)
    with app.app_context():
        assert LearningProgress.query.count() == 0


# ------------------------- Рекомендації -------------------------

def test_recommendations_have_reasons(client, app):
    _seed_terms(app)
    h = _admin(client)
    _resource(client, h, name="Рекомендований", is_featured=True)
    _resource(client, h, name="Звичайний")

    items = client.get("/api/catalog/recommendations", headers=h).get_json()["items"]
    assert items
    assert all(i["recommend_reason"] for i in items)
    assert items[0]["name"] == "Рекомендований"


def test_recommendations_skip_seen_materials(client, app):
    _seed_terms(app)
    h = _admin(client)
    rid = _resource(client, h, name="Переглянутий")["id"]
    _resource(client, h, name="Новий")
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    names = [i["name"] for i in
             client.get("/api/catalog/recommendations", headers=h).get_json()["items"]]
    assert "Переглянутий" not in names and "Новий" in names


def test_hidden_recommendation_does_not_return(client, app):
    _seed_terms(app)
    h = _admin(client)
    rid = _resource(client, h, name="Непотрібне")["id"]
    _resource(client, h, name="Потрібне")

    assert client.post("/api/catalog/recommendations/hide",
                       json={"item_id": rid}, headers=h).status_code == 200
    names = [i["name"] for i in
             client.get("/api/catalog/recommendations", headers=h).get_json()["items"]]
    assert "Непотрібне" not in names and "Потрібне" in names


def test_hiding_is_per_user(client, app):
    _seed_terms(app)
    h = _admin(client)
    rid = _resource(client, h, name="Матеріал")["id"]
    client.post("/api/catalog/recommendations/hide", json={"item_id": rid}, headers=h)
    names = [i["name"] for i in client.get("/api/catalog/recommendations",
                                           headers=_login(client, "u1")).get_json()["items"]]
    assert "Матеріал" in names


def test_recommendation_reason_mentions_user_level(client, app):
    _seed_terms(app)
    h = _admin(client)
    lv = _levels(client, h)
    client.patch("/api/catalog/profile", json={"maturity_level_id": lv["User"]},
                 headers=h)
    _resource(client, h, name="Під рівень", maturity_ids=[lv["User"]])

    item = next(i for i in client.get("/api/catalog/recommendations",
                                      headers=h).get_json()["items"]
                if i["name"] == "Під рівень")
    assert "User" in item["recommend_reason"]


def test_new_user_gets_meaningful_recommendations(client, app):
    """Без історії блок має показувати щось осмислене, а не порожнечу."""
    _seed_terms(app)
    _resource(client, _admin(client), name="Перший матеріал")
    items = client.get("/api/catalog/recommendations",
                       headers=_login(client, "u1")).get_json()["items"]
    assert items and items[0]["recommend_reason"]


def test_hide_rejects_unknown_material(client):
    assert client.post("/api/catalog/recommendations/hide", json={"item_id": 9999},
                       headers=_admin(client)).status_code == 404
