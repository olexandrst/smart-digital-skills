"""Раннер UAT-сценаріїв персон (AKH-19).

Сценарії лежать у `tests/uat_scenarios.yaml` — щоб додати новий, код тут
чіпати не потрібно. Кожен сценарій перевіряє те саме, що перевірятиме людина
на UAT: чи знаходиться потрібний матеріал щонайбільше за два кліки від
головної — пошуком або навігацією «розділ → колекція → картка».

Каталог наповнюється тими самими даними, що й `python -m scripts.seed`, тож
сценарії відтворювані на чистій базі.
"""
import os

import pytest
import yaml

from app.extensions import db
from app.core.schema import seed_catalog_terms, seed_search_synonyms, migrate_legacy_tags

SCENARIOS_FILE = os.path.join(os.path.dirname(__file__), "uat_scenarios.yaml")

# Стеля кліків від головної сторінки до матеріалу (ціль BR-06 / AKH-03).
DEFAULT_MAX_CLICKS = 2


def _load_scenarios():
    with open(SCENARIOS_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["scenarios"]


SCENARIOS = _load_scenarios()


def _scenario_id(scenario):
    return f"{scenario['persona']}: {scenario['goal']}"


@pytest.fixture(scope="module")
def _report():
    """Підсумок «скільки сценаріїв проходить» друкується наприкінці модуля."""
    stats = {"total": 0, "passed": 0, "failures": []}
    yield stats
    share = stats["passed"] / stats["total"] * 100 if stats["total"] else 0
    print(f"\n\nUAT-сценарії: {stats['passed']} з {stats['total']} "
          f"({share:.0f}%)")
    for line in stats["failures"]:
        print(f"  ✗ {line}")


@pytest.fixture
def uat_catalog(app, client):
    """Каталог, наповнений так само, як це робить scripts.seed."""
    import scripts.seed as seed_module
    from app.models import (
        User, CatalogSection, CatalogFolder, CatalogResource,
        CatalogResourceMaturity, CatalogTerm,
    )

    with app.app_context():
        seed_catalog_terms()
        seed_search_synonyms()
        admin = User.query.filter_by(username="admin").first()

        for position, spec in enumerate(seed_module.CATALOG_SECTIONS):
            if not CatalogSection.query.filter_by(name=spec["name"]).first():
                db.session.add(CatalogSection(**spec, position=position))
        db.session.commit()
        sections = {s.name: s.id for s in CatalogSection.query.all()}

        for position, (section_name, name, description, emoji) in enumerate(
                seed_module.CATALOG_FOLDERS):
            section_id = sections.get(section_name)
            if section_id and not CatalogFolder.query.filter_by(
                    section_id=section_id, name=name).first():
                db.session.add(CatalogFolder(section_id=section_id, name=name,
                                             description=description,
                                             icon_emoji=emoji, position=position))
        db.session.commit()
        folders = {f.name: f.id for f in CatalogFolder.query.all()}

        from datetime import datetime, timedelta
        now = datetime.utcnow()
        term_ids = {(t.kind, t.name): t.id for t in CatalogTerm.query.all()}
        for spec in seed_module.CATALOG_DEMO:
            if CatalogResource.query.filter_by(name=spec["name"]).first():
                continue
            fields = dict(spec)
            section_name = fields.pop("section", None)
            rtype = fields["resource_type"]
            complexity, value = seed_module.DEMO_TERMS_BY_TYPE.get(rtype, (None, None))
            resource = CatalogResource(
                **fields, section_id=sections.get(section_name),
                folder_id=folders.get(
                    seed_module.DEMO_FOLDER_BY_TYPE.get((section_name, rtype))),
                complexity_id=term_ids.get(("complexity", complexity)),
                business_value_id=term_ids.get(("business_value", value)),
                status="published", published_at=now,
                reviewed_at=now, next_review_at=now + timedelta(days=180),
                author="Metinvest Digital", created_by=admin.id)
            db.session.add(resource)
            db.session.flush()
            for level in seed_module.DEMO_MATURITY_BY_TYPE.get(rtype, []):
                term_id = term_ids.get(("maturity", level))
                if term_id:
                    db.session.add(CatalogResourceMaturity(resource_id=resource.id,
                                                           term_id=term_id))
        db.session.commit()
        migrate_legacy_tags()

    res = client.post("/api/auth/login",
                      json={"username": "u1", "password": "pass"})
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def _clicks_for_search(client, headers, scenario):
    """Пошук: 1 клік — ввести запит, 2-й — відкрити картку."""
    res = client.get(f"/api/catalog/search?q={scenario['query']}", headers=headers)
    assert res.status_code == 200, res.get_json()
    names = [i["name"] for i in res.get_json()["results"]]
    return names, 2


def _clicks_for_path(client, headers, scenario):
    """Навігація: кожен рівень (розділ, колекція, вид) — окремий клік."""
    path = scenario["path"]
    params, clicks = [], 0

    if "section" in path:
        sections = client.get("/api/catalog/sections", headers=headers).get_json()
        section = next((s for s in sections if s["name"] == path["section"]), None)
        assert section is not None, f"розділу «{path['section']}» немає"
        params.append(f"section_id={section['id']}")
        clicks += 1
    if "folder" in path:
        folders = client.get("/api/catalog/folders", headers=headers).get_json()
        folder = next((f for f in folders if f["name"] == path["folder"]), None)
        assert folder is not None, f"колекції «{path['folder']}» немає"
        params.append(f"folder_id={folder['id']}")
        clicks += 1
    if "kind" in path:
        params.append(f"type={path['kind']}")
        clicks += 1

    res = client.get("/api/catalog/resources?" + "&".join(params), headers=headers)
    assert res.status_code == 200, res.get_json()
    # Останній клік — відкрити саму картку.
    return [i["name"] for i in res.get_json()], clicks + 1


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_scenario_id)
def test_uat_scenario(client, uat_catalog, scenario, _report):
    _report["total"] += 1
    headers = uat_catalog
    limit = scenario.get("max_clicks", DEFAULT_MAX_CLICKS)

    if "query" in scenario:
        names, clicks = _clicks_for_search(client, headers, scenario)
    else:
        names, clicks = _clicks_for_path(client, headers, scenario)

    expected = scenario.get("expect")
    try:
        assert clicks <= limit, (f"потрібно {clicks} кліків, дозволено {limit}")
        if expected is None:
            assert scenario.get("allow_empty"), "сценарій без очікуваного матеріалу"
        else:
            # Матеріал має бути на видноті — у першій трійці, а не десь у хвості.
            assert expected in names[:3], f"знайдено {names[:3]}"
    except AssertionError as exc:
        _report["failures"].append(f"{_scenario_id(scenario)} — {exc}")
        raise
    _report["passed"] += 1


def test_every_brd_persona_has_scenarios():
    """Усі 11 категорій користувачів із BRD описані сценаріями."""
    personas = {s["persona"] for s in SCENARIOS}
    expected = {
        "Новачок в AI", "Новачок у компанії", "Бізнес-користувач",
        "Досвідчений користувач", "Розробник агентів (початковий рівень)",
        "Розробник агентів (просунутий рівень)", "Власник бізнес-процесу",
        "AI Partner", "Власник контенту", "Менеджер бази знань",
        "Власник продукту",
    }
    assert personas == expected, f"бракує: {expected - personas}"
    assert len(personas) == 11


def test_scenarios_file_is_well_formed():
    """Новий сценарій додається рядком у файл даних — перевіряємо формат."""
    for scenario in SCENARIOS:
        assert scenario.get("persona") and scenario.get("goal")
        assert ("query" in scenario) ^ ("path" in scenario), \
            f"{_scenario_id(scenario)}: має бути або query, або path"
        if scenario.get("expect") is None:
            assert scenario.get("allow_empty"), \
                f"{_scenario_id(scenario)}: порожній результат має бути явно дозволений"
