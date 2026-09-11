"""Каталог AI Knowledge Hub: розділи, ресурси, обране, пошук та аналітика.

Навички лишаються у /api/skills — тут усе інше наповнення каталогу.
Керування ресурсами й розділами: Admin / Skill Manager. Перегляд — усі.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    CatalogSection, CatalogFolder, CatalogTerm, CatalogResourceTag,
    CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog,
    Skill, SkillFeedback, AppSetting,
    RESOURCE_TYPES, LINK_TYPES, BODY_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
    TERM_KINDS, SINGLE_VALUE_TERM_KINDS,
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

    for field in ("category", "author", "icon_emoji", "owner", "tools"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(res, field, value or None)

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

    if source.kind == "tag":
        # Картки, що вже мають цільовий тег, інакше отримали б дубль зв'язку.
        taken = {row.resource_id for row in
                 CatalogResourceTag.query.filter_by(term_id=target.id)}
        for row in CatalogResourceTag.query.filter_by(term_id=source.id).all():
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
    if term.kind == "tag":
        CatalogResourceTag.query.filter_by(term_id=term_id).delete(
            synchronize_session=False)
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
    """Лічильник відкриттів/копіювань — для сортування «за популярністю»."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    res.opens_count = (res.opens_count or 0) + 1
    db.session.commit()
    return jsonify({"opens_count": res.opens_count})


@bp.delete("/resources/<int:resource_id>")
@require_global_role("admin", "skill_manager")
def delete_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    CatalogResourceTag.query.filter_by(resource_id=resource_id)\
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
    """Налаштування каталогу для UI."""
    return jsonify({"strict_publish": strict_publish_enabled()})


@bp.post("/settings")
@require_global_role("admin", "skill_manager")
def set_catalog_settings():
    data = request.get_json(silent=True) or {}
    if "strict_publish" not in data:
        raise ApiError("Вкажіть strict_publish", 400, "validation_error")
    AppSetting.set(STRICT_PUBLISH_KEY, "1" if data["strict_publish"] else "0")
    db.session.commit()
    return jsonify({"strict_publish": strict_publish_enabled()})


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
