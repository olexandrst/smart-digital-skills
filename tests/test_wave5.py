"""Хвиля 5 фідбеку тестування: аналітика за розділами, сповіщення про ідеї,
кольори й виділення в тексті (імпорт .docx і пошук)."""
from app.extensions import db
from app.models import CatalogSection, Notification, User
from app.services import search_service
from app.services.text_extract_service import nearest_color
from tests.conftest import login, auth, sm_section_id
from tests.test_text_extract import W, _docx, _send


def _login(client, username, password="pass"):
    return auth(login(client, username, password))


def _admin(client):
    return _login(client, "admin", "Admin123!")


def _other_section():
    section = CatalogSection(name="Чужий розділ")
    db.session.add(section)
    db.session.commit()
    return section.id


def _publish(client, name, section_id):
    res = client.post("/api/catalog/resources", headers=_admin(client), json={
        "resource_type": "prompt", "name": name, "description": "Опис", "body": "Текст",
        "status": "published", "section_id": section_id})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


# ------------------------- Аналітика за розділами -------------------------

def test_manager_analytics_limited_to_own_sections(client):
    other = _other_section()
    mine = _publish(client, "Мій матеріал", sm_section_id())
    alien = _publish(client, "Чужий матеріал", other)
    admin, sm = _admin(client), _login(client, "sm")
    for rid in (mine, alien, alien):
        client.post(f"/api/catalog/resources/{rid}/open", headers=admin)

    data = client.get("/api/catalog/analytics", headers=sm).get_json()
    assert data["scope"] == {"all": False, "section_ids": [sm_section_id()],
                             "section_names": ["Розділ менеджера"]}
    assert [s["name"] for s in data["by_section"]] == ["Розділ менеджера"]
    assert data["by_type"]["prompt"] == 1
    assert [t["name"] for t in data["top_opened"]] == ["Мій матеріал"]

    full = client.get("/api/catalog/analytics", headers=admin).get_json()
    assert full["scope"]["all"] is True
    assert {s["name"] for s in full["by_section"]} >= {"Розділ менеджера", "Чужий розділ"}


def test_manager_kpi_counts_only_own_views(client):
    other = _other_section()
    mine = _publish(client, "Мій матеріал", sm_section_id())
    alien = _publish(client, "Чужий матеріал", other)
    admin, sm = _admin(client), _login(client, "sm")
    for rid in (mine, alien, alien):
        name = "Мій матеріал" if rid == mine else "Чужий матеріал"
        assert client.post("/api/catalog/views", headers=admin, json={
            "target_type": "resource", "target_id": rid, "target_name": name}).status_code in (200, 201)

    k = client.get("/api/catalog/kpi", headers=sm).get_json()
    assert k["scope"]["all"] is False
    assert k["engagement"]["views"]["value"] == 1
    assert [t["name"] for t in k["engagement"]["top_viewed"]] == ["Мій матеріал"]
    assert k["content"]["total"] == 2        # промпт і навичка Summarizer розділу менеджера
    assert [s["name"] for s in k["content"]["by_section"]] == ["Розділ менеджера"]

    full = client.get("/api/catalog/kpi", headers=admin).get_json()
    assert full["engagement"]["views"]["value"] == 2   # повторний перегляд у сесії не рахується

    csv = client.get("/api/catalog/kpi/export", headers=sm).get_data(as_text=True)
    assert "Розділи: Розділ менеджера" in csv


def test_manager_without_sections_has_no_analytics(client):
    """Менеджер без розділів нічого не модерує — і аналітики не бачить."""
    sm = User.query.filter_by(username="sm").one()
    sm.managed_sections = []
    db.session.commit()
    assert client.get("/api/catalog/analytics",
                      headers=_login(client, "sm")).status_code == 403


def test_users_have_no_analytics(client):
    h = _login(client, "u1")
    for url in ("/api/catalog/analytics", "/api/catalog/kpi", "/api/catalog/search-analytics"):
        assert client.get(url, headers=h).status_code == 403


# ------------------------- Сповіщення про ідеї -------------------------

def test_new_idea_notifies_admins_and_ideas_owners(client):
    owner = User.query.filter_by(username="u2").one()
    owner.is_ideas_owner = True
    db.session.commit()
    res = client.post("/api/ideas", headers=_login(client, "u1"),
                      json={"title": "Бот для закупівель", "body": "Опис ідеї"})
    assert res.status_code == 201, res.get_json()
    got = {n.user_id for n in Notification.query.filter_by(kind="idea_submitted")}
    admin = User.query.filter_by(username="admin").one()
    sm = User.query.filter_by(username="sm").one()
    assert got == {admin.id, owner.id}
    assert sm.id not in got                       # менеджерам розділів ідеї не надходять
    note = Notification.query.filter_by(kind="idea_submitted", user_id=owner.id).one()
    assert "Бот для закупівель" in note.body


def test_owner_is_not_notified_about_own_idea(client):
    owner = User.query.filter_by(username="u2").one()
    owner.is_ideas_owner = True
    db.session.commit()
    client.post("/api/ideas", headers=_login(client, "u2"),
                json={"title": "Своя ідея", "body": "Опис"})
    assert Notification.query.filter_by(kind="idea_submitted", user_id=owner.id).count() == 0


# --------------------- Кольори й виділення в тексті ---------------------

def test_nearest_color_maps_word_colors():
    assert nearest_color("C00000") == "red"
    assert nearest_color("FF0000") == "red"
    assert nearest_color("00B050") == "green"
    assert nearest_color("0070C0") == "blue"
    assert nearest_color("7030A0") == "purple"
    assert nearest_color("ED7D31") == "orange"
    assert nearest_color("FFC000") == "orange"     # жовтий — до найближчого з набору
    assert nearest_color("808080") == "gray"
    assert nearest_color("000000") is None         # чорний — звичайний текст
    assert nearest_color("262626") is None
    assert nearest_color("FFFFFF") is None
    assert nearest_color("auto") is None


def test_docx_color_underline_and_highlight(client):
    token = login(client, "u1", "pass")

    def run(text, rpr=""):
        return f"<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space=\"preserve\">{text}</w:t></w:r>"

    para = ("xml", "<w:p>"
            + run("Увага", '<w:b/><w:color w:val="C00000"/>')
            + run(": перевірте ")
            + run("строки", '<w:highlight w:val="yellow"/>')
            + run(" і ")
            + run("суму", '<w:u w:val="single"/>')
            + run(", ")
            + run("чорний", '<w:color w:val="000000"/>')
            + "</w:p>")
    res = _send(client, token, "doc.docx", _docx([para]))
    assert res.status_code == 200, res.get_json()
    text = res.get_json()["text"]
    assert text == ('<span class="tc-red">**Увага**</span>: перевірте <mark>строки</mark> '
                    'і <u>суму</u>, чорний')
    assert W  # той самий простір імен, що й у тестах імпорту


def test_search_ignores_formatting_markup():
    tokens = search_service.tokenize('<span class="tc-red">Увага</span> <mark>строки</mark> <u>сума</u>')
    assert not {"span", "class", "tc", "red", "mark"} & set(tokens)
    assert len(tokens) == 3
    # Плейсхолдери промптів — це слова, їх не чіпаємо.
    assert search_service.tokenize("<документ>")
