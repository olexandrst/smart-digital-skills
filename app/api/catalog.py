"""Каталог AI Knowledge Hub: розділи, ресурси, обране, пошук та аналітика.

Навички лишаються у /api/skills — тут усе інше наповнення каталогу.
Керування ресурсами й розділами: Admin / Skill Manager. Перегляд — усі.
"""
import os
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_from_directory, Response
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.services import (
    file_service, usage_service, search_service, assistant_service,
)
from app.models import (
    CatalogSection, CatalogFolder, CatalogTerm, CatalogResourceTag,
    CatalogResourceMaturity, HiddenRecommendation, LearningPath, LearningProgress,
    CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog, SearchSynonym,
    UserFile,
    Skill, SkillFeedback, AppSetting, SurveyResponse, SurveyPrompt, ResourceView,
    User,
    RESOURCE_TYPES, LINK_TYPES, BODY_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
    TERM_KINDS, SINGLE_VALUE_TERM_KINDS, MULTI_VALUE_TERM_KINDS, VIEW_TARGETS,
    SURVEY_KINDS, SURVEY_SCALES,
)

bp = Blueprint("catalog", __name__)

FAVORITE_TYPES = {"skill", "resource"}
MAX_BODY = 100_000
MAX_QUERY = 200
# Ключ налаштування «сувора публікація» (BR-05): вимагати обов'язкові поля.
STRICT_PUBLISH_KEY = "catalog_publish_strict"
# Поля, без яких матеріал не публікується при увімкненій суворій публікації.
REQUIRED_ON_PUBLISH = (
    ("description", "короткий опис"),
    ("section_id", "розділ"),
    ("category", "категорія"),
    ("owner", "власник матеріалу"),
    ("next_review_at", "дата наступного перегляду"),
)


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def strict_publish_enabled():
    """Чи ввімкнено вимогу обов'язкових полів при публікації (governance)."""
    return AppSetting.get(STRICT_PUBLISH_KEY, "0") == "1"


def _clean_url(value, required, label="посилання"):
    """Валідує посилання: http(s)://… або внутрішній шлях /…"""
    url = (value or "").strip()
    if not url:
        if required:
            raise ApiError(f"Вкажіть {label}", 400, "validation_error")
        return None
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")
            or url.startswith("/")):
        raise ApiError("Посилання має починатися з http://, https:// або «/» "
                       "(внутрішній ресурс)", 400, "validation_error")
    if len(url) > 2000:
        raise ApiError("Посилання завелике", 400, "validation_error")
    return url


MAX_TAGS = 8


def _tag_names(value):
    """Приймає список або рядок через кому; повертає назви тегів без дублів."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    names, seen = [], set()
    for raw in items:
        # Внутрішні пробіли схлопуємо, щоб «AI  Agent» і «AI Agent» були одним тегом.
        name = " ".join(str(raw).split())
        key = CatalogTerm.normalize(name)
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names[:MAX_TAGS]


def _term_by_name(kind, name):
    """Знаходить термін довідника за назвою без урахування регістру й пробілів."""
    return CatalogTerm.query.filter_by(kind=kind,
                                       name_norm=CatalogTerm.normalize(name)).first()


def _set_tags(res, value):
    """Перезаписує теги матеріалу, створюючи відсутні терміни довідника.

    Користувач може запропонувати новий тег прямо в редакторі — він одразу стає
    значенням довідника, доступним іншим карткам.
    """
    names = _tag_names(value)
    if names is None:
        return
    terms = []
    for name in names:
        term = _term_by_name("tag", name)
        if term is None:
            term = CatalogTerm(kind="tag", name=name,
                               name_norm=CatalogTerm.normalize(name))
            db.session.add(term)
            db.session.flush()
        terms.append(term)

    if res.id is None:
        db.session.flush()  # потрібен id матеріалу для рядків зв'язку
    CatalogResourceTag.query.filter_by(resource_id=res.id).delete(
        synchronize_session=False)
    for term in terms:
        db.session.add(CatalogResourceTag(resource_id=res.id, term_id=term.id))
    # Щоб `res.tag_terms` не віддав кеш попереднього набору в тій самій транзакції.
    db.session.expire(res, ["tag_terms"])


def _set_maturity(res, value):
    """Перезаписує рівні зрілості матеріалу (BR-9). Приймає список id."""
    if value is None:
        return
    ids = value if isinstance(value, (list, tuple)) else [value]
    terms = []
    for raw in ids:
        term_id = _resolve_term("maturity", raw, "рівень зрілості")
        if term_id is not None and term_id not in terms:
            terms.append(term_id)
    if res.id is None:
        db.session.flush()
    CatalogResourceMaturity.query.filter_by(resource_id=res.id).delete(
        synchronize_session=False)
    for term_id in terms:
        db.session.add(CatalogResourceMaturity(resource_id=res.id, term_id=term_id))
    db.session.expire(res, ["maturity_terms"])


def _resolve_term(kind, value, label):
    """Перевіряє, що значення належить довіднику `kind`. Порожнє → None."""
    if value in (None, "", 0):
        return None
    try:
        term_id = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"Некоректне значення поля «{label}»", 400, "validation_error")
    term = CatalogTerm.query.get(term_id)
    if term is None or term.kind != kind:
        raise ApiError(f"Значення поля «{label}» відсутнє в довіднику",
                       400, "validation_error")
    return term.id


def _parse_date(value, field):
    """Дата 'YYYY-MM-DD' або ISO-рядок; порожнє значення → None."""
    text = (value or "").strip() if isinstance(value, str) else value
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        raise ApiError(f"Некоректна дата у полі «{field}» (очікується РРРР-ММ-ДД)",
                       400, "validation_error")


def _resolve_section(section_id):
    if section_id in (None, "", 0):
        return None
    try:
        section_id = int(section_id)
    except (TypeError, ValueError):
        raise ApiError("Некоректний розділ", 400, "validation_error")
    if CatalogSection.query.get(section_id) is None:
        raise ApiError("Розділ не знайдено", 404, "not_found")
    return section_id


def _resolve_folder(folder_id):
    """Повертає колекцію за id (або None), 404 — якщо такої немає."""
    if folder_id in (None, "", 0):
        return None
    try:
        folder_id = int(folder_id)
    except (TypeError, ValueError):
        raise ApiError("Некоректна колекція", 400, "validation_error")
    folder = CatalogFolder.query.get(folder_id)
    if folder is None:
        raise ApiError("Колекцію не знайдено", 404, "not_found")
    return folder


def _apply_placement(item, data):
    """Узгоджує розділ і колекцію матеріалу (BR-03).

    Колекція завжди належить розділу, тому вибір колекції задає й розділ. Якщо
    ж змінили лише розділ, а колекція належала іншому — прив'язка до колекції
    знімається, щоб у картці не лишалося суперечливого шляху навігації.
    """
    if "folder_id" in data:
        folder = _resolve_folder(data.get("folder_id"))
        item.folder_id = folder.id if folder else None
        if folder is not None:
            item.section_id = folder.section_id
            return
    if "section_id" in data:
        item.section_id = _resolve_section(data.get("section_id"))
        if item.folder_id is not None:
            folder = CatalogFolder.query.get(item.folder_id)
            if folder is None or folder.section_id != item.section_id:
                item.folder_id = None


def _apply_fields(res, data, *, creating=False):
    """Переносить поля запиту в ресурс із валідацією (спільне для POST/PATCH)."""
    if creating or "resource_type" in data:
        rtype = (data.get("resource_type") or "").strip()
        if rtype not in RESOURCE_TYPES:
            raise ApiError(
                "Тип ресурсу має бути одним із: " + ", ".join(RESOURCE_TYPES),
                400, "validation_error")
        res.resource_type = rtype

    if creating or "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Назва не може бути порожньою", 400, "validation_error")
        res.name = name

    if creating or "description" in data:
        res.description = (data.get("description") or "").strip()

    for field in ("category", "author", "icon_emoji", "owner", "owner_contact",
                  "tools"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(res, field, value or None)

    if "reuse_guidance" in data:
        guidance = (data.get("reuse_guidance") or "").strip()
        if len(guidance) > MAX_BODY:
            raise ApiError("Сценарій повторного використання завеликий",
                           400, "validation_error")
        res.reuse_guidance = guidance or None

    _apply_placement(res, data)

    if "complexity_id" in data:
        res.complexity_id = _resolve_term("complexity", data.get("complexity_id"),
                                          "складність")
    if "business_value_id" in data:
        res.business_value_id = _resolve_term(
            "business_value", data.get("business_value_id"), "бізнес-цінність")

    if "reuse_level" in data:
        level = (data.get("reuse_level") or "").strip()
        res.reuse_level = level if level in REUSE_LEVELS else None

    for field, label in (("reviewed_at", "Дата перегляду"),
                         ("next_review_at", "Дата наступного перегляду")):
        if field in data:
            setattr(res, field, _parse_date(data.get(field), label))

    if "body" in data:
        body = data.get("body") or ""
        if len(body) > MAX_BODY:
            raise ApiError("Текст завеликий (максимум 100 000 символів)",
                           400, "validation_error")
        res.body = body.strip() or None

    if "accent" in data:
        accent = (data.get("accent") or "").strip()
        res.accent = accent if accent in ACCENTS else None  # None = типовий для виду

    if "is_featured" in data:
        res.is_featured = bool(data.get("is_featured"))

    if "version" in data:
        version = (data.get("version") or "").strip()
        if not version:
            raise ApiError("Версія не може бути порожньою", 400, "validation_error")
        res.version = version

    if "link_scope" in data:
        scope = (data.get("link_scope") or "").strip()
        res.link_scope = scope if scope in LINK_SCOPES else None

    # Посилання обов'язкове для агентів, MCP-серверів і корисних посилань.
    needs_url = res.resource_type in LINK_TYPES
    url_label = "endpoint сервера" if res.resource_type == "mcp" else "посилання"
    if "url" in data or (creating and needs_url):
        res.url = _clean_url(data.get("url"), required=needs_url, label=url_label)
    if needs_url and not res.url:
        raise ApiError(f"Вкажіть {url_label}", 400, "validation_error")
    if needs_url and not res.link_scope:
        res.link_scope = "external"

    # Промпт, інструкція та кейс без тексту — беззмістовні.
    if res.resource_type in BODY_TYPES and creating and not res.body:
        raise ApiError("Додайте текст матеріалу", 400, "validation_error")

    # Теги живуть в окремій таблиці й потребують id матеріалу — їх застосовує
    # виклик `_set_tags` уже після flush (див. create_resource / update_resource).


def _require_publish_ready(res):
    """BR-05: не публікувати матеріал без обов'язкових полів (якщо ввімкнено)."""
    if not strict_publish_enabled():
        return
    missing = [label for field, label in REQUIRED_ON_PUBLISH if not getattr(res, field)]
    if missing:
        raise ApiError("Для публікації заповніть: " + ", ".join(missing),
                       400, "publish_requirements")


