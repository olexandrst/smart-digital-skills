"""Знайомство з інтерфейсом, інструкція в застосунку й favicon."""
from tests.conftest import auth, login


def test_tour_shown_until_completed_and_can_be_replayed(client):
    res = client.post("/api/auth/login", json={"username": "u1", "password": "pass"})
    body = res.get_json()
    assert body["user"]["tour_completed_at"] is None
    h = auth(body["access_token"])

    done = client.patch("/api/catalog/profile", json={"tour_completed": True}, headers=h)
    assert done.status_code == 200
    assert done.get_json()["tour_completed_at"]
    assert client.get("/api/auth/me", headers=h).get_json()["tour_completed_at"]
    again = client.post("/api/auth/login", json={"username": "u1", "password": "pass"})
    assert again.get_json()["user"]["tour_completed_at"]

    # «Показати знову» скидає позначку
    reset = client.patch("/api/catalog/profile", json={"tour_completed": False}, headers=h)
    assert reset.get_json()["tour_completed_at"] is None
    assert client.get("/api/auth/me", headers=h).get_json()["tour_completed_at"] is None


def test_tour_flag_is_personal_and_keeps_other_profile_fields(client):
    h1 = auth(login(client, "u1", "pass"))
    client.patch("/api/catalog/profile", json={"department": "Закупівлі"}, headers=h1)
    client.patch("/api/catalog/profile", json={"tour_completed": True}, headers=h1)
    me = client.get("/api/auth/me", headers=h1).get_json()
    assert me["department"] == "Закупівлі" and me["tour_completed_at"]
    h2 = auth(login(client, "u2", "pass"))
    assert client.get("/api/auth/me", headers=h2).get_json()["tour_completed_at"] is None


def test_guide_page_and_its_images_are_served(client):
    res = client.get("/guide/")
    assert res.status_code == 200
    assert res.mimetype == "text/html"
    assert "no-cache" in res.headers["Cache-Control"]
    html = res.get_data(as_text=True)
    assert "Перші кроки в AI Knowledge Hub" in html
    assert 'src="img/02-filters.jpg"' in html
    assert client.get("/guide").status_code in (301, 308)
    img = client.get("/guide/img/02-filters.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"


def test_app_links_guide_and_favicon(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="help-btn"' in html and 'href="guide/"' in html and 'target="_blank"' in html
    assert 'rel="icon" href="favicon.svg"' in html
    for path, kind in (("/favicon.svg", "image/svg+xml"), ("/favicon.ico", None),
                       ("/apple-touch-icon.png", "image/png")):
        res = client.get(path)
        assert res.status_code == 200, path
        if kind:
            assert res.mimetype == kind


def test_guide_drops_sections_removed_after_feedback(client):
    """Фідбек тестування, п. 10, 12, 13 і 11: очевидне з інструкції прибрано,
    «Як увійти» живе на сторінці входу, без речення про миттєву публікацію."""
    guide = client.get("/guide/").get_data(as_text=True)
    for gone in ("Що з чим робити", "Екран за хвилину", 'id="tour"',
                 'id="login"', "Ваш матеріал одразу в каталозі"):
        assert gone not in guide, gone
    index = client.get("/").get_data(as_text=True)
    assert 'id="login-help"' in index and "Як увійти до «AI Knowledge Hub»" in index
