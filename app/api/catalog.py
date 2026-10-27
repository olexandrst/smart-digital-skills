"""Каталог AI Knowledge Hub: розділи, ресурси, обране, пошук та аналітика.

Навички лишаються у /api/skills — тут усе інше наповнення каталогу.
Матеріал створює будь-який користувач, а в каталог він потрапляє після
схвалення менеджером свого розділу (попередня модерація, див.
moderation_service). Структуру каталогу, довідники й налаштування веде
адміністратор (див. app/core/access.py). Перегляд опублікованого — усі.
"""
import os
import re
from datetime import datetime, timedelta
from flask import (Blueprint, request, jsonify, send_from_directory, Response,
                   current_app)
from sqlalchemy import func
from app.extensions import db
from app.core import access
from app.core.permissions import require_auth, require_admin, require_moderator
from app.core.security import current_user
from app.core.errors import ApiError
from app.services import (
    file_service, usage_service, search_service, assistant_service, review_service,
    text_extract_service, comment_service, moderation_service,
)
from app.models import (
    CatalogSection, CatalogFolder, CatalogTerm, CatalogResourceTag,
    CatalogResourceMaturity, HiddenRecommendation,
    CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog, SearchSynonym,
    UserFile,
    Skill, SkillFeedback, AppSetting, SurveyResponse, SurveyPrompt, ResourceView,
    Notification,
    User, SectionManager,
    RESOURCE_TYPES, LINK_TYPES, BODY_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, AUTHOR_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
    TERM_KINDS, MULTI_VALUE_TERM_KINDS,
    CODE_BOUND_TERM_KINDS, VIEW_TARGETS,
    SURVEY_KINDS, SURVEY_SCALES,
)

bp = Blueprint("catalog", __name__)

FAVORITE_TYPES = {"skill", "resource"}
MAX_BODY = 100_000
MAX_QUERY = 200
# Ключ налаштування «сувора публікація» (BR-05): вимагати обов'язкові поля.
STRICT_PUBLISH_KEY = "catalog_publish_strict"
# Посилання на тест рівня AI-обізнаності: з'являється кнопкою на сторінці
# «Мій розвиток», щойно адміністратор його вкаже (фідбек тестування, п. 4).
MATURITY_TEST_KEY = "maturity_test_url"
# Поля, без яких матеріал не публікується при увімкненій суворій публікації.
REQUIRED_ON_PUBLISH = (
    ("description", "короткий опис"),
    ("section_id", "розділ"),
    ("category", "категорія"),
    ("owner", "власник матеріалу"),
    ("next_review_at", "дата наступного перегляду"),
)


def material_types():
    """Активні види матеріалів із довідника (BR/NFR-05).

    Довідник — джерело правди; константи RESOURCE_TYPES лишаються запасним
    варіантом лише для порожньої бази, щоб застосунок піднявся до першого
    наповнення довідників.
    """
    rows = (CatalogTerm.query.filter_by(kind="material_type", is_active=True)
            .order_by(CatalogTerm.position, CatalogTerm.id).all())
    if rows:
        return {r.code: r for r in rows if r.code}
    return {}


def known_type_codes():
    """Коди видів матеріалів, які застосунок приймає зараз."""
    codes = list(material_types())
    return codes or list(RESOURCE_TYPES)


def type_behaviour(code):
    """Поведінка виду: чи потрібні посилання, текст, область і як їх підписати."""
    term = material_types().get(code)
    if term is not None:
        return {
            "requires_url": bool(term.requires_url),
            "requires_body": bool(term.requires_body),
            "has_scope": bool(term.has_scope),
            "url_label": term.url_label or "посилання",
            "body_label": term.body_label or "Текст",
        }
    # Порожній довідник — поведінка з констант, щоб нічого не зламалося.
    return {
        "requires_url": code in LINK_TYPES,
        "requires_body": code in BODY_TYPES,
        "has_scope": code in ("mcp", "link"),
        "url_label": "endpoint сервера" if code == "mcp" else "посилання",
        "body_label": "Текст",
    }


# --- Хто що може з матеріалом ---
#
# Попередня модерація. Автор створює чернетку й подає її на розгляд; у
# каталог матеріал потрапляє після схвалення менеджером розділу (або
# адміністратором). Хто модерує розділ, публікує одразу. Зміни автора до
# опублікованого чекають схвалення, не ховаючи опубліковану версію. Менеджер
# розділу може з матеріалами своїх розділів усе — автор дізнається про його
# рішення сповіщенням. Архів і «потребує оновлення» — інструменти менеджера.

def _is_author(res, user):
    return moderation_service.is_author(res, user)


def _can_see(res, user):
    return moderation_service.can_see(res, user, PUBLIC_STATUSES)


def _may_edit(res, user):
    return _is_author(res, user) or access.can_moderate(user, res)