def _log_review(item_type, item, from_status, to_status, note=None):
    """Запис у журнал життєвого циклу (FR-08)."""
    user = current_user()
    db.session.add(ReviewLog(
        item_type=item_type, item_id=item.id, item_name=item.name,
        actor_user_id=user.id if user else None,
        actor_name=(user.full_name or user.username) if user else None,
        from_status=from_status, to_status=to_status, note=note,
    ))


# ------------------------------- Розділи -------------------------------

@bp.get("/sections")
@require_auth
def list_sections():
    """Розділи каталогу з кількістю матеріалів у кожному."""
    manager = _is_manager(current_user())
    query = CatalogSection.query
    if not manager:
        query = query.filter_by(is_active=True)
    sections = query.order_by(CatalogSection.position, CatalogSection.id).all()

    statuses = RESOURCE_STATUSES if manager else PUBLIC_STATUSES
    counts = dict(
        db.session.query(CatalogResource.section_id, func.count(CatalogResource.id))
        .filter(CatalogResource.section_id.isnot(None))
        .filter(CatalogResource.status.in_(statuses))
        .group_by(CatalogResource.section_id).all())
    skill_statuses = ("published",) if not manager else ("draft", "published")
    for section_id, n in (db.session.query(Skill.section_id, func.count(Skill.id))
                          .filter(Skill.section_id.isnot(None))
                          .filter(Skill.status.in_(skill_statuses))
                          .group_by(Skill.section_id).all()):
        counts[section_id] = counts.get(section_id, 0) + n
    return jsonify([s.to_dict(counts) for s in sections])


@bp.post("/sections")
@require_global_role("admin", "skill_manager")
def create_section():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Назва розділу не може бути порожньою", 400, "validation_error")
    # Порівняння без урахування регістру в Python (SQLite lower() лишає кирилицю).
    if any(s.name.casefold() == name.casefold() for s in CatalogSection.query.all()):
        raise ApiError("Такий розділ уже існує", 409, "section_exists")
    section = CatalogSection(name=name)
    _apply_section_fields(section, data)
    db.session.add(section)
    db.session.commit()
    return jsonify(section.to_dict()), 201


def _apply_section_fields(section, data):
    for field in ("description", "icon_emoji"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(section, field, value or None)
    if "accent" in data:
        accent = (data.get("accent") or "").strip()
        section.accent = accent if accent in ACCENTS else None
    if "url" in data:
        section.url = _clean_url(data.get("url"), required=False)
    if "position" in data:
        try:
            section.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")
    if "is_active" in data:
        section.is_active = bool(data.get("is_active"))


@bp.patch("/sections/<int:section_id>")
@require_global_role("admin", "skill_manager")
def update_section(section_id):
    section = CatalogSection.query.get_or_404(section_id)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Назва розділу не може бути порожньою",
                           400, "validation_error")
        if any(s.name.casefold() == name.casefold() and s.id != section_id
               for s in CatalogSection.query.all()):
            raise ApiError("Такий розділ уже існує", 409, "section_exists")
        section.name = name
    _apply_section_fields(section, data)
    db.session.commit()
    return jsonify(section.to_dict())


@bp.delete("/sections/<int:section_id>")
@require_global_role("admin", "skill_manager")
def delete_section(section_id):
    """Видаляє розділ. Матеріали зберігаються — у них знімається прив'язка."""
    section = CatalogSection.query.get_or_404(section_id)
    folder_ids = [f.id for f in CatalogFolder.query.filter_by(section_id=section_id)]
    if folder_ids:
        # Колекція не живе без розділу — знімаємо прив'язку й видаляємо її разом
        # із розділом; самі матеріали лишаються в каталозі.
        CatalogResource.query.filter(CatalogResource.folder_id.in_(folder_ids)).update(
            {CatalogResource.folder_id: None}, synchronize_session=False)
        Skill.query.filter(Skill.folder_id.in_(folder_ids)).update(
            {Skill.folder_id: None}, synchronize_session=False)
        CatalogFolder.query.filter(CatalogFolder.id.in_(folder_ids)).delete(
            synchronize_session=False)
    CatalogResource.query.filter_by(section_id=section_id).update(
        {CatalogResource.section_id: None}, synchronize_session=False)
    Skill.query.filter_by(section_id=section_id).update(
        {Skill.section_id: None}, synchronize_session=False)
    db.session.delete(section)
    db.session.commit()
    return jsonify({"message": "Розділ видалено"})


# ------------------------------ Колекції ------------------------------

def _folder_counts(manager):
    """Кількість матеріалів у кожній колекції (з урахуванням видимості статусів)."""
    statuses = RESOURCE_STATUSES if manager else PUBLIC_STATUSES
    counts = dict(
        db.session.query(CatalogResource.folder_id, func.count(CatalogResource.id))
        .filter(CatalogResource.folder_id.isnot(None))
        .filter(CatalogResource.status.in_(statuses))
        .group_by(CatalogResource.folder_id).all())
    skill_statuses = ("draft", "published") if manager else ("published",)
    for folder_id, n in (db.session.query(Skill.folder_id, func.count(Skill.id))
                         .filter(Skill.folder_id.isnot(None))
                         .filter(Skill.status.in_(skill_statuses))
                         .group_by(Skill.folder_id).all()):
        counts[folder_id] = counts.get(folder_id, 0) + n
    return counts


@bp.get("/folders")
@require_auth
def list_folders():
    """Колекції каталогу. `?section_id=` — лише колекції одного розділу."""
    manager = _is_manager(current_user())
    query = CatalogFolder.query
    if not manager:
        query = query.filter_by(is_active=True)
    section_id = request.args.get("section_id")
    if section_id:
        query = query.filter_by(section_id=_resolve_section(section_id))
    folders = query.order_by(CatalogFolder.section_id, CatalogFolder.position,
                             CatalogFolder.id).all()
    return jsonify([f.to_dict(_folder_counts(manager)) for f in folders])


def _folder_name_taken(section_id, name, exclude_id=None):
    """Назва колекції унікальна в межах розділу (порівняння без регістру)."""
    rows = CatalogFolder.query.filter_by(section_id=section_id).all()
    return any(f.name.casefold() == name.casefold() and f.id != exclude_id
               for f in rows)


@bp.post("/folders")
@require_global_role("admin", "skill_manager")
def create_folder():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Назва колекції не може бути порожньою",
                       400, "validation_error")
    section_id = _resolve_section(data.get("section_id"))
    if section_id is None:
        raise ApiError("Колекція має належати розділу — вкажіть розділ",
                       400, "validation_error")
    if _folder_name_taken(section_id, name):
        raise ApiError("Така колекція вже є в цьому розділі", 409, "folder_exists")
    folder = CatalogFolder(name=name, section_id=section_id)
    _apply_folder_fields(folder, data)
    db.session.add(folder)
    db.session.commit()
    return jsonify(folder.to_dict()), 201


def _apply_folder_fields(folder, data):
    for field in ("description", "icon_emoji"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(folder, field, value or None)
    if "position" in data:
        try:
            folder.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")
    if "is_active" in data:
        folder.is_active = bool(data.get("is_active"))


@bp.patch("/folders/<int:folder_id>")
@require_global_role("admin", "skill_manager")
def update_folder(folder_id):
    folder = CatalogFolder.query.get_or_404(folder_id)
    data = request.get_json(silent=True) or {}
    name = folder.name
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Назва колекції не може бути порожньою",
                           400, "validation_error")
    if "section_id" in data:
        section_id = _resolve_section(data.get("section_id"))
        if section_id is None:
            raise ApiError("Колекція має належати розділу — вкажіть розділ",
                           400, "validation_error")
        if section_id != folder.section_id:
            # Колекція переїхала — матеріали в ній переїжджають разом із нею,
            # інакше в картці лишиться розділ, якому колекція вже не належить.
            CatalogResource.query.filter_by(folder_id=folder.id).update(
                {CatalogResource.section_id: section_id}, synchronize_session=False)
            Skill.query.filter_by(folder_id=folder.id).update(
                {Skill.section_id: section_id}, synchronize_session=False)
        folder.section_id = section_id
    if _folder_name_taken(folder.section_id, name, exclude_id=folder.id):
        raise ApiError("Така колекція вже є в цьому розділі", 409, "folder_exists")
    folder.name = name
    _apply_folder_fields(folder, data)
    db.session.commit()
    return jsonify(folder.to_dict())


@bp.delete("/folders/<int:folder_id>")
@require_global_role("admin", "skill_manager")
def delete_folder(folder_id):
    """Видаляє колекцію. Матеріали лишаються в розділі, без колекції."""
    folder = CatalogFolder.query.get_or_404(folder_id)
    CatalogResource.query.filter_by(folder_id=folder_id).update(
        {CatalogResource.folder_id: None}, synchronize_session=False)
    Skill.query.filter_by(folder_id=folder_id).update(
        {Skill.folder_id: None}, synchronize_session=False)
    db.session.delete(folder)
    db.session.commit()
    return jsonify({"message": "Колекцію видалено"})


# ------------------------ Довідники метаданих ------------------------

