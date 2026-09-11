"""Облік переглядів, сесії, дашборд KPI та опитування (AKH-08…AKH-10)."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import ResourceView, UserSession, SurveyResponse, User
from app.services import usage_service


def _login(client, username, password="pass"):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _resource(client, headers, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=headers)


def _user(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).first()


# --------------------- Облік переглядів і сесій ---------------------

def test_opening_card_records_view_and_keeps_counter(client, app):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]

    res = client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    assert res.status_code == 200
    assert res.get_json()["opens_count"] == 1      # наявний лічильник не зламано

    with app.app_context():
        view = ResourceView.query.one()
        assert view.target_type == "resource"
        assert view.target_id == rid
        assert view.target_name == "Промпт"
        assert view.day == datetime.utcnow().date()


def test_section_view_recorded(client, app):
    h = _admin(client)
    sid = client.post("/api/catalog/sections", json={"name": "Toolbox"},
                      headers=h).get_json()["id"]
    res = client.post("/api/catalog/views",
                      json={"target_type": "section", "target_id": sid}, headers=h)
    assert res.status_code == 201
    with app.app_context():
        view = ResourceView.query.one()
        assert view.target_type == "section" and view.target_name == "Toolbox"


def test_view_rejects_unknown_target_type(client):
    assert client.post("/api/catalog/views",
                       json={"target_type": "planet", "target_id": 1},
                       headers=_admin(client)).status_code == 400


def test_view_rejects_missing_section(client):
    assert client.post("/api/catalog/views",
                       json={"target_type": "section", "target_id": 9999},
                       headers=_admin(client)).status_code == 404


def test_views_share_one_session(client, app):
    h = _admin(client)
    first = _resource(client, h).get_json()["id"]
    second = _resource(client, h, name="Другий").get_json()["id"]
    client.post(f"/api/catalog/resources/{first}/open", headers=h)
    client.post(f"/api/catalog/resources/{second}/open", headers=h)

    with app.app_context():
        assert UserSession.query.count() == 1
        assert UserSession.query.one().views_count == 2


def test_session_breaks_after_idle(client, app):
    """Понад 30 хвилин без активності — нова сесія, а стара закрита."""
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    with app.app_context():
        stale = UserSession.query.one()
        long_ago = datetime.utcnow() - timedelta(minutes=45)
        stale.started_at = long_ago
        stale.last_seen_at = long_ago
        db.session.commit()
        stale_id, stale_last_seen = stale.id, stale.last_seen_at

    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        assert UserSession.query.count() == 2
        closed = UserSession.query.get(stale_id)
        # Закрита моментом останньої активності, а не «зараз»:
        # пауза не має потрапити в тривалість.
        assert closed.ended_at == stale_last_seen
        assert UserSession.query.filter_by(ended_at=None).count() == 1


def test_session_continues_within_window(client, app):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        session = UserSession.query.one()
        session.last_seen_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        assert UserSession.query.count() == 1


def test_repeat_open_in_one_session_counts_once(client, app):
    """Відкрив картку й скопіював промпт — це один перегляд, а не два.

    Лічильник `opens_count` дедуплікація не зачіпає: він рахує саме дії.
    """
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    second = client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    assert second.get_json()["opens_count"] == 2
    with app.app_context():
        assert ResourceView.query.count() == 1
        assert UserSession.query.one().views_count == 1


def test_new_session_counts_the_card_again(client, app):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        session = UserSession.query.one()
        session.last_seen_at = datetime.utcnow() - timedelta(minutes=45)
        db.session.commit()
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        assert ResourceView.query.count() == 2


def test_active_users_counts_distinct_people(client, app):
    h = _admin(client)
    first = _resource(client, h).get_json()["id"]
    second = _resource(client, h, name="Другий").get_json()["id"]
    client.post(f"/api/catalog/resources/{first}/open", headers=h)
    client.post(f"/api/catalog/resources/{second}/open", headers=h)
    client.post(f"/api/catalog/resources/{first}/open", headers=_login(client, "u1"))

    with app.app_context():
        today = datetime.utcnow().date()
        assert usage_service.active_users(today) == 2
        assert usage_service.views_count(today) == 3


def test_returning_gap_counts_only_lapsed_users(client, app):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    client.post(f"/api/catalog/resources/{rid}/open", headers=_login(client, "u1"))

    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        old = datetime.utcnow() - timedelta(days=100)
        ResourceView.query.filter_by(user_id=admin.id).update(
            {ResourceView.day: old.date()})
        db.session.commit()
        assert usage_service.returning_gap(90) == 1     # лише адмін
        assert usage_service.returning_gap(30) == 1
        assert usage_service.returning_gap(200) == 0


# ---------------------------- Дашборд KPI ----------------------------

def test_kpi_hidden_from_plain_user(client):
    assert client.get("/api/catalog/kpi", headers=_login(client, "u1")).status_code == 403
    assert client.get("/api/catalog/kpi/export",
                      headers=_login(client, "u1")).status_code == 403


def test_kpi_rejects_unknown_period(client):
    assert client.get("/api/catalog/kpi?period=5y",
                      headers=_admin(client)).status_code == 400


def test_kpi_has_every_required_block(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    data = client.get("/api/catalog/kpi", headers=h).get_json()
    a, e, c, s = data["audience"], data["engagement"], data["content"], data["search"]
    assert a["dau"] == 1 and a["wau"] == 1 and a["mau"] == 1
    assert a["stickiness_pct"] == 100.0
    assert set(a["churn"]) == {"gap_30", "gap_60", "gap_90"}
    assert e["views"]["value"] == 1
    assert e["avg_depth"] == 1.0 and e["sessions"]["value"] == 1
    assert e["top_viewed"][0]["name"] == "Промпт"
    # total рахує і навички: у фікстурі вже є одна, плюс щойно створений промпт.
    assert c["total"] == 2 and c["by_type"]["prompt"] == 1 and c["by_type"]["skill"] == 1
    assert c["published_pct"] == 100.0          # частка рахується лише за ресурсами
    assert "success_pct" in s and "no_results_pct" in s
    assert set(data["survey"]) >= {"nps", "csat", "ces", "comments"}


def test_kpi_compares_with_previous_period(client, app):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    second = _resource(client, h, name="Другий").get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    with app.app_context():
        # Переносимо перегляд у попередній 7-денний період і закриваємо сесію,
        # щоб наступні відкриття рахувалися заново.
        ResourceView.query.update(
            {ResourceView.day: (datetime.utcnow() - timedelta(days=8)).date()})
        UserSession.query.update({UserSession.ended_at: datetime.utcnow()})
        db.session.commit()
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)
    client.post(f"/api/catalog/resources/{second}/open", headers=h)

    views = client.get("/api/catalog/kpi?period=7d", headers=h).get_json()["engagement"]["views"]
    assert views["value"] == 2 and views["previous"] == 1
    assert views["change_pct"] == 100.0


def test_kpi_counts_reuse_views(client):
    h = _admin(client)
    ready = _resource(client, h, name="Готове", reuse_level="ready").get_json()["id"]
    plain = _resource(client, h, name="Без рівня").get_json()["id"]
    client.post(f"/api/catalog/resources/{ready}/open", headers=h)
    client.post(f"/api/catalog/resources/{plain}/open", headers=h)

    assert client.get("/api/catalog/kpi",
                      headers=h).get_json()["engagement"]["reuse_views"] == 1


def test_kpi_export_matches_dashboard(client):
    h = _admin(client)
    rid = _resource(client, h).get_json()["id"]
    client.post(f"/api/catalog/resources/{rid}/open", headers=h)

    data = client.get("/api/catalog/kpi", headers=h).get_json()
    res = client.get("/api/catalog/kpi/export", headers=h)
    assert res.status_code == 200
    assert "text/csv" in res.headers["Content-Type"]
    text = res.data.decode("utf-8-sig")
    assert f"Відкриттів карток;{data['engagement']['views']['value']}" in text
    assert f"Унікальні користувачі за день;{data['audience']['dau']}" in text
    assert "Промпт;1" in text                     # топ переглядів


def test_kpi_export_respects_period(client):
    h = _admin(client)
    res = client.get("/api/catalog/kpi/export?period=7d", headers=_admin(client))
    assert res.status_code == 200
    data = client.get("/api/catalog/kpi?period=7d", headers=h).get_json()
    assert data["range"]["from"] in res.data.decode("utf-8-sig")


# ---------------------- Опитування NPS/CSAT/CES ----------------------

def _make_eligible(client, headers, count=5):
    """Відкриває кілька РІЗНИХ карток, щоб користувач пройшов поріг показу.

    Саме різних: повторне відкриття однієї картки за сесію — один перегляд.
    """
    admin = _admin(client)
    for i in range(count):
        rid = _resource(client, admin, name=f"Матеріал {i}").get_json()["id"]
        client.post(f"/api/catalog/resources/{rid}/open", headers=headers)
    return rid


def test_survey_not_shown_before_threshold(client):
    h = _login(client, "u1")
    _make_eligible(client, h, count=2)
    assert client.get("/api/catalog/survey/due", headers=h).get_json()["due"] is None


def test_survey_shown_after_threshold(client):
    h = _login(client, "u1")
    _make_eligible(client, h)
    due = client.get("/api/catalog/survey/due", headers=h).get_json()["due"]
    assert due["kind"] == "nps"
    assert due["min"] == 0 and due["max"] == 10
    assert "порекомендуєте" in due["title"]


def test_survey_answer_saved(client, app):
    h = _login(client, "u1")
    _make_eligible(client, h)
    res = client.post("/api/catalog/survey",
                      json={"kind": "nps", "score": 9, "comment": "Зручно"}, headers=h)
    assert res.status_code == 201
    with app.app_context():
        row = SurveyResponse.query.one()
        assert row.score == 9 and row.user_role == "user" and row.comment == "Зручно"


def test_survey_score_validated(client):
    h = _login(client, "u1")
    _make_eligible(client, h)
    assert client.post("/api/catalog/survey", json={"kind": "nps", "score": 11},
                       headers=h).status_code == 400
    assert client.post("/api/catalog/survey", json={"kind": "csat", "score": 0},
                       headers=h).status_code == 400
    assert client.post("/api/catalog/survey", json={"kind": "ces", "score": 5},
                       headers=h).status_code == 201


def test_answered_survey_not_asked_again(client):
    h = _login(client, "u1")
    _make_eligible(client, h)
    client.post("/api/catalog/survey", json={"kind": "nps", "score": 8}, headers=h)
    due = client.get("/api/catalog/survey/due", headers=h).get_json()["due"]
    assert due["kind"] == "csat"          # перейшли до наступного типу, не повторили


def test_dismissed_survey_not_returned_immediately(client):
    h = _login(client, "u1")
    _make_eligible(client, h)
    client.post("/api/catalog/survey", json={"kind": "nps", "dismissed": True}, headers=h)
    assert client.get("/api/catalog/survey/due",
                      headers=h).get_json()["due"]["kind"] == "csat"


def test_survey_interval_configurable_without_code(client, app):
    h = _login(client, "u1")
    _make_eligible(client, h)
    client.post("/api/catalog/survey", json={"kind": "nps", "dismissed": True}, headers=h)

    # Нульовий інтервал очікування — питання повертається одразу.
    assert client.post("/api/catalog/settings", json={"survey_days_after_dismiss": 0},
                       headers=_admin(client)).status_code == 200
    assert client.get("/api/catalog/survey/due",
                      headers=h).get_json()["due"]["kind"] == "nps"


def test_survey_can_be_switched_off(client):
    h = _login(client, "u1")
    _make_eligible(client, h)
    client.post("/api/catalog/settings", json={"survey_enabled": False},
                headers=_admin(client))
    assert client.get("/api/catalog/survey/due", headers=h).get_json()["due"] is None


def test_survey_threshold_configurable(client):
    h = _login(client, "u1")
    _make_eligible(client, h, count=1)
    assert client.get("/api/catalog/survey/due", headers=h).get_json()["due"] is None
    client.post("/api/catalog/settings", json={"survey_min_views": 1},
                headers=_admin(client))
    assert client.get("/api/catalog/survey/due", headers=h).get_json()["due"] is not None


def test_survey_settings_validated(client):
    h = _admin(client)
    assert client.post("/api/catalog/settings", json={"survey_min_views": "багато"},
                       headers=h).status_code == 400
    assert client.post("/api/catalog/settings", json={"survey_days_after_answer": -1},
                       headers=h).status_code == 400


def test_survey_summary_in_kpi(client):
    h = _admin(client)
    _make_eligible(client, h)
    client.post("/api/catalog/survey", json={"kind": "nps", "score": 10}, headers=h)
    u1 = _login(client, "u1")
    _make_eligible(client, u1)
    client.post("/api/catalog/survey",
                json={"kind": "nps", "score": 3, "comment": "Важко шукати"}, headers=u1)

    survey = client.get("/api/catalog/kpi", headers=h).get_json()["survey"]
    nps = survey["nps"]
    assert nps["responses"] == 2
    assert nps["buckets"] == {"promoter": 1, "passive": 0, "detractor": 1}
    assert nps["score"] == 0               # 50% промоутерів − 50% критиків
    assert survey["comments"][0]["comment"] == "Важко шукати"


def test_survey_rejects_unknown_kind(client):
    assert client.post("/api/catalog/survey", json={"kind": "mood", "score": 5},
                       headers=_login(client, "u1")).status_code == 400