# Типовий період до наступного перегляду, якщо автор його не задав.
DEFAULT_REVIEW_MONTHS = 6


def _default_ownership(item, user):
    """Власником опублікованого матеріалу стає його автор (див. _publish_resource).

    Правило однакове для всіх ролей. Без власника й дати перегляду
    опублікований матеріал ніхто не актуалізує, а сувора публікація взагалі
    не випустила б його: таких полів у майстрі немає. Власника, вказаного
    явно (повний редактор менеджера), не чіпаємо.
    """
    if not (item.owner or "").strip():
        item.owner = user.full_name or user.username
    if hasattr(item, "next_review_at") and item.next_review_at is None:
        item.next_review_at = datetime.utcnow() + timedelta(days=30 * DEFAULT_REVIEW_MONTHS)


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


def target_section(item, data):
    """Розділ, у якому опиниться матеріал після змін із запиту (див. _apply_placement)."""
    if "folder_id" in data:
        folder = _resolve_folder(data.get("folder_id"))
        if folder is not None:
            return folder.section_id
    if "section_id" in data:
        return _resolve_section(data.get("section_id"))
    return item.section_id


def require_stays_moderated(user, item, data):
    """Менеджер не виводить матеріал зі своїх розділів: у чужому розділі його
    мав би розглянути інший менеджер."""
    if (item.status != "draft" and access.can_moderate(user, item)
            and not access.can_moderate_section(user, target_section(item, data))):
        raise ApiError("Перенести матеріал можна лише в розділ, який ви модеруєте",
                       403, "forbidden")