def _term_counts():
    """Скільки карток посилається на кожен термін (теги + одиничні довідники)."""
    counts = dict(
        db.session.query(CatalogResourceTag.term_id,
                         func.count(CatalogResourceTag.resource_id))
        .group_by(CatalogResourceTag.term_id).all())
    for term_id, n in (db.session.query(CatalogResourceMaturity.term_id,
                                        func.count(CatalogResourceMaturity.resource_id))
                       .group_by(CatalogResourceMaturity.term_id).all()):
        counts[term_id] = counts.get(term_id, 0) + n
    for col in (CatalogResource.complexity_id, CatalogResource.business_value_id):
        for term_id, n in (db.session.query(col, func.count(CatalogResource.id))
                           .filter(col.isnot(None)).group_by(col).all()):
            counts[term_id] = counts.get(term_id, 0) + n
    for rtype, n in (db.session.query(CatalogResource.resource_type,
                                      func.count(CatalogResource.id))
                     .group_by(CatalogResource.resource_type).all()):
        for term in CatalogTerm.query.filter_by(kind="material_type", code=rtype):
            counts[term.id] = counts.get(term.id, 0) + n
    return counts


def _require_term_kind(kind):
    if kind not in TERM_KINDS:
        raise ApiError("Довідник має бути одним із: " + ", ".join(TERM_KINDS),
                       400, "validation_error")
    return kind


@bp.get("/terms")
@require_auth
def list_terms():
    """Значення довідників. `?kind=` — один довідник, без нього — усі."""
    manager = _is_manager(current_user())
    query = CatalogTerm.query
    kind = request.args.get("kind")
    if kind:
        query = query.filter_by(kind=_require_term_kind(kind))
    if not manager:
        query = query.filter_by(is_active=True)
    terms = query.order_by(CatalogTerm.kind, CatalogTerm.position,
                           CatalogTerm.name).all()
    counts = _term_counts()
    return jsonify([t.to_dict(counts) for t in terms])


@bp.post("/terms")
@require_global_role("admin", "skill_manager")
def create_term():
    data = request.get_json(silent=True) or {}
    kind = _require_term_kind((data.get("kind") or "").strip())
    if kind == "material_type":
        # Вид матеріалу задає поведінку (обов'язковість посилання чи тексту),
        # тому нові коди додаються разом із кодом, а не через довідник.
        raise ApiError("Види матеріалів додаються разом із підтримкою в коді — "
                       "тут можна змінити назву, порядок і видимість наявних",
                       400, "material_type_fixed")
    name = " ".join((data.get("name") or "").split())
    if not name:
        raise ApiError("Назва значення не може бути порожньою",
                       400, "validation_error")
    if _term_by_name(kind, name) is not None:
        raise ApiError("Таке значення вже є в довіднику", 409, "term_exists")
    term = CatalogTerm(kind=kind, name=name, name_norm=CatalogTerm.normalize(name))
    _apply_term_fields(term, data)
    db.session.add(term)
    db.session.commit()
    return jsonify(term.to_dict()), 201


def _apply_term_fields(term, data):
    if "description" in data:
        term.description = (data.get("description") or "").strip() or None
    if "position" in data:
        try:
            term.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")
    if "is_active" in data:
        term.is_active = bool(data.get("is_active"))


@bp.patch("/terms/<int:term_id>")
@require_global_role("admin", "skill_manager")
def update_term(term_id):
    """Перейменування та налаштування терміна.

    Картки посилаються на термін за id, тож нова назва одразу видно в усіх
    матеріалах — окремого оновлення карток не потрібно.
    """
    term = CatalogTerm.query.get_or_404(term_id)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = " ".join((data.get("name") or "").split())
        if not name:
            raise ApiError("Назва значення не може бути порожньою",
                           400, "validation_error")
        other = _term_by_name(term.kind, name)
        if other is not None and other.id != term.id:
            raise ApiError("Таке значення вже є в довіднику — об'єднайте їх",
                           409, "term_exists")
        term.name = name
        term.name_norm = CatalogTerm.normalize(name)
    _apply_term_fields(term, data)
    db.session.commit()
    return jsonify(term.to_dict())


@bp.post("/terms/<int:term_id>/merge")
@require_global_role("admin", "skill_manager")
def merge_term(term_id):
    """Зливає термін в інший того ж довідника: посилання переносяться, термін зникає."""
    source = CatalogTerm.query.get_or_404(term_id)
    data = request.get_json(silent=True) or {}
    target = CatalogTerm.query.get(data.get("into") or 0)
    if target is None or target.kind != source.kind:
        raise ApiError("Вкажіть значення того самого довідника, у яке зливати",
                       400, "validation_error")
    if target.id == source.id:
        raise ApiError("Не можна злити значення саме в себе", 400, "validation_error")
    if source.kind == "material_type":
        raise ApiError("Види матеріалів не зливаються", 400, "material_type_fixed")

    if source.kind in MULTI_VALUE_TERM_KINDS:
        link = (CatalogResourceTag if source.kind == "tag"
                else CatalogResourceMaturity)
        # Картки, що вже мають цільове значення, інакше отримали б дубль зв'язку.
        taken = {row.resource_id for row in link.query.filter_by(term_id=target.id)}
        for row in link.query.filter_by(term_id=source.id).all():
            if row.resource_id in taken:
                db.session.delete(row)
            else:
                row.term_id = target.id
    else:
        for col in (CatalogResource.complexity_id, CatalogResource.business_value_id):
            CatalogResource.query.filter(col == source.id).update(
                {col: target.id}, synchronize_session=False)
    db.session.delete(source)
    db.session.commit()
    return jsonify({"message": f"Значення злито в «{target.name}»",
                    "term": target.to_dict()})


@bp.delete("/terms/<int:term_id>")
@require_global_role("admin", "skill_manager")
def delete_term(term_id):
    """Видаляє значення довідника. Посилання на нього в картках знімаються."""
    term = CatalogTerm.query.get_or_404(term_id)
    if term.kind == "material_type":
        raise ApiError("Вид матеріалу не видаляється — його можна приховати",
                       400, "material_type_fixed")
    if term.kind in MULTI_VALUE_TERM_KINDS:
        link = CatalogResourceTag if term.kind == "tag" else CatalogResourceMaturity
        link.query.filter_by(term_id=term_id).delete(synchronize_session=False)
    else:
        for col in (CatalogResource.complexity_id, CatalogResource.business_value_id):
            CatalogResource.query.filter(col == term_id).update(
                {col: None}, synchronize_session=False)
    db.session.delete(term)
    db.session.commit()
    return jsonify({"message": "Значення видалено"})


# ------------------------------- Ресурси -------------------------------

@bp.get("/resources")
@require_auth
def list_resources():
    """Перелік ресурсів із фільтрами за атрибутами картки (BR-06, FR-06).

    Параметри — усі необов'язкові й комбінуються між собою:
    `type`, `section_id`, `folder_id`, `status`, `complexity_id`,
    `business_value_id`, `reuse_level`, `tag_id`, `tool`, `owner`, `q`.

    Admin/Skill Manager бачать усе; решта — опубліковані та ті, що потребують
    оновлення (чернетки й архів приховані).
    """
    manager = _is_manager(current_user())
    query = CatalogResource.query

    rtype = request.args.get("type")
    if rtype:
        if rtype not in RESOURCE_TYPES:
            raise ApiError("Невідомий тип ресурсу", 400, "validation_error")
        query = query.filter_by(resource_type=rtype)

    section_id = request.args.get("section_id")
    if section_id:
        query = query.filter_by(section_id=_resolve_section(section_id))

    folder_id = request.args.get("folder_id")
    if folder_id:
        query = query.filter_by(folder_id=_resolve_folder(folder_id).id)

    for param, kind, label in (("complexity_id", "complexity", "складність"),
                               ("business_value_id", "business_value",
                                "бізнес-цінність")):
        value = request.args.get(param)
        if value:
            query = query.filter(getattr(CatalogResource, param)
                                 == _resolve_term(kind, value, label))

    reuse_level = request.args.get("reuse_level")
    if reuse_level:
        if reuse_level not in REUSE_LEVELS:
            raise ApiError("Рівень повторного використання має бути одним із: "
                           + ", ".join(REUSE_LEVELS), 400, "validation_error")
        query = query.filter_by(reuse_level=reuse_level)

    tag_id = request.args.get("tag_id")
    if tag_id:
        term_id = _resolve_term("tag", tag_id, "тег")
        query = query.filter(CatalogResource.id.in_(
            db.session.query(CatalogResourceTag.resource_id)
            .filter_by(term_id=term_id)))

    maturity_id = request.args.get("maturity_id")
    if maturity_id:
        term_id = _resolve_term("maturity", maturity_id, "рівень зрілості")
        query = query.filter(CatalogResource.id.in_(
            db.session.query(CatalogResourceMaturity.resource_id)
            .filter_by(term_id=term_id)))

    # Інструмент і власник — вільний текст у картці, тому збіг за підрядком.
    for param, column in (("tool", CatalogResource.tools),
                          ("owner", CatalogResource.owner)):
        value = (request.args.get(param) or "").strip()
        if value:
            query = query.filter(column.ilike(f"%{value}%"))

    text = (request.args.get("q") or "").strip()
    if text:
        like = f"%{text}%"
        query = query.filter(db.or_(CatalogResource.name.ilike(like),
                                    CatalogResource.description.ilike(like),
                                    CatalogResource.category.ilike(like),
                                    CatalogResource.author.ilike(like),
                                    CatalogResource.owner.ilike(like)))

    status = request.args.get("status")
    if status:
        if status not in RESOURCE_STATUSES:
            raise ApiError("Статус має бути одним із: " + ", ".join(RESOURCE_STATUSES),
                           400, "validation_error")
        if not manager and status not in PUBLIC_STATUSES:
            raise ApiError("Матеріали цього статусу недоступні", 403, "forbidden")
        query = query.filter_by(status=status)
    if not manager:
        query = query.filter(CatalogResource.status.in_(PUBLIC_STATUSES))

    items = query.order_by(CatalogResource.is_featured.desc(),
                           CatalogResource.id.desc()).all()
    ratings = _rating_map("resource")
    return jsonify([dict(r.to_dict(), **ratings.get(r.id, _EMPTY_RATING))
                    for r in items])


