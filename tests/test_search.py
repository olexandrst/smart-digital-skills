"""Пошук мовою задачі та AI-помічник (AKH-11, AKH-12)."""
from app.services import search_service, assistant_service


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
    res = client.post("/api/catalog/resources", json=payload, headers=headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _seed_synonyms(app):
    from app.core.schema import seed_search_synonyms
    with app.app_context():
        seed_search_synonyms()


def _catalog(client, headers):
    """Демо-набір, схожий на реальний каталог: різні задачі, різні формулювання."""
    _resource(client, headers, name="Протокол наради за стенограмою",
              description="Перетворює розшифровку зустрічі на протокол із рішеннями.",
              category="Операційна робота", tags="наради, підсумки",
              body="Склади протокол наради за стенограмою нижче.")
    _resource(client, headers, name="Аналіз тендерної документації",
              description="Розбирає ТД на вимоги, строки та ризики.",
              category="Закупівлі", tags="аналітика, документи",
              tools="Copilot", body="Проаналізуй тендерну документацію.")
    _resource(client, headers, resource_type="instruction",
              name="Як писати ефективні промпти",
              description="Базові правила формулювання завдань для моделей.",
              category="Навчання", tags="prompt engineering, основи",
              body="Роль, контекст, задача, формат.")
    _resource(client, headers, resource_type="case",
              name="Кейс: автоматизація обробки заявок у HR",
              description="Агент класифікує звернення та відповідає на типові.",
              category="HR", tags="кейс, автоматизація",
              tools="Copilot Studio", body="Агент обробляє заявки співробітників.")
    _resource(client, headers, resource_type="agent", url="https://a.example.com",
              name="Агент аналітики виробництва", body="",
              description="Відповідає на запитання щодо показників зміни.",
              category="Виробництво", tags="аналітика, звіти")
    _resource(client, headers, resource_type="link", url="https://b.example.com",
              name="Портал знань Metinvest Digital", body="",
              description="Внутрішня база регламентів, шаблонів і навчальних матеріалів.",
              category="Внутрішні ресурси", tags="регламенти")


# ------------------------- Нормалізація тексту -------------------------

def test_stemming_matches_ukrainian_cases():
    """Відмінкові форми одного слова дають одну основу."""
    forms = ["документ", "документи", "документів", "документами"]
    stems = {search_service.stem(f) for f in forms}
    assert len(stems) == 1, stems


def test_derived_words_match_by_common_root():
    """Словотвір стемінг не зводить — його ловить спільний початок основи.

    Там, де основи розходяться раніше («аналіз» / «аналітика»), працює вже не
    морфологія, а словник синонімів — це його задача, не стемера.
    """
    pairs = [("документ", "документація"), ("автоматизувати", "автоматизація"),
             ("промпт", "промпти")]
    for a, b in pairs:
        assert search_service.same_root(search_service.stem(a),
                                        search_service.stem(b)), (a, b)


def test_stop_words_dropped():
    assert search_service.tokenize("я хочу щоб це було для мене") == []


def test_tokenize_strips_punctuation():
    assert "нарад" in search_service.tokenize("Протокол наради, будь ласка!")


# --------------------- Пошук мовою задачі (AKH-11) ---------------------

TASK_QUERIES = [
    ("хочу автоматизувати збір ідей", "Кейс: автоматизація обробки заявок у HR"),
    ("як швидше обробляти документи", "Аналіз тендерної документації"),
    ("потрібен протокол наради", "Протокол наради за стенограмою"),
    ("підсумок зустрічі", "Протокол наради за стенограмою"),
    ("навчитися писати промпти", "Як писати ефективні промпти"),
    ("аналіз тендерів", "Аналіз тендерної документації"),
    ("показники виробництва", "Агент аналітики виробництва"),
    ("де знайти регламенти", "Портал знань Metinvest Digital"),
    ("обробка заявок співробітників", "Кейс: автоматизація обробки заявок у HR"),
    ("автоматизувати звернення в hr", "Кейс: автоматизація обробки заявок у HR"),
    ("документація закупівель", "Аналіз тендерної документації"),
    ("як формулювати завдання для моделі", "Як писати ефективні промпти"),
]


def test_task_language_queries_find_expected_material(client, app):
    """Набір запитів мовою задачі має повертати очікувані матеріали."""
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)

    misses = []
    for query, expected in TASK_QUERIES:
        res = client.get(f"/api/catalog/search?q={query}", headers=h)
        assert res.status_code == 200, res.get_json()
        names = [i["name"] for i in res.get_json()["results"]]
        if expected not in names[:3]:
            misses.append(f"«{query}» → {names[:3]}, очікували «{expected}»")
    assert not misses, "\n".join(misses)