def _apply_fields(res, data, *, creating=False):
    """Переносить поля запиту в ресурс із валідацією (спільне для POST/PATCH)."""
    if creating or "resource_type" in data:
        rtype = (data.get("resource_type") or "").strip()
        allowed = known_type_codes()
        if rtype not in allowed:
            raise ApiError("Тип ресурсу має бути одним із: " + ", ".join(allowed),
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

    # Колір картки визначає вид матеріалу (фідбек, п. 2.2): поле «accent»
    # більше не приймаємо, щоб однаковий вид завжди виглядав однаково.

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

    # Що саме обов'язкове — вирішує запис довідника, а не константа в коді.
    behaviour = type_behaviour(res.resource_type)
    needs_url = behaviour["requires_url"]
    url_label = behaviour["url_label"]
    if "url" in data or (creating and needs_url):
        res.url = _clean_url(data.get("url"), required=needs_url, label=url_label)
    if needs_url and not res.url:
        raise ApiError(f"Вкажіть {url_label}", 400, "validation_error")
    if needs_url and not res.link_scope:
        res.link_scope = "external"

    # Види, які без тексту беззмістовні (теж із довідника).
    if behaviour["requires_body"] and creating and not res.body:
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
    moderation_service.log(item_type, item, current_user(), from_status, to_status, note)


# ------------------------------- Розділи -------------------------------

@bp.get("/sections")
@require_auth
def list_sections():
    """Розділи каталогу з кількістю матеріалів у кожному. Приховані розділи
    й неопубліковане в лічильниках бачить адміністратор."""
    manager = access.is_admin(current_user())
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
@require_admin
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
@require_admin
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
@require_admin
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
    # Призначення менеджерів зникають разом із розділом; матеріали без розділу
    # далі модерує адміністратор.
    SectionManager.query.filter_by(section_id=section_id).delete(
        synchronize_session=False)
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
    manager = access.is_admin(current_user())
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
@require_admin
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
@require_admin
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
@require_admin
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
    manager = access.is_admin(current_user())
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
@require_admin
def create_term():
    data = request.get_json(silent=True) or {}
    kind = _require_term_kind((data.get("kind") or "").strip())
    if kind in CODE_BOUND_TERM_KINDS:
        # Статуси й акценти зав'язані на логіку життєвого циклу та оформлення:
        # додавати коди без підтримки в коді не можна, але назву, іконку,
        # колір і видимість наявних змінювати вільно.
        raise ApiError(f"Довідник «{kind}» має фіксований набір значень — "
                       f"тут можна змінити назву, оформлення й видимість",
                       400, "term_kind_fixed")
    name = " ".join((data.get("name") or "").split())
    if not name:
        raise ApiError("Назва значення не може бути порожньою",
                       400, "validation_error")
    if _term_by_name(kind, name) is not None:
        raise ApiError("Таке значення вже є в довіднику", 409, "term_exists")
    term = CatalogTerm(kind=kind, name=name, name_norm=CatalogTerm.normalize(name))
    if kind == "material_type":
        term.code = _new_type_code(data.get("code"), name)
    _apply_term_fields(term, data)
    db.session.add(term)
    db.session.commit()
    return jsonify(term.to_dict()), 201


def _new_type_code(raw, name):
    """Код нового виду матеріалу: латиницею, унікальний, придатний для URL."""
    import re as _re
    from unicodedata import normalize as _norm

    base = (raw or name or "").strip().lower()
    translit = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
        "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia", " ": "_", "-": "_",
    })
    code = _re.sub(r"[^a-z0-9_]", "", _norm("NFC", base).translate(translit))[:40]
    if not code:
        raise ApiError("Не вдалося скласти код виду — вкажіть його латиницею",
                       400, "validation_error")
    taken = {t.code for t in CatalogTerm.query.filter_by(kind="material_type")}
    if code in taken:
        raise ApiError("Вид матеріалу з таким кодом уже існує", 409, "term_exists")
    return code


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
    for field in ("icon_emoji", "color", "url_label", "body_label"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(term, field, value or None)
    for flag in ("requires_url", "requires_body", "has_scope"):
        if flag in data:
            setattr(term, flag, bool(data.get(flag)))


@bp.patch("/terms/<int:term_id>")
@require_admin
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
@require_admin
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
    if source.kind in CODE_BOUND_TERM_KINDS or source.kind == "material_type":
        raise ApiError("Значення цього довідника не зливаються",
                       400, "term_kind_fixed")

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
@require_admin
def delete_term(term_id):
    """Видаляє значення довідника. Посилання на нього в картках знімаються."""
    term = CatalogTerm.query.get_or_404(term_id)
    if term.kind in CODE_BOUND_TERM_KINDS:
        raise ApiError("Значення цього довідника не видаляється — його можна "
                       "приховати", 400, "term_kind_fixed")
    if term.kind == "material_type":
        used = CatalogResource.query.filter_by(resource_type=term.code).count()
        if used:
            raise ApiError(f"Вид використовують {used} матеріалів — спершу "
                           f"перенесіть їх або приховайте вид",
                           409, "term_in_use")
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

    Усі бачать опубліковані та ті, що потребують оновлення; менеджер розділу —
    ще й усе в розділах, які модерує, адміністратор — усе. `mine=1` — лише
    матеріали, які створив поточний користувач, у будь-якому статусі: так
    автор бачить свої чернетки, не отримуючи доступу до чужих.
    """
    user = current_user()
    manager = access.is_admin(user)
    query = CatalogResource.query

    mine = request.args.get("mine") in ("1", "true")
    if mine:
        query = query.filter_by(created_by=user.id)

    rtype = request.args.get("type")
    if rtype:
        if rtype not in known_type_codes():
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
        if (not mine and status not in PUBLIC_STATUSES
                and not access.is_moderator(user)):
            raise ApiError("Матеріали цього статусу недоступні", 403, "forbidden")
        query = query.filter_by(status=status)
    if not manager and not mine:
        query = query.filter(moderation_service.visible_filter(
            CatalogResource, user, PUBLIC_STATUSES))

    order = ((CatalogResource.updated_at.desc(), CatalogResource.id.desc()) if mine
             else (CatalogResource.is_featured.desc(), CatalogResource.id.desc()))
    items = query.order_by(*order).all()
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
    manager = access.is_moderator(current_user())
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


@bp.post("/resources/extract-text")
@require_auth
def extract_text():
    """Текст із файлу для поля «зміст» майстра створення матеріалу.

    Сам файл тут не зберігається: користувач ще нічого не створив, а чи
    лишати оригінал вкладенням — вирішує він сам уже після створення картки.
    Зберігаються лише картинки з .docx — вони стають картинками в тексті.
    """
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    data = file.read()
    max_bytes = current_app.config.get("USER_FILE_MAX_BYTES", 25 * 1024 * 1024)
    if len(data) > max_bytes:
        raise ApiError("Файл завеликий", 400, "file_too_large")
    user = current_user()
    images = []

    def keep_image(name, blob):
        """Картинка з документа Word стає картинкою в тексті."""
        try:
            uf = file_service.save_inline_image(user, name, blob)
        except ApiError:
            return None        # формат, якого браузер не покаже (EMF, WMF), — пропускаємо
        images.append({"id": uf.id, "url": file_service.preview_url(uf, user)})
        return file_service.inline_ref(uf)

    result = text_extract_service.extract(file.filename, data, on_image=keep_image)
    return jsonify(dict(result, filename=file.filename, images=images))


@bp.get("/resources/<int:resource_id>")
@require_auth
def get_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if not _can_see(res, user):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    return jsonify(_payload(res, user))


def _payload(res, user):
    """Картка для відповіді: рейтинг і пропозиція змін (автору й модератору)."""
    ratings = _rating_map("resource")
    out = dict(res.to_dict(), **ratings.get(res.id, _EMPTY_RATING))
    out["pending"] = moderation_service.pending_payload(res, user)
    out["can_moderate"] = access.can_moderate(user, res)
    return out


@bp.post("/resources")
@require_auth
def create_resource():
    """Новий матеріал: чернетка (`draft`) або подання (`review` / `published`).

    `published` публікує одразу лише той, хто модерує обраний розділ; для
    решти це подання на розгляд — у відповіді статус буде `review`.
    """
    data = request.get_json(silent=True) or {}
    user = current_user()
    status = data.get("status") or "draft"
    if status not in AUTHOR_STATUSES:
        raise ApiError("Новий матеріал можна зберегти чернеткою (draft), подати на "
                       "розгляд (review) або опублікувати (published)",
                       400, "validation_error")
    res = CatalogResource(created_by=user.id)
    res.author = user.full_name or user.username
    _apply_fields(res, data, creating=True)
    # «Рекомендований» — рішення того, хто модерує розділ.
    if not access.can_moderate(user, res):
        res.is_featured = False
    if status != "draft":
        moderation_service.require_section(user, res)
    db.session.add(res)
    db.session.flush()
    _set_tags(res, data.get("tags"))
    _set_maturity(res, data.get("maturity_ids"))
    file_service.sync_inline_images(res, user)
    if status != "draft":
        moderation_service.submit("resource", res, user, status,
                                  lambda item: _publish_resource(item, user))
    db.session.commit()
    return jsonify(_payload(res, user)), 201


def _publish_resource(res, user):
    """Що стається з матеріалом у момент публікації. Власником стає автор:
    менеджер, що схвалив матеріал, відповідає за модерацію, а не за зміст."""
    author = db.session.get(User, res.created_by) if res.created_by else None
    _default_ownership(res, author or user)
    _require_publish_ready(res)
    res.status = "published"
    if res.published_at is None:
        res.published_at = datetime.utcnow()
    res.reviewed_at = datetime.utcnow()
    res.review_note = None


# Поля, які автор не може змінити сам: «рекомендований» і стан — рішення модератора.
_MODERATOR_FIELDS = ("is_featured", "status")


def _check_changes(res, data):
    """Перевіряє зміни, не чіпаючи матеріал: на копії полів. Так пропозиція
    змін не потрапить на розгляд з помилкою, яку виявлять лише при схваленні."""
    copy = CatalogResource(**{c.name: getattr(res, c.name)
                              for c in CatalogResource.__table__.columns
                              if c.name != "id"})
    _apply_fields(copy, data)
    _tag_names(data.get("tags"))
    for raw in (data.get("maturity_ids") or []):
        _resolve_term("maturity", raw, "рівень зрілості")


def _apply_changes(res, data, user):
    """Застосовує зміни до матеріалу (власне збереження або схвалення пропозиції)."""
    _apply_fields(res, data)
    if "tags" in data:
        _set_tags(res, data.get("tags"))
    if "maturity_ids" in data:
        _set_maturity(res, data.get("maturity_ids"))


@bp.patch("/resources/<int:resource_id>")
@require_auth
def update_resource(resource_id):
    """Зміна матеріалу. Опублікований матеріал автор (якщо він не модерує
    розділ) змінює через пропозицію змін: опублікована версія лишається, доки
    менеджер розділу не схвалить нову (див. /changes)."""
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if not _may_edit(res, user):
        raise ApiError("Редагувати матеріал може його автор або менеджер розділу",
                       403, "forbidden")
    data = request.get_json(silent=True) or {}
    moderator = access.can_moderate(user, res)
    if not moderator:
        data = {k: v for k, v in data.items() if k not in _MODERATOR_FIELDS}

    if moderation_service.needs_pending(res, user):
        _check_changes(res, data)
        moderation_service.store_pending("resource", res, user, data)
        if "body" in data:
            file_service.sync_inline_images(res, user)
        db.session.commit()
        return jsonify(_payload(res, user))

    old_section = res.section_id
    require_stays_moderated(user, res, data)
    _apply_changes(res, data, user)
    if "body" in data:
        file_service.sync_inline_images(res, user)
    # Поданий матеріал переїхав в інший розділ — розглядати його тепер іншим.
    if res.status == "review" and res.section_id != old_section:
        moderation_service.require_section(user, res)
        review_service.notify_review_requested("resource", res, user)
    db.session.commit()
    return jsonify(_payload(res, user))


@bp.post("/resources/<int:resource_id>/status")
@require_auth
def change_resource_status(resource_id):
    """Зміна стану матеріалу.

    Автор подає матеріал на розгляд (`review`, або `published`, що для нього
    означає те саме), забирає з розгляду чи знімає з публікації (`draft`).
    Менеджер розділу — будь-який перехід: схвалити (`published`), повернути
    на доопрацювання (`draft`), архів, «потребує оновлення». Рішення щодо
    чужого матеріалу автор отримує сповіщенням із причиною (`note`).
    """
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    moderator = access.can_moderate(user, res)
    author = _is_author(res, user)
    if not (moderator or author):
        raise ApiError("Змінити стан матеріалу може його автор або менеджер розділу",
                       403, "forbidden")
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    allowed = RESOURCE_STATUSES if moderator else AUTHOR_STATUSES
    if new_status not in allowed:
        raise ApiError("Статус має бути одним із: " + ", ".join(allowed),
                       400, "validation_error")
    note = moderation_service.clean_note(data)
    old_status = res.status
    if old_status == new_status:
        return jsonify(_payload(res, user))

    if new_status in ("review", "published") and not moderator:
        moderation_service.submit("resource", res, user, new_status,
                                  lambda item: _publish_resource(item, user))
    elif new_status == "review":
        moderation_service.submit("resource", res, user, "review", lambda item: None)
    elif new_status == "published":
        _publish_resource(res, user)
        moderation_service.log("resource", res, user, old_status, "published", note)
        if not author:
            action = "approved" if old_status == "review" else "published"
            review_service.notify_moderated("resource", res, user, action, note)
    else:
        if new_status == "draft" and old_status in PUBLIC_STATUSES:
            _merge_pending(res, user)
        if new_status == "archived":
            _drop_pending(res, user)
        res.status = new_status
        if moderator and not author:
            # Причина лишається в картці: автор побачить її, відкривши матеріал.
            res.review_note = note if new_status in ("draft", "archived") else res.review_note
            action = ("returned" if old_status == "review" and new_status == "draft"
                      else {"draft": "unpublished"}.get(new_status, new_status))
            review_service.notify_moderated("resource", res, user, action, note)
        moderation_service.log("resource", res, user, old_status, new_status, note)
    db.session.commit()
    return jsonify(_payload(res, user))


def _merge_pending(res, user):
    """Матеріал повертається в чернетки — неcхвалені зміни автора стають
    чернеткою: далі він працює з власною останньою версією."""
    data = moderation_service.get_pending(res)
    if data is None:
        return
    moderation_service.clear_pending(res)
    _apply_changes(res, data, user)
    file_service.sync_inline_images(res, user)


def _drop_pending(res, user):
    if res.pending_data:
        moderation_service.clear_pending(res)
        file_service.sync_inline_images(res, user)


@bp.post("/resources/<int:resource_id>/changes")
@require_auth
def resource_changes(resource_id):
    """Рішення щодо пропозиції змін до опублікованого матеріалу.

    `action`: `approve` (менеджер розділу — зміни потрапляють у каталог),
    `reject` (менеджер — з причиною `note`, лишається попередня версія),
    `withdraw` (автор забирає свої зміни).
    """
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("approve", "reject", "withdraw"):
        raise ApiError("action має бути approve, reject або withdraw",
                       400, "validation_error")
    moderator = access.can_moderate(user, res)
    if action == "withdraw" and not (_is_author(res, user) or moderator):
        raise ApiError("Забрати зміни може їхній автор", 403, "forbidden")
    if action != "withdraw" and not moderator:
        raise ApiError("Рішення щодо змін ухвалює менеджер розділу", 403, "forbidden")
    changes = moderation_service.get_pending(res)
    if changes is None:
        raise ApiError("Змін, що чекають схвалення, немає", 400, "no_pending")
    note = moderation_service.clean_note(data)

    moderation_service.clear_pending(res)
    if action == "approve":
        _apply_changes(res, changes, user)
        res.reviewed_at = datetime.utcnow()
        moderation_service.log("resource", res, user, res.status, res.status,
                               note or "Зміни схвалено")
        review_service.notify_moderated("resource", res, user, "changes_approved", note)
    else:
        moderation_service.log("resource", res, user, res.status, res.status,
                               ("Зміни відхилено" + (f": {note}" if note else ""))
                               if action == "reject" else "Автор забрав зміни")
        if action == "reject":
            review_service.notify_moderated("resource", res, user,
                                            "changes_rejected", note)
    file_service.sync_inline_images(res, user)
    db.session.commit()
    return jsonify(_payload(res, user))


@bp.get("/resources/<int:resource_id>/review-log")
@require_auth
def resource_review_log(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    if not access.can_moderate(current_user(), res):
        raise ApiError("Журнал бачить менеджер розділу", 403, "forbidden")
    rows = (ReviewLog.query.filter_by(item_type="resource", item_id=resource_id)
            .order_by(ReviewLog.created_at.desc()).all())
    return jsonify([r.to_dict() for r in rows])


@bp.get("/moderation")
@require_moderator
def moderation_queue():
    """Черга модерації: подане на розгляд і зміни до опублікованого, що чекають
    схвалення, — у розділах, які модерує поточний користувач (адміністратор —
    усі, зокрема матеріали без розділу). Найдавніші — першими."""
    from app.api.skills import skill_payload
    user = current_user()
    items = []
    for res in CatalogResource.query.filter(
            moderation_service.queue_filter(CatalogResource, user)).all():
        items.append({"type": "resource",
                      "kind": "submitted" if res.status == "review" else "changes",
                      "at": (res.submitted_at if res.status == "review" else res.pending_at),
                      "item": _payload(res, user)})
    for skill in Skill.query.filter(moderation_service.queue_filter(Skill, user)).all():
        items.append({"type": "skill",
                      "kind": "submitted" if skill.status == "review" else "changes",
                      "at": (skill.submitted_at if skill.status == "review"
                             else skill.pending_at),
                      "item": skill_payload(skill, user)})
    items.sort(key=lambda x: x["at"] or datetime.min)
    for x in items:
        x["at"] = x["at"].isoformat() if x["at"] else None
    return jsonify({"items": items, "count": len(items)})


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
    if not _can_see(res, user):
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
    if not _can_see(res, current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    return res


def _may_attach(res, user):
    """Наповнювати картку файлами може її автор або менеджер розділу.

    Поле `owner` — вільний текст (прізвище відповідального), а не посилання на
    користувача, тому правами воно керувати не може: звірятися з ним означало б
    видавати доступ за збігом рядків.
    """
    return _may_edit(res, user)


@bp.get("/resources/<int:resource_id>/files")
@require_auth
def list_resource_files(resource_id):
    """Вкладення картки — видно всім, хто бачить саму картку.

    Зображення й відео отримують `media` і `preview_url` — підписане
    посилання для <img>/<video>, які не вміють передати JWT.
    """
    _visible_resource(resource_id)
    user = current_user()
    rows = (UserFile.query.filter_by(resource_id=resource_id)
            .order_by(UserFile.created_at.desc()).all())
    out = []
    for f in rows:
        item, media = f.to_dict(), file_service.media_of(f)
        item["media"] = media[0] if media else None
        item["preview_url"] = file_service.preview_url(f, user) if media else None
        out.append(item)
    return jsonify(out)


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
    uf = file_service.save_attachment(user, file.filename, file.read(), res.id)
    # Файл до опублікованого з'являється одразу — менеджер розділу має знати.
    if moderation_service.is_public(res):
        review_service.notify_updated("resource", res, user)
        db.session.commit()
    return jsonify(uf.to_dict()), 201


@bp.post("/images")
@require_auth
def upload_inline_image():
    """Зображення для тексту матеріалу (фідбек тестування, п. 3.2).

    Повертає постійне посилання `ref` — його редактор пише в текст — і
    підписане `url` для показу. До картки картинка прив'язується, коли
    матеріал зберігають (див. file_service.sync_inline_images).
    """
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    user = current_user()
    uf = file_service.save_inline_image(user, file.filename, file.read())
    return jsonify({"id": uf.id, "ref": file_service.inline_ref(uf),
                    "url": file_service.preview_url(uf, user),
                    "filename": uf.filename}), 201


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


@bp.get("/files/<int:file_id>/preview")
def preview_resource_file(file_id):
    """Перегляд зображення чи відео з вкладень — для тегів <img> і <video>.

    Без JWT: доступ дає підписане посилання зі списку вкладень (див.
    file_service). Права на картку перевіряються щоразу, тож знята з
    публікації картка за старим посиланням уже не відкриється. Відео
    віддається з підтримкою Range — його можна перемотувати, не
    завантажуючи цілком.
    """
    data = file_service.read_preview_token(request.args.get("sig"))
    if not data or data.get("f") != file_id:
        raise ApiError("Посилання на перегляд недійсне або застаріло", 403, "forbidden")
    uf = db.session.get(UserFile, file_id)
    viewer = db.session.get(User, data.get("u"))
    if uf is None:
        raise ApiError("Файл не знайдено", 404, "not_found")
    if viewer is None or not viewer.is_active:
        raise ApiError("Файл недоступний", 403, "forbidden")
    if uf.resource_id is None:
        # Картинка в ще не збереженому матеріалі — бачить лише той, хто вставив.
        if uf.source != "inline" or uf.user_id != viewer.id:
            raise ApiError("Файл недоступний", 403, "forbidden")
    else:
        res = db.session.get(CatalogResource, uf.resource_id)
        if res is None or not _can_see(res, viewer):
            raise ApiError("Файл недоступний", 403, "forbidden")
    media = file_service.media_of(uf)
    if media is None:
        raise ApiError("Для цього файлу немає перегляду", 415, "unsupported_media")
    resp = send_from_directory(file_service.user_dir(uf.user), uf.stored_name,
                               mimetype=media[1], conditional=True, max_age=3600)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    # Файл від користувача: браузер не вгадує тип, а відкритий напряму
    # документ нічого не виконує.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


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
@require_auth
def delete_resource(resource_id):
    """Видалення матеріалу автором або менеджером. Якщо видаляє менеджер
    чужий матеріал, автор отримує сповіщення з причиною (`note`)."""
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if not _may_edit(res, user):
        raise ApiError("Видалити матеріал може його автор або менеджер каталогу",
                       403, "forbidden")
    note = " ".join(((request.get_json(silent=True) or {}).get("note") or "").split()) or None
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
    # Сповіщення про видалений матеріал вели б у нікуди (ON DELETE CASCADE у
    # схемі є, але SQLite без перевірки зовнішніх ключів його не виконує).
    Notification.query.filter_by(resource_id=resource_id)\
        .delete(synchronize_session=False)
    SkillFeedback.query.filter_by(resource_id=resource_id).update(
        {SkillFeedback.resource_id: None}, synchronize_session=False)
    if not _is_author(res, user):
        review_service.notify_moderated("resource", res, user, "deleted", note,
                                        link=False)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Ресурс видалено"})


# ---------------------------- Коментарі й оцінки ----------------------------

@bp.get("/resources/<int:resource_id>/feedback")
@require_auth
def resource_comments(resource_id):
    """Публічна стрічка коментарів картки, середня оцінка й власна оцінка."""
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if not _can_see(res, user):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    return jsonify(comment_service.thread("resource", res.id, user))


@bp.post("/resources/<int:resource_id>/feedback")
@require_auth
def submit_resource_feedback(resource_id):
    """Коментар та/або оцінка матеріалу (BR-13). Коментар бачать усі; автор
    матеріалу отримує сповіщення."""
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    if not _can_see(res, user):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    fb = comment_service.add("resource", res, user, data.get("message"), data.get("rating"))
    db.session.commit()
    return jsonify(comment_service.public_dict(fb, user)), 201


@bp.delete("/feedback/<int:feedback_id>")
@require_auth
def delete_feedback(feedback_id):
    """Видалення коментаря (матеріалу чи навички): автор коментаря або менеджер."""
    fb = SkillFeedback.query.get_or_404(feedback_id)
    comment_service.remove(fb, current_user())
    db.session.commit()
    return jsonify({"message": "Коментар видалено"})


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
    out = {"strict_publish": strict_publish_enabled(),
           MATURITY_TEST_KEY: AppSetting.get(MATURITY_TEST_KEY, "") or ""}
    out.update({key: _survey_setting(key) for key in SURVEY_SETTINGS})
    return jsonify(out)


@bp.post("/settings")
@require_admin
def set_catalog_settings():
    """Зміна налаштувань. Частота опитувань керується звідси, а не з коду."""
    data = request.get_json(silent=True) or {}
    known = {"strict_publish", MATURITY_TEST_KEY, *SURVEY_SETTINGS}
    if not known & set(data):
        raise ApiError("Вкажіть щонайменше одне налаштування: "
                       + ", ".join(sorted(known)), 400, "validation_error")

    if "strict_publish" in data:
        AppSetting.set(STRICT_PUBLISH_KEY, "1" if data["strict_publish"] else "0")
    if MATURITY_TEST_KEY in data:
        url = (data.get(MATURITY_TEST_KEY) or "").strip()
        if url and not re.match(r"^https?://\S+$", url, re.I):
            raise ApiError("Посилання на тест має починатися з https:// або http://",
                           400, "validation_error")
        AppSetting.set(MATURITY_TEST_KEY, url)
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
@require_moderator
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
@require_moderator
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
@require_moderator
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
    # Скільки днів не питати нічого після будь-якого опитування: людина бачить
    # щонайбільше одне питання на місяць (фідбек тестування, п. 6).
    "survey_days_between": "30",
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
    гарантовано не отримати відповіді на жодне. Питає інтерфейс тоді, коли
    людина закінчує роботу: під час виходу або на початку наступного сеансу.
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
    last = max((p.shown_at for p in prompts.values()), default=None)
    if last is not None and now - last < timedelta(days=_survey_int("survey_days_between")):
        return jsonify({"due": None})

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
        user_role="manager" if access.is_moderator(user) else "user",
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
    query = CatalogResource.query.filter(
        moderation_service.visible_filter(CatalogResource, user, PUBLIC_STATUSES))
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
    """Словник формулювань задач. Адміністратор бачить і вимкнені."""
    query = SearchSynonym.query
    if not access.is_admin(current_user()):
        query = query.filter_by(is_active=True)
    rows = query.order_by(SearchSynonym.phrase).all()
    return jsonify([r.to_dict() for r in rows])


@bp.post("/synonyms")
@require_admin
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
@require_admin
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
@require_admin
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
@require_moderator
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
    """Користувач сам вказує свій рівень AI-зрілості та підрозділ.

    `tour_completed: true` — знайомство з інтерфейсом пройдено (або
    пропущено), більше не показувати; `false` — показати знову.
    """
    data = request.get_json(silent=True) or {}
    user = current_user()
    if "maturity_level_id" in data:
        level = _resolve_term("maturity", data.get("maturity_level_id"), "рівень зрілості")
        if level != user.maturity_level_id:
            user.maturity_assessed_at = datetime.utcnow() if level else None
        user.maturity_level_id = level
    if "department" in data:
        value = (data.get("department") or "").strip()
        user.department = value[:200] or None
    if "tour_completed" in data:
        user.tour_completed_at = datetime.utcnow() if data.get("tour_completed") else None
    db.session.commit()
    return jsonify({"maturity_level_id": user.maturity_level_id,
                    "department": user.department,
                    "tour_completed_at": (user.tour_completed_at.isoformat()
                                          if user.tour_completed_at else None)})


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
        "assessed_at": (user.maturity_assessed_at.isoformat()
                        if current is not None and user.maturity_assessed_at else None),
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


# ---------- Сповіщення та перегляд власного контенту (AKH-18) ----------

@bp.get("/notifications")
@require_auth
def list_notifications():
    """Сповіщення користувача, найновіші згори."""
    rows = (Notification.query.filter_by(user_id=current_user().id)
            .order_by(Notification.created_at.desc()).limit(50).all())
    return jsonify({"items": [n.to_dict() for n in rows],
                    "unread": sum(1 for n in rows if not n.is_read)})


@bp.post("/notifications/read")
@require_auth
def mark_notifications_read():
    """Позначає сповіщення прочитаними (усі або перелічені в `ids`)."""
    data = request.get_json(silent=True) or {}
    query = Notification.query.filter_by(user_id=current_user().id, is_read=False)
    ids = data.get("ids")
    if isinstance(ids, list) and ids:
        query = query.filter(Notification.id.in_(ids))
    query.update({Notification.is_read: True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/my-content")
@require_auth
def my_content():
    """Матеріали, за які відповідає користувач, з простроченим переглядом.

    Одне місце, з якого власник бачить усе своє й може одразу діяти, — інакше
    нагадування веде в нікуди.
    """
    user = current_user()
    names = {(user.full_name or "").casefold(), (user.username or "").casefold()}
    names.discard("")
    now = datetime.utcnow()

    rows = (CatalogResource.query
            .filter(CatalogResource.owner.isnot(None))
            .order_by(CatalogResource.next_review_at.asc().nullslast()).all())
    mine = [r for r in rows if (r.owner or "").strip().casefold() in names]

    def pack(r):
        due = r.next_review_at.date() if r.next_review_at else None
        return dict(r.to_dict(),
                    review_state=("overdue" if due and due < now.date()
                                  else "soon" if due and (due - now.date()).days <= 7
                                  else "ok"))
    items = [pack(r) for r in mine]
    return jsonify({
        "items": items,
        "overdue": sum(1 for i in items if i["review_state"] == "overdue"),
        "soon": sum(1 for i in items if i["review_state"] == "soon"),
        "email_digest": review_service.email_digest_enabled(user),
    })


@bp.post("/my-content/email-digest")
@require_auth
def toggle_email_digest():
    """Вмикає чи вимикає щотижневий підсумок на пошту."""
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        raise ApiError("Вкажіть enabled", 400, "validation_error")
    enabled = review_service.set_email_digest(current_user(), bool(data["enabled"]))
    return jsonify({"email_digest": enabled})


@bp.post("/resources/<int:resource_id>/confirm-review")
@require_auth
def confirm_review(resource_id):
    """Власник підтверджує, що матеріал актуальний, прямо зі списку.

    Дія пише в той самий журнал перегляду, що й зміна статусу: інакше в історії
    матеріалу з'явиться діра між «нагадали» і «щось сталося».
    """
    res = CatalogResource.query.get_or_404(resource_id)
    user = current_user()
    names = {(user.full_name or "").casefold(), (user.username or "").casefold()}
    if ((res.owner or "").strip().casefold() not in names
            and not access.can_moderate(user, res)):
        raise ApiError("Підтвердити актуальність може власник матеріалу або менеджер розділу",
                       403, "forbidden")

    data = request.get_json(silent=True) or {}
    try:
        months = int(data.get("months", 6))
    except (TypeError, ValueError):
        raise ApiError("Період має бути числом місяців", 400, "validation_error")
    months = max(1, min(months, 36))

    res.reviewed_at = datetime.utcnow()
    res.next_review_at = datetime.utcnow() + timedelta(days=30 * months)
    if res.status == "needs_update":
        res.status = "published"
    _log_review("resource", res, res.status, res.status,
                f"Актуальність підтверджено, наступний перегляд через {months} міс.")
    db.session.commit()
    return jsonify(res.to_dict())