SHOWCASE_LIMIT = 6


@bp.get("/showcase")
@require_auth
def showcase():
    """Блоки головної сторінки: стан бази знань, а не лише перелік розділів (BR-01).

    Порожні блоки не повертаються взагалі — фронт не має вирішувати, чи
    малювати рамку без вмісту.
    """
    manager = _is_manager(current_user())
    ratings = _rating_map("resource")

    def pack(items):
        return [dict(r.to_dict(), **ratings.get(r.id, _EMPTY_RATING)) for r in items]

    public = CatalogResource.query.filter(
        CatalogResource.status.in_(PUBLIC_STATUSES))

    featured = (public.filter_by(is_featured=True)
                .order_by(CatalogResource.updated_at.desc())
                .limit(SHOWCASE_LIMIT).all())
    recent = (public.order_by(CatalogResource.published_at.desc().nullslast(),
                              CatalogResource.id.desc())
              .limit(SHOWCASE_LIMIT).all())
    popular = (public.filter(CatalogResource.opens_count > 0)
               .order_by(CatalogResource.opens_count.desc())
               .limit(SHOWCASE_LIMIT).all())

    blocks = [
        {"key": "featured", "title": "Рекомендовані рішення",
         "hint": "Відібрані командою хабу", "items": pack(featured)},
        {"key": "recent", "title": "Нещодавно додані",
         "hint": "Що з'явилося останнім", "items": pack(recent)},
        {"key": "popular", "title": "Найчастіше повторно використовувані",
         "hint": "За кількістю відкриттів і копіювань", "items": pack(popular)},
    ]

    # Стан контенту — робота власників і менеджерів, а не вітрина для всіх.
    if manager:
        now = datetime.utcnow()
        stale = (CatalogResource.query
                 .filter(db.or_(CatalogResource.status == "needs_update",
                                db.and_(CatalogResource.next_review_at.isnot(None),
                                        CatalogResource.next_review_at < now,
                                        CatalogResource.status.in_(PUBLIC_STATUSES))))
                 .order_by(CatalogResource.next_review_at.asc().nullslast())
                 .limit(SHOWCASE_LIMIT).all())
        blocks.append({"key": "needs_update", "title": "Потребують оновлення",
                       "hint": "Прострочений перегляд або позначка «потребує оновлення»",
                       "manager_only": True, "items": pack(stale)})

    return jsonify([b for b in blocks if b["items"]])


_EMPTY_RATING = {"rating_avg": None, "rating_count": 0}


def _rating_map(item_type):
    """Середній рейтинг і кількість оцінок за матеріалами одного типу."""
    id_col = SkillFeedback.resource_id if item_type == "resource" else SkillFeedback.skill_id
    rows = (db.session.query(id_col, func.avg(SkillFeedback.rating),
                             func.count(SkillFeedback.rating))
            .filter(id_col.isnot(None), SkillFeedback.rating.isnot(None))
            .group_by(id_col).all())
    return {row[0]: {"rating_avg": round(float(row[1]), 1), "rating_count": int(row[2])}
            for row in rows}


@bp.get("/resources/<int:resource_id>")
@require_auth
def get_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    ratings = _rating_map("resource")
    return jsonify(dict(res.to_dict(), **ratings.get(res.id, _EMPTY_RATING)))


@bp.post("/resources")
@require_global_role("admin", "skill_manager")
def create_resource():
    data = request.get_json(silent=True) or {}
    user = current_user()
    res = CatalogResource(created_by=user.id)
    res.author = user.full_name or user.username
    _apply_fields(res, data, creating=True)
    publish = (data.get("status") or "draft") == "published"
    if publish:
        _require_publish_ready(res)
        res.status = "published"
        res.published_at = datetime.utcnow()
    db.session.add(res)
    db.session.flush()
    _set_tags(res, data.get("tags"))
    _set_maturity(res, data.get("maturity_ids"))
    if publish:
        _log_review("resource", res, None, "published")
    db.session.commit()
    return jsonify(res.to_dict()), 201


@bp.patch("/resources/<int:resource_id>")
@require_global_role("admin", "skill_manager")
def update_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    data = request.get_json(silent=True) or {}
    _apply_fields(res, data)
    if "tags" in data:
        _set_tags(res, data.get("tags"))
    if "maturity_ids" in data:
        _set_maturity(res, data.get("maturity_ids"))
    db.session.commit()
    return jsonify(res.to_dict())