def test_exact_name_match_ranks_first(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    res = client.get("/api/catalog/search?q=Аналіз тендерної документації", headers=h)
    assert res.get_json()["results"][0]["name"] == "Аналіз тендерної документації"


def test_search_tolerates_typo(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    res = client.get("/api/catalog/search?q=тендрної документації", headers=h)
    names = [i["name"] for i in res.get_json()["results"]]
    assert "Аналіз тендерної документації" in names


def test_search_uses_synonym_dictionary(client, app):
    """Слова запиту не зустрічаються в каталозі — знаходить лише словник."""
    h = _admin(client)
    _catalog(client, h)
    query = "скоротити паперову тяганину"
    assert not client.get(f"/api/catalog/search?q={query}", headers=h).get_json()["results"]

    client.post("/api/catalog/synonyms",
                json={"phrase": query, "terms": "тендерної, документації"}, headers=h)
    after = client.get(f"/api/catalog/search?q={query}", headers=h).get_json()
    assert [i["name"] for i in after["results"]][:1] == ["Аналіз тендерної документації"]


def test_disabled_synonym_stops_working(client, app):
    h = _admin(client)
    _catalog(client, h)
    query = "скоротити паперову тяганину"
    sid = client.post("/api/catalog/synonyms",
                      json={"phrase": query, "terms": "тендерної, документації"},
                      headers=h).get_json()["id"]
    assert client.get(f"/api/catalog/search?q={query}", headers=h).get_json()["results"]

    client.patch(f"/api/catalog/synonyms/{sid}", json={"is_active": False}, headers=h)
    assert not client.get(f"/api/catalog/search?q={query}",
                          headers=h).get_json()["results"]


def test_empty_result_returns_nearest_and_marks_unanswered(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    data = client.get("/api/catalog/search?q=ремонт доменної печі", headers=h).get_json()
    assert data["results"] == []
    assert data["unanswered"] is True
    assert data["search_log_id"]


def test_search_logs_query(client, app):
    from app.models import SearchQueryLog
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    client.get("/api/catalog/search?q=протокол наради", headers=h)
    with app.app_context():
        log = SearchQueryLog.query.one()
        assert log.query_text == "протокол наради" and log.results_count > 0


def test_search_hides_drafts_from_plain_user(client, app):
    h = _admin(client)
    _resource(client, h, name="Чернетка протоколу", status="draft",
              description="Протокол наради")
    assert client.get("/api/catalog/search?q=протокол",
                      headers=_login(client, "u1")).get_json()["results"] == []
    assert client.get("/api/catalog/search?q=протокол",
                      headers=h).get_json()["results"]


def test_empty_query_rejected(client):
    assert client.get("/api/catalog/search?q=   ",
                      headers=_admin(client)).status_code == 400


# ------------------------- Словник синонімів -------------------------

def test_synonym_crud(client):
    h = _admin(client)
    created = client.post("/api/catalog/synonyms",
                          json={"phrase": "зробити презентацію", "terms": "слайди, шаблон"},
                          headers=h)
    assert created.status_code == 201
    sid = created.get_json()["id"]
    assert client.patch(f"/api/catalog/synonyms/{sid}", json={"terms": "слайди"},
                        headers=h).get_json()["terms"] == "слайди"
    assert client.delete(f"/api/catalog/synonyms/{sid}", headers=h).status_code == 200


def test_duplicate_synonym_rejected(client):
    h = _admin(client)
    client.post("/api/catalog/synonyms", json={"phrase": "звіт", "terms": "аналітика"},
                headers=h)
    assert client.post("/api/catalog/synonyms",
                       json={"phrase": "ЗВІТ", "terms": "дані"},
                       headers=h).status_code == 409


def test_plain_user_may_not_edit_synonyms(client):
    assert client.post("/api/catalog/synonyms",
                       json={"phrase": "звіт", "terms": "аналітика"},
                       headers=_login(client, "u1")).status_code == 403


def test_default_synonyms_seeded(client, app):
    _seed_synonyms(app)
    _seed_synonyms(app)          # ідемпотентно
    from app.models import DEFAULT_SYNONYMS
    rows = client.get("/api/catalog/synonyms", headers=_admin(client)).get_json()
    assert len(rows) == len(DEFAULT_SYNONYMS)
    assert any(r["phrase"] == "обробляти документи" for r in rows)


# ------------------------- AI-помічник (AKH-12) -------------------------

def test_assistant_returns_only_existing_materials(client, app):
    """Помічник не може повернути посилання, якого немає в базі."""
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    known = {i["name"] for i in client.get("/api/catalog/resources",
                                           headers=h).get_json()}

    data = client.post("/api/catalog/assistant",
                       json={"question": "треба швидше готувати протоколи нарад"},
                       headers=h).get_json()
    assert data["items"], data
    for item in data["items"]:
        assert item["name"] in known
        assert item["id"]
        assert item["assistant_reason"]


def test_assistant_answers_honestly_when_nothing_found(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    data = client.post("/api/catalog/assistant",
                       json={"question": "потрібен графік ремонту прокатного стану"},
                       headers=h).get_json()
    assert data["items"] == [] or data.get("suggest_idea")
    assert "ідеєю" in data["reply"]


def test_assistant_asks_for_clarification_on_one_word(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    data = client.post("/api/catalog/assistant", json={"question": "документи"},
                       headers=h).get_json()
    assert data["clarify"] is True and not data["items"]


def test_assistant_rejects_empty_question(client):
    assert client.post("/api/catalog/assistant", json={"question": "  "},
                       headers=_admin(client)).status_code == 400


def test_assistant_works_in_mock_mode_without_network(client, app):
    """Тести не ходять у мережу: пояснення збираються з полів картки."""
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    with app.app_context():
        items = [{"id": 1, "name": "Протокол наради за стенограмою",
                  "description": "Перетворює розшифровку на протокол.",
                  "resource_type": "prompt", "category": "Операційна робота",
                  "tags": ["наради"], "tools": None, "body": ""}]
        out = assistant_service.answer("підсумок наради", items, use_model=False)
    assert out["items"][0]["assistant_reason"]
    assert out["grounded"] is True


def test_assistant_feedback_recorded(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    log_id = client.post("/api/catalog/assistant",
                         json={"question": "протокол наради зі стенограми"},
                         headers=h).get_json()["search_log_id"]

    res = client.post("/api/catalog/assistant/feedback",
                      json={"search_log_id": log_id, "helpful": True}, headers=h)
    assert res.status_code == 200 and res.get_json()["helpful"] is True


def test_assistant_feedback_guards_foreign_log(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    log_id = client.post("/api/catalog/assistant",
                         json={"question": "протокол наради зі стенограми"},
                         headers=h).get_json()["search_log_id"]
    assert client.post("/api/catalog/assistant/feedback",
                       json={"search_log_id": log_id, "helpful": True},
                       headers=_login(client, "u1")).status_code == 403


# ---------------------- Аналітика запитів (AKH-12) ----------------------

def test_search_analytics_shows_gaps(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    client.get("/api/catalog/search?q=протокол наради", headers=h)
    client.get("/api/catalog/search?q=ремонт доменної печі", headers=h)
    client.get("/api/catalog/search?q=ремонт доменної печі", headers=h)

    data = client.get("/api/catalog/search-analytics", headers=h).get_json()
    assert data["total"] == 3
    assert [q["query"] for q in data["no_results"]] == ["ремонт доменної печі"]
    assert data["no_results"][0]["count"] == 2


def test_search_analytics_counts_assistant_usefulness(client, app):
    _seed_synonyms(app)
    h = _admin(client)
    _catalog(client, h)
    good = client.post("/api/catalog/assistant",
                       json={"question": "протокол наради зі стенограми"},
                       headers=h).get_json()["search_log_id"]
    bad = client.post("/api/catalog/assistant",
                      json={"question": "аналіз тендерної документації закупівлі"},
                      headers=h).get_json()["search_log_id"]
    client.post("/api/catalog/assistant/feedback",
                json={"search_log_id": good, "helpful": True}, headers=h)
    client.post("/api/catalog/assistant/feedback",
                json={"search_log_id": bad, "helpful": False}, headers=h)

    stats = client.get("/api/catalog/search-analytics", headers=h).get_json()["assistant"]
    assert stats["questions"] == 2 and stats["rated"] == 2
    assert stats["helpful"] == 1 and stats["helpful_pct"] == 50.0


def test_search_analytics_hidden_from_plain_user(client):
    assert client.get("/api/catalog/search-analytics",
                      headers=_login(client, "u1")).status_code == 403