@bp.post("/resources/<int:resource_id>/status")
@require_global_role("admin", "skill_manager")
def change_resource_status(resource_id):
    """Перехід життєвим циклом: draft → published → needs_update → archived."""
    res = CatalogResource.query.get_or_404(resource_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in RESOURCE_STATUSES:
        raise ApiError("Статус має бути одним із: " + ", ".join(RESOURCE_STATUSES),
                       400, "validation_error")
    if new_status == "published":
        _require_publish_ready(res)

    old_status = res.status
    res.status = new_status
    if new_status == "published":
        if res.published_at is None:
            res.published_at = datetime.utcnow()
        res.reviewed_at = datetime.utcnow()
    if old_status != new_status:
        _log_review("resource", res, old_status, new_status,
                    (data.get("note") or "").strip() or None)
    db.session.commit()
    return jsonify(res.to_dict())


@bp.get("/resources/<int:resource_id>/review-log")
@require_global_role("admin", "skill_manager")
def resource_review_log(resource_id):
    CatalogResource.query.get_or_404(resource_id)
    rows = (ReviewLog.query.filter_by(item_type="resource", item_id=resource_id)
            .order_by(ReviewLog.created_at.desc()).all())
    return jsonify([r.to_dict() for r in rows])


@bp.post("/resources/<int:resource_id>/open")
@require_auth
def register_open(resource_id):
    """Лічильник відкриттів плюс запис перегляду для аналітики (FR-11).

    `opens_count` лишається як був — він живить сортування «за популярністю»;
    `resource_views` додає розрізи за користувачами й періодами, яких лічильник
    дати не може.
    """
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if res.status not in PUBLIC_STATUSES and not _is_manager(user):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    res.opens_count = (res.opens_count or 0) + 1
    usage_service.record_view(user, "resource", res.id, res.name)
    db.session.commit()
    return jsonify({"opens_count": res.opens_count})


@bp.post("/views")
@require_auth
def register_view():
    """Перегляд розділу, колекції чи навички — те, що не має свого лічильника.

    Аналітика не має заважати роботі, тому помилка запису не валить запит:
    фронт викликає цей ендпоінт «у фоні» й ігнорує відповідь.
    """
    data = request.get_json(silent=True) or {}
    target_type = (data.get("target_type") or "").strip()
    if target_type not in VIEW_TARGETS:
        raise ApiError("target_type має бути одним із: " + ", ".join(VIEW_TARGETS),
                       400, "validation_error")
    try:
        target_id = int(data.get("target_id"))
    except (TypeError, ValueError):
        raise ApiError("Вкажіть target_id", 400, "validation_error")

    name = (data.get("target_name") or "").strip() or None
    if target_type == "section":
        section = CatalogSection.query.get(target_id)
        if section is None:
            raise ApiError("Розділ не знайдено", 404, "not_found")
        name = section.name
    elif target_type == "folder":
        folder = CatalogFolder.query.get(target_id)
        if folder is None:
            raise ApiError("Колекцію не знайдено", 404, "not_found")
        name = folder.name

    usage_service.record_view(current_user(), target_type, target_id, name)
    db.session.commit()
    return jsonify({"ok": True}), 201


# --------------------- Вкладення до картки (FR-03) ---------------------

def _visible_resource(resource_id):
    """Картка, яку поточний користувач має право бачити."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    return res


def _may_attach(res, user):
    """Наповнювати картку файлами може її автор або менеджер каталогу.

    Поле `owner` — вільний текст (прізвище відповідального), а не посилання на
    користувача, тому правами воно керувати не може: звірятися з ним означало б
    видавати доступ за збігом рядків.
    """
    return _is_manager(user) or res.created_by == user.id


@bp.get("/resources/<int:resource_id>/files")
@require_auth
def list_resource_files(resource_id):
    """Вкладення картки — видно всім, хто бачить саму картку."""
    _visible_resource(resource_id)
    rows = (UserFile.query.filter_by(resource_id=resource_id)
            .order_by(UserFile.created_at.desc()).all())
    return jsonify([f.to_dict() for f in rows])


@bp.post("/resources/<int:resource_id>/files")
@require_auth
def upload_resource_file(resource_id):
    res = _visible_resource(resource_id)
    user = current_user()
    if not _may_attach(res, user):
        raise ApiError("Додавати файли до цієї картки може її автор або менеджер",
                       403, "forbidden")
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    uf = file_service.save_bytes(user, file.filename, file.read(),
                                 source="attachment", resource_id=res.id)
    return jsonify(uf.to_dict()), 201


@bp.get("/resources/<int:resource_id>/files/<int:file_id>/download")
@require_auth
def download_resource_file(resource_id, file_id):
    """Звантаження вкладення — для всіх, хто бачить картку, а не лише для власника."""
    _visible_resource(resource_id)
    uf = UserFile.query.filter_by(id=file_id, resource_id=resource_id).first()
    if uf is None:
        raise ApiError("Файл не знайдено", 404, "not_found")
    return send_from_directory(
        file_service.user_dir(uf.user), uf.stored_name, as_attachment=True,
        download_name=os.path.basename(uf.filename) or uf.stored_name)


@bp.delete("/resources/<int:resource_id>/files/<int:file_id>")
@require_auth
def delete_resource_file(resource_id, file_id):
    res = _visible_resource(resource_id)
    user = current_user()
    uf = UserFile.query.filter_by(id=file_id, resource_id=resource_id).first()
    if uf is None:
        raise ApiError("Файл не знайдено", 404, "not_found")
    if not (_may_attach(res, user) or uf.user_id == user.id):
        raise ApiError("Видалити вкладення може той, хто його додав, "
                       "автор картки або менеджер", 403, "forbidden")
    file_service.remove_stored_file(uf)
    db.session.delete(uf)
    db.session.commit()
    return jsonify({"message": "Вкладення видалено"})


@bp.delete("/resources/<int:resource_id>")
@require_global_role("admin", "skill_manager")
def delete_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    # Вкладення живуть лише разом із карткою — інакше у сховищі лишаються
    # файли, до яких більше немає жодного шляху з інтерфейсу.
    for uf in UserFile.query.filter_by(resource_id=resource_id).all():
        file_service.remove_stored_file(uf)
        db.session.delete(uf)
    CatalogResourceTag.query.filter_by(resource_id=resource_id)\
        .delete(synchronize_session=False)
    CatalogResourceMaturity.query.filter_by(resource_id=resource_id)\
        .delete(synchronize_session=False)
    CatalogFavorite.query.filter_by(item_type="resource", item_id=resource_id)\
        .delete(synchronize_session=False)
    SkillFeedback.query.filter_by(resource_id=resource_id).update(
        {SkillFeedback.resource_id: None}, synchronize_session=False)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Ресурс видалено"})


# -------------------------- Зворотний зв'язок --------------------------

def _clean_rating(value):
    if value in (None, ""):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    if not 1 <= rating <= 5:
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    return rating


@bp.post("/resources/<int:resource_id>/feedback")
@require_auth
def submit_resource_feedback(resource_id):
    """Відгук та/або оцінка матеріалу каталогу (BR-13)."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    rating = _clean_rating(data.get("rating"))
    if not message and rating is None:
        raise ApiError("Залиште оцінку або повідомлення", 400, "validation_error")
    if len(message) > 5000:
        raise ApiError("Повідомлення завелике (максимум 5000 символів)",
                       400, "validation_error")
    user = current_user()
    fb = SkillFeedback(
        item_type="resource", resource_id=res.id, skill_name=res.name,
        skill_version=res.version, rating=rating,
        user_id=user.id, username=user.full_name or user.username,
        message=message or "(без коментаря)",
    )
    db.session.add(fb)
    db.session.commit()
    return jsonify({"message": "Дякуємо! Відгук надіслано.", "id": fb.id}), 201


# -------------------------------- Обране --------------------------------

@bp.get("/favorites")
@require_auth
def list_favorites():
    """Обране поточного користувача — окремо навички й ресурси."""
    rows = CatalogFavorite.query.filter_by(user_id=current_user().id).all()
    return jsonify({
        "skill": [r.item_id for r in rows if r.item_type == "skill"],
        "resource": [r.item_id for r in rows if r.item_type == "resource"],
    })


@bp.post("/favorites")
@require_auth
def toggle_favorite():
    """Перемикає «зірочку» для навички або ресурсу каталогу."""
    data = request.get_json(silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    item_id = data.get("item_id")
    if item_type not in FAVORITE_TYPES or not isinstance(item_id, int):
        raise ApiError("Вкажіть item_type ('skill' або 'resource') та item_id",
                       400, "validation_error")
    model = Skill if item_type == "skill" else CatalogResource
    if model.query.get(item_id) is None:
        raise ApiError("Елемент каталогу не знайдено", 404, "not_found")

    user_id = current_user().id
    row = CatalogFavorite.query.filter_by(
        user_id=user_id, item_type=item_type, item_id=item_id).first()
    if row is not None:
        db.session.delete(row)
        favorited = False
    else:
        db.session.add(CatalogFavorite(user_id=user_id, item_type=item_type,
                                       item_id=item_id))
        favorited = True
    db.session.commit()
    return jsonify({"item_type": item_type, "item_id": item_id,
                    "favorited": favorited})


# ---------------------------- Пошукові запити ----------------------------

@bp.post("/search-log")
@require_auth
def log_search():
    """Реєструє пошуковий запит користувача (BR-08, FR-09).

    Повертає id запису — фронт передає його у /search-log/<id>/opened, якщо
    користувач після пошуку відкрив картку (це і є Search Success Rate).
    """
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        raise ApiError("Порожній запит", 400, "validation_error")
    row = SearchQueryLog(
        query_text=query[:MAX_QUERY],
        query_norm=query[:MAX_QUERY].casefold(),
        results_count=int(data.get("results_count") or 0),
        section_id=_resolve_section(data.get("section_id")),
        kind=(data.get("kind") or None),
        user_id=current_user().id,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201


@bp.post("/search-log/<int:log_id>/opened")
@require_auth
def log_search_opened(log_id):
    """Позначає, що після цього запиту користувач відкрив матеріал."""
    row = SearchQueryLog.query.get_or_404(log_id)
    if row.user_id != current_user().id:
        raise ApiError("Чужий запис пошуку", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    if item_type not in FAVORITE_TYPES:
        raise ApiError("Вкажіть item_type ('skill' або 'resource')",
                       400, "validation_error")
    row.opened_item_type = item_type
    row.opened_item_id = data.get("item_id")
    db.session.commit()
    return jsonify({"id": row.id, "opened_item_type": row.opened_item_type})


# ----------------------------- Аналітика хабу -----------------------------

@bp.get("/settings")
@require_auth
def catalog_settings():
    """Налаштування каталогу для UI (разом із частотою опитувань)."""
    out = {"strict_publish": strict_publish_enabled()}
    out.update({key: _survey_setting(key) for key in SURVEY_SETTINGS})
    return jsonify(out)


@bp.post("/settings")
@require_global_role("admin", "skill_manager")
def set_catalog_settings():
    """Зміна налаштувань. Частота опитувань керується звідси, а не з коду."""
    data = request.get_json(silent=True) or {}
    known = {"strict_publish", *SURVEY_SETTINGS}
    if not known & set(data):
        raise ApiError("Вкажіть щонайменше одне налаштування: "
                       + ", ".join(sorted(known)), 400, "validation_error")

    if "strict_publish" in data:
        AppSetting.set(STRICT_PUBLISH_KEY, "1" if data["strict_publish"] else "0")
    for key in SURVEY_SETTINGS:
        if key not in data:
            continue
        value = data[key]
        if key == "survey_enabled":
            AppSetting.set(key, "1" if value else "0")
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ApiError(f"Налаштування «{key}» має бути числом",
                           400, "validation_error")
        if number < 0:
            raise ApiError(f"Налаштування «{key}» не може бути від'ємним",
                           400, "validation_error")
        AppSetting.set(key, str(number))

    db.session.commit()
    return jsonify(catalog_settings().get_json())


@bp.get("/analytics")
@require_global_role("admin", "skill_manager")
def analytics():
    """Базова аналітика наповнення хабу (BR-16, FR-11).

    Свідомо містить лише метрики, які чесно рахуються з наявних даних.
    MAU/DAU/NPS потребують посесійного трекінгу переглядів — його немає.
    """
    resources = CatalogResource.query.all()
    skills = Skill.query.all()
    now = datetime.utcnow()

    by_type = {t: 0 for t in RESOURCE_TYPES}
    by_status = {s: 0 for s in RESOURCE_STATUSES}
    for r in resources:
        by_type[r.resource_type] = by_type.get(r.resource_type, 0) + 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
    by_type["skill"] = len(skills)

    sections = CatalogSection.query.order_by(CatalogSection.position,
                                             CatalogSection.id).all()
    by_section = []
    for s in sections:
        by_section.append({
            "id": s.id, "name": s.name,
            "resources": sum(1 for r in resources if r.section_id == s.id),
            "skills": sum(1 for k in skills if k.section_id == s.id),
        })
    unassigned = (sum(1 for r in resources if not r.section_id)
                  + sum(1 for k in skills if not k.section_id))

    published = [r for r in resources if r.status in PUBLIC_STATUSES]
    freshness = {
        "total": len(resources),
        "published": by_status.get("published", 0),
        "needs_update": by_status.get("needs_update", 0),
        "draft": by_status.get("draft", 0),
        "archived": by_status.get("archived", 0),
        "no_owner": sum(1 for r in published if not r.owner),
        "no_section": sum(1 for r in published if not r.section_id),
        "no_tags": sum(1 for r in published if not r.tag_terms),
        "review_overdue": sum(1 for r in published
                              if r.next_review_at and r.next_review_at < now),
    }

    top_opened = sorted(resources, key=lambda r: r.opens_count or 0, reverse=True)[:10]
    top_items = [{"id": r.id, "name": r.name, "resource_type": r.resource_type,
                  "opens_count": r.opens_count or 0} for r in top_opened
                 if (r.opens_count or 0) > 0]

    # Пошукові запити: найпопулярніші та ті, що не дали результатів.
    def _queries(no_results):
        q = (db.session.query(SearchQueryLog.query_norm,
                              func.count(SearchQueryLog.id),
                              func.max(SearchQueryLog.query_text))
             .group_by(SearchQueryLog.query_norm))
        q = (q.having(func.max(SearchQueryLog.results_count) == 0) if no_results
             else q.having(func.max(SearchQueryLog.results_count) > 0))
        rows = q.order_by(func.count(SearchQueryLog.id).desc()).limit(10).all()
        return [{"query": row[2], "count": int(row[1])} for row in rows]

    total_searches = SearchQueryLog.query.count()
    successful = SearchQueryLog.query.filter(
        SearchQueryLog.opened_item_type.isnot(None)).count()
    zero_results = SearchQueryLog.query.filter_by(results_count=0).count()

    rating_row = (db.session.query(func.avg(SkillFeedback.rating),
                                   func.count(SkillFeedback.rating))
                  .filter(SkillFeedback.rating.isnot(None)).one())

    return jsonify({
        "by_type": by_type,
        "by_section": by_section,
        "unassigned": unassigned,
        "freshness": freshness,
        "top_opened": top_items,
        "search": {
            "total": total_searches,
            "success_rate": round(successful / total_searches * 100, 1) if total_searches else None,
            "no_results_rate": round(zero_results / total_searches * 100, 1) if total_searches else None,
            "top": _queries(no_results=False),
            "no_results": _queries(no_results=True),
        },
        "rating": {
            "avg": round(float(rating_row[0]), 2) if rating_row[0] is not None else None,
            "count": int(rating_row[1]),
        },
        "feedback_unread": SkillFeedback.query.filter_by(is_read=False).count(),
        "strict_publish": strict_publish_enabled(),
    })


# --------------------------- Дашборд KPI (BR-16) ---------------------------

# Дозволені періоди дашборда: скільки днів назад від сьогодні.
KPI_PERIODS = {"7d": 7, "30d": 30, "90d": 90}
DEFAULT_KPI_PERIOD = "30d"


def _kpi_range(period, now=None):
    """Поточний і попередній проміжки однакової довжини — для порівняння."""
    now = now or datetime.utcnow()
    days = KPI_PERIODS[period]
    today = now.date()
    current_from = today - timedelta(days=days - 1)
    previous_to = current_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=days - 1)
    return current_from, today, previous_from, previous_to


def _delta(current, previous):
    """Напрям і величина зміни. None, якщо порівнювати нема з чим."""
    if previous in (None, 0):
        return {"value": current, "previous": previous,
                "change_pct": None if not previous else 0.0}
    change = (current - previous) / previous * 100
    return {"value": current, "previous": previous, "change_pct": round(change, 1)}


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else None


@bp.get("/kpi")
@require_global_role("admin", "skill_manager")
def kpi():
    """Показники розділу 11 BRD за період із порівнянням до попереднього.

    `?period=7d|30d|90d`. Кожне число рахується запитом до бази; перебору
    історії в Python немає, тому дашборд не «важчає» з накопиченням переглядів.
    """
    period = request.args.get("period", DEFAULT_KPI_PERIOD)
    if period not in KPI_PERIODS:
        raise ApiError("Період має бути одним із: " + ", ".join(KPI_PERIODS),
                       400, "validation_error")
    # Висячі сесії закриваємо перед підрахунком, інакше тривалість завищена.
    usage_service.close_stale_sessions()
    now = datetime.utcnow()
    cur_from, cur_to, prev_from, prev_to = _kpi_range(period, now)
    today = now.date()

    dau = usage_service.active_users(today)
    wau = usage_service.active_users(today - timedelta(days=6))
    mau = usage_service.active_users(today - timedelta(days=29))

    views_now = usage_service.views_count(cur_from, cur_to)
    views_prev = usage_service.views_count(prev_from, prev_to)
    users_now = usage_service.active_users(cur_from, cur_to)
    users_prev = usage_service.active_users(prev_from, prev_to)

    sessions_now = usage_service.session_metrics(cur_from, cur_to)
    sessions_prev = usage_service.session_metrics(prev_from, prev_to)

    resources = CatalogResource.query.all()
    published = [r for r in resources if r.status in PUBLIC_STATUSES]
    by_type = {t: 0 for t in RESOURCE_TYPES}
    for r in resources:
        by_type[r.resource_type] = by_type.get(r.resource_type, 0) + 1
    by_type["skill"] = Skill.query.count()
    total_items = sum(by_type.values())

    sections = CatalogSection.query.order_by(CatalogSection.position,
                                             CatalogSection.id).all()
    skills = Skill.query.all()
    by_section = [{
        "id": s.id, "name": s.name,
        "items": sum(1 for r in resources if r.section_id == s.id)
                 + sum(1 for k in skills if k.section_id == s.id),
    } for s in sections]
    for row in by_section:
        row["share_pct"] = _pct(row["items"], total_items)

    def _search_stats(since, until):
        total = SearchQueryLog.query.filter(
            func.date(SearchQueryLog.created_at) >= since,
            func.date(SearchQueryLog.created_at) <= until).count()
        opened = SearchQueryLog.query.filter(
            func.date(SearchQueryLog.created_at) >= since,
            func.date(SearchQueryLog.created_at) <= until,
            SearchQueryLog.opened_item_type.isnot(None)).count()
        empty = SearchQueryLog.query.filter(
            func.date(SearchQueryLog.created_at) >= since,
            func.date(SearchQueryLog.created_at) <= until,
            SearchQueryLog.results_count == 0).count()
        return {"total": total, "success_pct": _pct(opened, total),
                "no_results_pct": _pct(empty, total)}

    search_now = _search_stats(cur_from, cur_to)
    search_prev = _search_stats(prev_from, prev_to)

    # «Повторні використання» — відкриття матеріалів, позначених як придатні до
    # повторного використання: саме вони означають, що рішення пішло далі.
    reusable_ids = [r.id for r in resources if r.reuse_level in ("ready", "adaptable")]
    reuse_views = 0
    if reusable_ids:
        reuse_views = int(db.session.query(func.count(ResourceView.id))
                          .filter(ResourceView.target_type == "resource",
                                  ResourceView.target_id.in_(reusable_ids),
                                  ResourceView.day >= cur_from,
                                  ResourceView.day <= cur_to).scalar() or 0)

    survey = _survey_summary(cur_from, cur_to)

    return jsonify({
        "period": period,
        "range": {"from": cur_from.isoformat(), "to": cur_to.isoformat(),
                  "previous_from": prev_from.isoformat(),
                  "previous_to": prev_to.isoformat()},
        "audience": {
            "dau": dau, "wau": wau, "mau": mau,
            # Липкість: яка частка місячної аудиторії заходить щодня.
            "stickiness_pct": _pct(dau, mau),
            "active_users": _delta(users_now, users_prev),
            "churn": {
                "gap_30": usage_service.returning_gap(30, now),
                "gap_60": usage_service.returning_gap(60, now),
                "gap_90": usage_service.returning_gap(90, now),
            },
        },
        "engagement": {
            "views": _delta(views_now, views_prev),
            "views_per_item": round(views_now / total_items, 1) if total_items else None,
            "avg_session_seconds": _delta(sessions_now["avg_duration_seconds"] or 0,
                                          sessions_prev["avg_duration_seconds"] or 0),
            "avg_depth": sessions_now["avg_depth"],
            "sessions": _delta(sessions_now["sessions"], sessions_prev["sessions"]),
            "reuse_views": reuse_views,
            "top_viewed": usage_service.top_viewed(cur_from, cur_to),
            "daily": usage_service.daily_series(cur_from, cur_to),
        },
        "content": {
            "total": total_items,
            "by_type": by_type,
            "by_section": by_section,
            "published_pct": _pct(len(published), len(resources)),
            "needs_update_pct": _pct(sum(1 for r in resources
                                         if r.status == "needs_update"), len(resources)),
        },
        "search": {
            "total": _delta(search_now["total"], search_prev["total"]),
            "success_pct": search_now["success_pct"],
            "success_pct_previous": search_prev["success_pct"],
            "no_results_pct": search_now["no_results_pct"],
            "no_results_pct_previous": search_prev["no_results_pct"],
        },
        "survey": survey,
    })


@bp.get("/kpi/export")
@require_global_role("admin", "skill_manager")
def kpi_export():
    """Ті самі числа, що й на екрані, у вигляді CSV.

    Формується з відповіді `kpi()`, а не окремими запитами: інакше вивантаження
    з часом розійшлося б із дашбордом.
    """
    import csv
    import io as _io

    # Той самий обробник, той самий `request` — тому числа збігаються з екраном
    # за побудовою, а не за домовленістю.
    data = kpi().get_json()

    buf = _io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Показник", "Значення", "Попередній період", "Зміна, %"])

    def row(label, block, key=None):
        if key is None:
            writer.writerow([label, block, "", ""])
            return
        cell = block.get(key)
        if isinstance(cell, dict):
            writer.writerow([label, cell.get("value"), cell.get("previous"),
                             cell.get("change_pct")])
        else:
            writer.writerow([label, cell, "", ""])

    a, e, c, s = data["audience"], data["engagement"], data["content"], data["search"]
    writer.writerow([f"Період: {data['range']['from']} — {data['range']['to']}", "", "", ""])
    row("Унікальні користувачі за день", a["dau"])
    row("Унікальні користувачі за тиждень", a["wau"])
    row("Унікальні користувачі за місяць", a["mau"])
    row("Липкість DAU/MAU, %", a["stickiness_pct"])
    row("Активні користувачі за період", a, "active_users")
    row("Не поверталися понад 30 днів", a["churn"]["gap_30"])
    row("Не поверталися понад 60 днів", a["churn"]["gap_60"])
    row("Не поверталися понад 90 днів", a["churn"]["gap_90"])
    row("Відкриттів карток", e, "views")
    row("Переглядів на матеріал", e["views_per_item"])
    row("Середня тривалість сесії, с", e, "avg_session_seconds")
    row("Глибина перегляду за сесію", e["avg_depth"])
    row("Сесій", e, "sessions")
    row("Відкриттів матеріалів для повторного використання", e["reuse_views"])
    row("Усього матеріалів", c["total"])
    row("Опубліковано, %", c["published_pct"])
    row("Потребують оновлення, %", c["needs_update_pct"])
    row("Пошукових запитів", s, "total")
    row("Запитів із відкриттям матеріалу, %", s["success_pct"])
    row("Запитів без результатів, %", s["no_results_pct"])
    if data["survey"]["nps"]["responses"]:
        row("NPS", data["survey"]["nps"]["score"])
    if data["survey"]["csat"]["responses"]:
        row("CSAT (середня)", data["survey"]["csat"]["avg"])
    if data["survey"]["ces"]["responses"]:
        row("CES (середня)", data["survey"]["ces"]["avg"])

    writer.writerow([])
    writer.writerow(["Матеріал", "Переглядів", "", ""])
    for item in e["top_viewed"]:
        writer.writerow([item["name"] or f"#{item['target_id']}", item["views"], "", ""])

    # BOM — щоб Excel відкрив кирилицю без танців із кодуванням.
    payload = "﻿" + buf.getvalue()
    return Response(payload, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="ai-knowledge-hub-kpi-'
                               f'{data["range"]["from"]}_{data["range"]["to"]}.csv"',
    })


# ------------------- Опитування NPS / CSAT / CES (BR-16) -------------------

# Налаштування частоти показу — керуються без зміни коду (через /catalog/settings).
SURVEY_SETTINGS = {
    # Скільки днів не питати після відповіді / після закриття без відповіді.
    "survey_days_after_answer": "90",
    "survey_days_after_dismiss": "14",
    # Скільки карток треба відкрити, перш ніж узагалі щось запитати.
    "survey_min_views": "5",
    # Глобальний вимикач опитувань.
    "survey_enabled": "1",
}

SURVEY_QUESTIONS = {
    "nps": {"title": "Наскільки ймовірно, що порекомендуєте AI Knowledge Hub колезі?",
            "low": "0 — точно ні", "high": "10 — точно так"},
    "csat": {"title": "Наскільки ви задоволені контентом і пошуком у хабі?",
             "low": "1 — зовсім ні", "high": "5 — цілком"},
    "ces": {"title": "Наскільки легко було знайти потрібне й застосувати його?",
            "low": "1 — дуже важко", "high": "5 — дуже легко"},
}


def _survey_setting(key):
    return AppSetting.get(key, SURVEY_SETTINGS[key])


def _survey_int(key):
    try:
        return int(_survey_setting(key))
    except (TypeError, ValueError):
        return int(SURVEY_SETTINGS[key])


@bp.get("/survey/due")
@require_auth
def survey_due():
    """Чи час питати цього користувача — і про що саме.

    Повертає щонайбільше одне опитування за раз: показувати три підряд означає
    гарантовано не отримати відповіді на жодне.
    """
    user = current_user()
    if _survey_setting("survey_enabled") != "1":
        return jsonify({"due": None})

    views = ResourceView.query.filter_by(user_id=user.id).count()
    if views < _survey_int("survey_min_views"):
        return jsonify({"due": None})

    now = datetime.utcnow()
    after_answer = timedelta(days=_survey_int("survey_days_after_answer"))
    after_dismiss = timedelta(days=_survey_int("survey_days_after_dismiss"))
    prompts = {p.kind: p for p in SurveyPrompt.query.filter_by(user_id=user.id)}

    for kind in SURVEY_KINDS:
        prompt = prompts.get(kind)
        if prompt is None:
            break
        wait = after_answer if prompt.answered else after_dismiss
        if now - prompt.shown_at >= wait:
            break
    else:
        return jsonify({"due": None})

    low, high = SURVEY_SCALES[kind]
    return jsonify({"due": {"kind": kind, "min": low, "max": high,
                            **SURVEY_QUESTIONS[kind]}})


@bp.post("/survey")
@require_auth
def submit_survey():
    """Відповідь на опитування або закриття без відповіді (`dismissed: true`)."""
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    if kind not in SURVEY_KINDS:
        raise ApiError("Тип опитування має бути одним із: " + ", ".join(SURVEY_KINDS),
                       400, "validation_error")
    user = current_user()
    now = datetime.utcnow()

    prompt = SurveyPrompt.query.filter_by(user_id=user.id, kind=kind).first()
    if prompt is None:
        prompt = SurveyPrompt(user_id=user.id, kind=kind)
        db.session.add(prompt)
    prompt.shown_at = now

    if data.get("dismissed"):
        # Закрите без відповіді не повертається одразу — але повернеться раніше,
        # ніж до того, хто відповів.
        prompt.answered = False
        db.session.commit()
        return jsonify({"recorded": False})

    low, high = SURVEY_SCALES[kind]
    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        raise ApiError(f"Оцінка має бути числом від {low} до {high}",
                       400, "validation_error")
    if not low <= score <= high:
        raise ApiError(f"Оцінка має бути числом від {low} до {high}",
                       400, "validation_error")

    comment = (data.get("comment") or "").strip()
    if len(comment) > 2000:
        raise ApiError("Коментар завеликий (максимум 2000 символів)",
                       400, "validation_error")

    prompt.answered = True
    db.session.add(SurveyResponse(
        kind=kind, score=score, comment=comment or None,
        user_id=user.id,
        user_role="manager" if _is_manager(user) else "user",
        context=(data.get("context") or "catalog")[:50],
        context_id=data.get("context_id") if isinstance(data.get("context_id"), int) else None,
        created_at=now, day=now.date(),
    ))
    db.session.commit()
    return jsonify({"recorded": True}), 201


def _survey_summary(since=None, until=None):
    """Підсумки опитувань: NPS, середні CSAT/CES і вільні коментарі."""
    def _scoped(query):
        if since is not None:
            query = query.filter(SurveyResponse.day >= since)
        if until is not None:
            query = query.filter(SurveyResponse.day <= until)
        return query

    out = {}
    for kind in SURVEY_KINDS:
        rows = _scoped(SurveyResponse.query.filter_by(kind=kind)).all()
        block = {"responses": len(rows), "avg": None}
        if rows:
            block["avg"] = round(sum(r.score for r in rows) / len(rows), 2)
        if kind == "nps":
            buckets = {"promoter": 0, "passive": 0, "detractor": 0}
            for r in rows:
                buckets[SurveyResponse.nps_bucket(r.score)] += 1
            block["buckets"] = buckets
            block["score"] = (round((buckets["promoter"] - buckets["detractor"])
                                    / len(rows) * 100) if rows else None)
            for name, n in buckets.items():
                block[f"{name}_pct"] = _pct(n, len(rows))
        out[kind] = block

    comments = (_scoped(SurveyResponse.query.filter(SurveyResponse.comment.isnot(None)))
                .order_by(SurveyResponse.created_at.desc()).limit(20).all())
    out["comments"] = [{"kind": c.kind, "score": c.score, "comment": c.comment,
                        "role": c.user_role,
                        "created_at": c.created_at.isoformat()} for c in comments]
    return out


# ------------------- Пошук мовою задачі (BR-07, AKH-11) -------------------

def _searchable_items(user):
    """Матеріали, доступні користувачу, у вигляді словників для пошуку."""
    query = CatalogResource.query
    if not _is_manager(user):
        query = query.filter(CatalogResource.status.in_(PUBLIC_STATUSES))
    return [r.to_dict() for r in query.all()]


def _synonym_index():
    rows = SearchSynonym.query.filter_by(is_active=True).all()
    return search_service.build_synonym_index(rows)


@bp.get("/search")
@require_auth
def task_search():
    """Пошук мовою бізнес-задачі: `?q=хочу автоматизувати збір ідей`.

    Шукає за назвою, описом, тегами, інструментами й текстом матеріалу з різною
    вагою полів, розуміє словник формулювань задач і терпить українські
    відмінки та одруки. Коли точних збігів немає — повертає найближчі за
    змістом і позначає запит як незакритий, щоб менеджер бачив, чого бракує.
    """
    raw = (request.args.get("q") or "").strip()
    if not raw:
        raise ApiError("Порожній запит", 400, "validation_error")
    if len(raw) > MAX_QUERY:
        raw = raw[:MAX_QUERY]

    user = current_user()
    items = _searchable_items(user)
    synonyms = _synonym_index()
    ratings = _rating_map("resource")

    def pack(rows):
        return [dict(item, **ratings.get(item["id"], _EMPTY_RATING),
                     match_score=score, matched=matched)
                for item, score, matched in rows]

    hits = search_service.search(raw, items, synonyms, limit=20)
    suggestions = [] if hits else search_service.nearest(raw, items, synonyms)

    log = SearchQueryLog(
        query_text=raw, query_norm=raw.casefold(),
        results_count=len(hits), user_id=user.id,
        kind=request.args.get("kind") or None,
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "query": raw,
        "results": pack(hits),
        # Коли нічого не знайдено — не порожній екран, а найближче за змістом
        # плюс пропозиція поділитися ідеєю.
        "suggestions": pack(suggestions),
        "unanswered": not hits,
        "search_log_id": log.id,
    })


@bp.get("/synonyms")
@require_auth
def list_synonyms():
    """Словник формулювань задач. Менеджер бачить і вимкнені."""
    query = SearchSynonym.query
    if not _is_manager(current_user()):
        query = query.filter_by(is_active=True)
    rows = query.order_by(SearchSynonym.phrase).all()
    return jsonify([r.to_dict() for r in rows])


@bp.post("/synonyms")
@require_global_role("admin", "skill_manager")
def create_synonym():
    data = request.get_json(silent=True) or {}
    phrase = " ".join((data.get("phrase") or "").split())
    terms = " ".join((data.get("terms") or "").split())
    if not phrase or not terms:
        raise ApiError("Вкажіть формулювання і канонічні слова через кому",
                       400, "validation_error")
    if any(s.phrase.casefold() == phrase.casefold() for s in SearchSynonym.query.all()):
        raise ApiError("Таке формулювання вже є у словнику", 409, "synonym_exists")
    row = SearchSynonym(phrase=phrase, terms=terms)
    if "is_active" in data:
        row.is_active = bool(data["is_active"])
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@bp.patch("/synonyms/<int:synonym_id>")
@require_global_role("admin", "skill_manager")
def update_synonym(synonym_id):
    row = SearchSynonym.query.get_or_404(synonym_id)
    data = request.get_json(silent=True) or {}
    if "phrase" in data:
        phrase = " ".join((data.get("phrase") or "").split())
        if not phrase:
            raise ApiError("Формулювання не може бути порожнім",
                           400, "validation_error")
        if any(s.phrase.casefold() == phrase.casefold() and s.id != synonym_id
               for s in SearchSynonym.query.all()):
            raise ApiError("Таке формулювання вже є у словнику", 409, "synonym_exists")
        row.phrase = phrase
    if "terms" in data:
        terms = " ".join((data.get("terms") or "").split())
        if not terms:
            raise ApiError("Канонічні слова не можуть бути порожніми",
                           400, "validation_error")
        row.terms = terms
    if "is_active" in data:
        row.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify(row.to_dict())


@bp.delete("/synonyms/<int:synonym_id>")
@require_global_role("admin", "skill_manager")
def delete_synonym(synonym_id):
    row = SearchSynonym.query.get_or_404(synonym_id)
    db.session.delete(row)
    db.session.commit()
    return jsonify({"message": "Формулювання видалено"})


# ---------------- AI-помічник пошуку (BR-08, FR-09, AKH-12) ----------------

@bp.post("/assistant")
@require_auth
def assistant():
    """Відповідь помічника на задачу, сформульовану звичайною мовою.

    Картки добирає детермінований пошук; модель лише пояснює добірку. Тому в
    відповіді фізично не може бути посилання, якого немає в базі.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        raise ApiError("Опишіть задачу", 400, "validation_error")
    if len(question) > MAX_QUERY * 5:
        raise ApiError("Запит завеликий", 400, "validation_error")

    user = current_user()
    result = assistant_service.answer(question, _searchable_items(user),
                                      _synonym_index())

    # Питання помічнику — теж пошуковий запит: він має потрапити в аналітику,
    # інакше менеджер не побачить, чого бракує саме тут.
    log = SearchQueryLog(
        query_text=question[:MAX_QUERY], query_norm=question[:MAX_QUERY].casefold(),
        results_count=len(result["items"]), user_id=user.id, kind="assistant",
    )
    db.session.add(log)
    db.session.commit()

    result["search_log_id"] = log.id
    return jsonify(result)


@bp.post("/assistant/feedback")
@require_auth
def assistant_feedback():
    """Оцінка відповіді помічника: допомогла чи ні."""
    data = request.get_json(silent=True) or {}
    log = SearchQueryLog.query.get_or_404(data.get("search_log_id") or 0)
    if log.user_id != current_user().id:
        raise ApiError("Чужий запис пошуку", 403, "forbidden")
    if "helpful" not in data:
        raise ApiError("Вкажіть helpful (true або false)", 400, "validation_error")
    # Переводимо в наявне поле «після запиту щось відкрили»: корисна відповідь
    # означає, що запит закрито. Окремої таблиці для цього не заводимо.
    log.opened_item_type = "assistant_helpful" if data["helpful"] else None
    log.assistant_helpful = bool(data["helpful"])
    db.session.commit()
    return jsonify({"recorded": True, "helpful": log.assistant_helpful})


@bp.get("/search-analytics")
@require_global_role("admin", "skill_manager")
def search_analytics():
    """Аналітика запитів: теми, глухі кути й користь помічника (BR-08)."""
    def _grouped(filter_fn=None, limit=10):
        query = (db.session.query(SearchQueryLog.query_norm,
                                  func.count(SearchQueryLog.id),
                                  func.max(SearchQueryLog.query_text))
                 .group_by(SearchQueryLog.query_norm))
        if filter_fn is not None:
            query = filter_fn(query)
        rows = query.order_by(func.count(SearchQueryLog.id).desc()).limit(limit).all()
        return [{"query": r[2], "count": int(r[1])} for r in rows]

    total = SearchQueryLog.query.count()
    assistant_logs = SearchQueryLog.query.filter_by(kind="assistant")
    helpful = assistant_logs.filter_by(assistant_helpful=True).count()
    unhelpful = assistant_logs.filter_by(assistant_helpful=False).count()
    rated = helpful + unhelpful

    return jsonify({
        "total": total,
        "top": _grouped(),
        "no_results": _grouped(lambda q: q.having(
            func.max(SearchQueryLog.results_count) == 0)),
        # Знайшли, але нічого не відкрили — теж сигнал: контент не переконує.
        "no_open": _grouped(lambda q: q.having(db.and_(
            func.max(SearchQueryLog.results_count) > 0,
            func.count(SearchQueryLog.opened_item_type) == 0))),
        "assistant": {
            "questions": assistant_logs.count(),
            "rated": rated,
            "helpful": helpful,
            "helpful_pct": _pct(helpful, rated),
        },
    })


# ------------- Профіль: рівень зрілості та підрозділ (AKH-13) -------------

@bp.patch("/profile")
@require_auth
def update_profile():
    """Користувач сам вказує свій рівень AI-зрілості та підрозділ."""
    data = request.get_json(silent=True) or {}
    user = current_user()
    if "maturity_level_id" in data:
        user.maturity_level_id = _resolve_term(
            "maturity", data.get("maturity_level_id"), "рівень зрілості")
    if "department" in data:
        value = (data.get("department") or "").strip()
        user.department = value[:200] or None
    db.session.commit()
    return jsonify({"maturity_level_id": user.maturity_level_id,
                    "department": user.department})


def _maturity_ladder():
    """Рівні зрілості в порядку зростання."""
    return (CatalogTerm.query.filter_by(kind="maturity", is_active=True)
            .order_by(CatalogTerm.position, CatalogTerm.id).all())


@bp.get("/maturity")
@require_auth
def maturity_overview():
    """Рівень користувача, наступний рівень і матеріали під поточний рівень."""
    user = current_user()
    ladder = _maturity_ladder()
    current = next((t for t in ladder if t.id == user.maturity_level_id), None)
    nxt = None
    if current is not None:
        index = ladder.index(current)
        nxt = ladder[index + 1] if index + 1 < len(ladder) else None

    ratings = _rating_map("resource")
    items = []
    if current is not None:
        rows = (CatalogResource.query
                .filter(CatalogResource.status.in_(PUBLIC_STATUSES))
                .filter(CatalogResource.id.in_(
                    db.session.query(CatalogResourceMaturity.resource_id)
                    .filter_by(term_id=current.id)))
                .order_by(CatalogResource.is_featured.desc(),
                          CatalogResource.opens_count.desc())
                .limit(SHOWCASE_LIMIT).all())
        items = [dict(r.to_dict(), **ratings.get(r.id, _EMPTY_RATING)) for r in rows]

    return jsonify({
        "levels": [t.to_dict() for t in ladder],
        "current": current.to_dict() if current else None,
        "next": nxt.to_dict() if nxt else None,
        # Підказка «що дає наступний рівень» — з опису самого рівня, щоб текст
        # редагувався в довіднику, а не жив у коді.
        "next_hint": nxt.description if nxt else None,
        "items": items,
    })


# ------------------ Персональні рекомендації (AKH-15) ------------------

RECOMMEND_LIMIT = 6


@bp.get("/recommendations")
@require_auth
def recommendations():
    """Добірка «Рекомендовано вам» із поясненням кожної поради.

    Кожна порада має причину, яку видно користувачу: інакше блок виглядає як
    випадковий набір карток і йому не довіряють. Уже переглянуте й приховане
    не повертається.
    """
    user = current_user()
    seen = {row.target_id for row in
            ResourceView.query.filter_by(user_id=user.id, target_type="resource")}
    hidden = {row.item_id for row in
              HiddenRecommendation.query.filter_by(user_id=user.id,
                                                   item_type="resource")}
    favorites = {row.item_id for row in
                 CatalogFavorite.query.filter_by(user_id=user.id,
                                                 item_type="resource")}

    pool = (CatalogResource.query
            .filter(CatalogResource.status.in_(PUBLIC_STATUSES)).all())
    available = [r for r in pool if r.id not in seen and r.id not in hidden]

    # Причини в порядку переконливості: рівень → колеги → інтерес → популярне.
    reasons = {}

    if user.maturity_level_id:
        level_ids = {row.resource_id for row in CatalogResourceMaturity.query
                     .filter_by(term_id=user.maturity_level_id)}
        level = CatalogTerm.query.get(user.maturity_level_id)
        for r in available:
            if r.id in level_ids:
                reasons.setdefault(r.id, f"Відповідає вашому рівню «{level.name}»")

    if user.department:
        peers = [u.id for u in User.query.filter_by(department=user.department)
                 if u.id != user.id]
        if peers:
            popular_with_peers = dict(
                db.session.query(ResourceView.target_id, func.count(ResourceView.id))
                .filter(ResourceView.target_type == "resource",
                        ResourceView.user_id.in_(peers))
                .group_by(ResourceView.target_id).all())
            for r in available:
                if popular_with_peers.get(r.id):
                    reasons.setdefault(r.id, f"Популярне у підрозділі "
                                             f"«{user.department}»")

    # Категорії та теги того, що користувач уже вподобав або дивився.
    liked = [r for r in pool if r.id in favorites or r.id in seen]
    liked_categories = {r.category for r in liked if r.category}
    liked_tags = {t.id for r in liked for t in r.tag_terms}
    for r in available:
        if r.category and r.category in liked_categories:
            reasons.setdefault(r.id, f"Схоже на те, що ви дивилися: "
                                     f"«{r.category}»")
        elif liked_tags and {t.id for t in r.tag_terms} & liked_tags:
            reasons.setdefault(r.id, "Спільні теги з вашими матеріалами")

    # Новому користувачу без історії блок теж має бути осмисленим.
    for r in available:
        if r.is_featured:
            reasons.setdefault(r.id, "Відібрано командою хабу")
        elif (r.opens_count or 0) > 0:
            reasons.setdefault(r.id, "Часто відкривають колеги")
        else:
            reasons.setdefault(r.id, "Нове в базі знань")

    ratings = _rating_map("resource")
    ranked = sorted(available,
                    key=lambda r: (not r.is_featured, -(r.opens_count or 0), -r.id))
    items = [dict(r.to_dict(), **ratings.get(r.id, _EMPTY_RATING),
                  recommend_reason=reasons[r.id])
             for r in ranked[:RECOMMEND_LIMIT]]
    return jsonify({"items": items})


@bp.post("/recommendations/hide")
@require_auth
def hide_recommendation():
    """«Більше не показувати» — назавжди, а не до наступного перерахунку."""
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    if not isinstance(item_id, int):
        raise ApiError("Вкажіть item_id", 400, "validation_error")
    if CatalogResource.query.get(item_id) is None:
        raise ApiError("Матеріал не знайдено", 404, "not_found")
    user = current_user()
    exists = HiddenRecommendation.query.filter_by(
        user_id=user.id, item_type="resource", item_id=item_id).first()
    if exists is None:
        db.session.add(HiddenRecommendation(user_id=user.id, item_type="resource",
                                            item_id=item_id))
        db.session.commit()
    return jsonify({"hidden": True, "item_id": item_id})
