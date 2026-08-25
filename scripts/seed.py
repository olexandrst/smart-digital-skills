"""Ідемпотентне наповнення початковими даними (п.8 архітектури).

Запуск: python -m scripts.seed
Створює: ролі, супер-адміна, демо-моделі, демо-скіл, демо-групу та демо-користувачів.
"""
import os
import json
from datetime import datetime
from app import create_app
from app.extensions import db
from app.core.security import hash_password
from app.models import (
    Role, User, UserRole, Group, GroupMembership,
    Model, Skill, SkillInput,
)
from app.services import skill_service


def _get_or_create(model, defaults=None, **kwargs):
    instance = model.query.filter_by(**kwargs).first()
    if instance:
        return instance, False
    params = dict(kwargs)
    params.update(defaults or {})
    instance = model(**params)
    db.session.add(instance)
    db.session.flush()
    return instance, True


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()

        # 1. Ролі
        admin_role, _ = _get_or_create(
            Role, code="admin",
            defaults={"name": "Адміністратор системи",
                      "description": "Повний контроль над системою"})
        sm_role, _ = _get_or_create(
            Role, code="skill_manager",
            defaults={"name": "Skill-менеджер",
                      "description": "Управління скілами"})

        # 2. Супер-адмін
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin123!")
        admin, created = _get_or_create(
            User, username=admin_username,
            defaults={
                "full_name": "Системний адміністратор",
                "email": "admin@profihub.local",
                "password_hash": hash_password(admin_password),
                "is_system_admin": True,
                "is_active": True,
            })
        if created:
            db.session.add(UserRole(user_id=admin.id, role_id=admin_role.id))

        # 3. Демо Skill-менеджер
        sm_user, sm_created = _get_or_create(
            User, username="skillmanager",
            defaults={
                "full_name": "Демо Skill-менеджер",
                "email": "skill@profihub.local",
                "password_hash": hash_password("Skill123!"),
                "is_active": True,
            })
        if sm_created:
            db.session.add(UserRole(user_id=sm_user.id, role_id=sm_role.id))

        # 4. Демо-користувачі (мембери)
        member1, _ = _get_or_create(
            User, username="user1",
            defaults={"full_name": "Олена Мембер",
                      "email": "user1@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})
        member2, _ = _get_or_create(
            User, username="user2",
            defaults={"full_name": "Іван Мембер",
                      "email": "user2@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})

        # 5. Реєстр моделей — ПОРОЖНІЙ на першому запуску.
        # Користувач додає власні моделі (Azure OpenAI або локальні
        # Ollama/LM Studio) у вкладці «Моделі».

        # 6. Демо-скіл Summarizer (published). Модель НЕ прив'язуємо — її обере
        # користувач після додавання власної.
        skill, skill_created = _get_or_create(
            Skill, name="Summarizer",
            defaults={
                "description": "Стисло підсумовує наданий текст.",
                "author": "Smart Digital Skills",
                "category": "Текст",
                "prompt_template": "Зроби стислий підсумок тексту мовою {language}:\n\n{text}",
                "parameters": json.dumps({"temperature": 0.3}),
                "input_spec": "Будь-який текст (стаття, лист, нотатки) та, опційно, "
                              "мова відповіді.",
                "output_spec": "Стислий підсумок наданого тексту в кілька речень.",
                "starter_prompt": "Підсумуй, будь ласка, цей текст українською:",
                "status": "published",
                "published_at": datetime.utcnow(),
                "version": "1.0.0",
                "created_by": admin.id,
            })
        if skill_created:
            db.session.add(SkillInput(
                skill_id=skill.id, name="text", label="Текст",
                data_type="string", is_required=True,
                description="Текст для опрацювання", position=0))
            db.session.add(SkillInput(
                skill_id=skill.id, name="language", label="Мова відповіді",
                data_type="string", is_required=False,
                default_value="українською", position=1))

        # 7. Демо-група з адміном-менеджером (правило безперервності управління)
        group, group_created = _get_or_create(
            Group, name="Default Department",
            defaults={"description": "Демонстраційний відділ",
                      "created_by": admin.id})
        if group_created:
            db.session.add(GroupMembership(
                group_id=group.id, user_id=admin.id,
                role="member", status="active"))
            db.session.add(GroupMembership(
                group_id=group.id, user_id=member1.id,
                role="member", status="active"))
        db.session.commit()

        # 8. Призначення демо-скіла групі + самостійна активація для member2
        skill_service.assign_to_group(group.id, skill.id, admin.id)
        skill_service.self_activate(member2.id, skill.id)

        # 9. Демо-скіл-ПАКЕТ (виконання Python-коду з архіву)
        _seed_package_skill(admin)

        # 10. Розділи каталогу (дзеркало меню корпоративного AI Knowledge Hub)
        sections = _seed_catalog_sections()

        # 11. Наповнення каталогу: промпти, інструкції, кейси, агенти, посилання
        _seed_catalog_resources(admin, sections)

        print("✓ Seed завершено.")
        print(f"  Адмін:          {admin_username} / {admin_password}")
        print("  Skill-менеджер: skillmanager / Skill123!")
        print("  Користувачі:    user1 / User123!,  user2 / User123!")


def _zip_dir_to_bytes(src_dir):
    """Пакує вміст теки у zip-байти (шляхи відносні до src_dir)."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, src_dir)
                zf.write(full, rel)
    return buf.getvalue()


def _seed_package_skill(admin):
    """Збирає приклад examples/skills/word-counter у архів та реєструє як скіл-пакет."""
    from datetime import datetime
    from app.services import package_service
    from app.models import SkillInput

    if Skill.query.filter_by(name="Word Counter").first():
        return

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    example_dir = os.path.join(base_dir, "examples", "skills", "word-counter")
    if not os.path.isdir(example_dir):
        print("  (приклад word-counter не знайдено — пропускаю скіл-пакет)")
        return

    file_bytes = _zip_dir_to_bytes(example_dir)
    meta = package_service.parse_package(file_bytes)

    skill = Skill(
        name=meta["name"], description=meta["description"],
        author=meta.get("author") or "Smart Digital Skills",
        category=meta.get("category") or "Загальне",
        skill_kind="package", runtime=meta["runtime"], entrypoint=meta["entrypoint"],
        version=meta["version"], prompt_template=meta.get("instructions"),
        status="published", published_at=datetime.utcnow(),
        package_filename="word-counter.zip", created_by=admin.id,
    )
    db.session.add(skill)
    db.session.flush()
    skill.package_path = package_service.store_package(skill.id, file_bytes, "word-counter.zip")
    for spec in meta["inputs"]:
        db.session.add(SkillInput(
            skill_id=skill.id, name=spec["name"], label=spec.get("label"),
            data_type=spec.get("data_type", "string"),
            is_required=spec.get("is_required", True),
            default_value=spec.get("default_value"),
            description=spec.get("description"), position=spec.get("position", 0)))
    db.session.commit()


# Розділи каталогу — дзеркало меню корпоративного AI Knowledge Hub.
# `url` заповнено там, де розділ поки живе у зовнішньому порталі (гібридний режим):
# плитка веде туди. Коли контент переїде до нас — посилання прибирається.
CATALOG_SECTIONS = [
    {"name": "Про проєкт «AI Culture Acceleration»", "icon_emoji": "🚀", "accent": "red",
     "description": "Мета, етапи та команда програми розвитку AI-культури.",
     "url": "/portal/ai-culture-acceleration"},
    {"name": "Toolbox", "icon_emoji": "🧰", "accent": "ink",
     "description": "Доступні ШІ-інструменти компанії та умови їх використання."},
    {"name": "FAQ", "icon_emoji": "❓", "accent": "steel",
     "description": "Часті питання та відповіді про роботу зі штучним інтелектом."},
    {"name": "Power Links", "icon_emoji": "🔗", "accent": "green",
     "description": "Корисні зовнішні сервіси та внутрішні ресурси."},
    {"name": "Insight Center", "icon_emoji": "💡", "accent": "amber",
     "description": "Лайфхаки та практики від AI-партнерів."},
    {"name": "Learning Space", "icon_emoji": "🎓", "accent": "steel",
     "description": "Навчальні матеріали, самостійне навчання та вебінари."},
    {"name": "AI Use Policy & Safety", "icon_emoji": "🔐", "accent": "red",
     "description": "Політики, правила та безпека використання ШІ."},
    {"name": "AI Partners Network", "icon_emoji": "🤝", "accent": "ink",
     "description": "Спільнота AI-партнерів у Viva Engage.",
     "url": "https://web.yammer.com/main/groups/ai-partners-network"},
    {"name": "Help & Feedback", "icon_emoji": "🆘", "accent": "amber",
     "description": "Технічна підтримка та зворотний зв'язок щодо хабу.",
     "url": "/portal/ai-help-feedback"},
]


def _seed_catalog_sections():
    """Створює розділи каталогу (ідемпотентно). Повертає мапу назва → id."""
    from app.models import CatalogSection

    created = 0
    for position, spec in enumerate(CATALOG_SECTIONS):
        section = CatalogSection.query.filter_by(name=spec["name"]).first()
        if section is None:
            db.session.add(CatalogSection(**spec, position=position))
            created += 1
    if created:
        db.session.commit()
        print(f"  Каталог: додано розділів — {created}")
    return {s.name: s.id for s in CatalogSection.query.all()}


CATALOG_DEMO = [
    {
        "resource_type": "prompt", "name": "Аналіз тендерної документації",
        "section": "Insight Center", "owner": "Марина Кондратенко",
        "description": "Розбирає ТД на вимоги, строки та ризики й повертає структурований чекліст.",
        "category": "Закупівлі", "tags": "аналітика, документи",
        "icon_emoji": "📑", "is_featured": True,
        "body": ("Ти — досвідчений фахівець із закупівель.\n"
                 "Проаналізуй наведену тендерну документацію та поверни:\n"
                 "1. Ключові вимоги до учасника.\n"
                 "2. Строки та етапи.\n"
                 "3. Ризики й неоднозначні формулювання.\n"
                 "4. Чекліст документів для подання.\n\n"
                 "Документація:\n\"\"\"\n{{текст}}\n\"\"\""),
    },
    {
        "resource_type": "prompt", "name": "Протокол наради за стенограмою",
        "section": "Insight Center", "owner": "Марина Кондратенко",
        "description": "Перетворює розшифровку зустрічі на протокол із рішеннями та задачами.",
        "category": "Операційна робота", "tags": "наради, підсумки",
        "icon_emoji": "🗒️",
        "body": ("Склади протокол наради за стенограмою нижче.\n"
                 "Формат: Порядок денний → Обговорення → Рішення → Задачі "
                 "(відповідальний, дедлайн).\n\nСтенограма:\n\"\"\"\n{{текст}}\n\"\"\""),
    },
    {
        "resource_type": "instruction", "name": "Як писати ефективні промпти",
        "section": "Learning Space", "owner": "Катерина Сумарєва",
        "description": "Базові правила формулювання завдань для LLM: роль, контекст, формат, приклади.",
        "category": "Навчання", "tags": "prompt engineering, основи",
        "icon_emoji": "🎓", "is_featured": True,
        "body": ("## Чотири складові якісного промпту\n\n"
                 "1. **Роль** — ким має бути модель: «Ти — фінансовий аналітик…».\n"
                 "2. **Контекст** — дані, обмеження, аудиторія результату.\n"
                 "3. **Задача** — одна чітка дія: проаналізуй, порівняй, склади.\n"
                 "4. **Формат** — таблиця, список, JSON, довжина відповіді.\n\n"
                 "### Що покращує результат\n\n"
                 "- Один-два приклади бажаної відповіді.\n"
                 "- Явна заборона вигадувати факти.\n"
                 "- Розбиття складного завдання на кроки.\n\n"
                 "### Типові помилки\n\n"
                 "- Занадто загальне формулювання («напиши щось про…»).\n"
                 "- Кілька різних задач в одному запиті.\n"
                 "- Відсутність критеріїв якості результату."),
    },
    {
        "resource_type": "instruction", "name": "Робота з конфіденційними даними в ШІ",
        "section": "AI Use Policy & Safety", "owner": "Дмитро Кирєєв",
        "description": "Що можна і що не можна передавати в моделі; правила знеособлення.",
        "category": "Безпека", "tags": "політика, безпека",
        "icon_emoji": "🔐",
        "body": ("## Що не передаємо в зовнішні моделі\n\n"
                 "- Персональні дані працівників і контрагентів.\n"
                 "- Комерційну таємницю, ціни діючих контрактів.\n"
                 "- Облікові дані та ключі доступу.\n\n"
                 "## Як працювати безпечно\n\n"
                 "1. Знеособлюйте дані перед запитом (заміна імен на «Контрагент А»).\n"
                 "2. Для чутливих задач використовуйте внутрішні моделі.\n"
                 "3. Не зберігайте відповіді моделі поза корпоративними системами."),
    },
    {
        "resource_type": "case", "name": "Кейс: автоматизація обробки заявок у HR",
        "section": "Insight Center", "owner": "Марина Кондратенко",
        "description": "Як HR скоротив час опрацювання типових звернень удвічі за допомогою агента.",
        "category": "HR", "tags": "кейс, автоматизація", "icon_emoji": "📈",
        "reuse_level": "adaptable", "tools": "Copilot Studio, SharePoint",
        "body": ("## Задача\n\nHR отримував ~400 типових звернень на місяць; "
                 "кожне опрацьовувалось вручну до 15 хвилин.\n\n"
                 "## Рішення\n\nАгент на Copilot Studio класифікує звернення, "
                 "відповідає на типові з бази знань і ескалює складні на людину.\n\n"
                 "## Результат\n\n- Час опрацювання: 15 → 7 хвилин.\n"
                 "- 62% звернень закриваються без участі людини.\n"
                 "- Окупність — третій місяць експлуатації.\n\n"
                 "## Як повторити\n\nШаблон агента й базу знань можна адаптувати "
                 "під будь-який підрозділ із типовим потоком звернень."),
    },
    {
        "resource_type": "agent", "name": "Асистент технічної підтримки",
        "section": "Toolbox", "owner": "Антон Іщенко",
        "description": "Агент першої лінії: класифікує звернення та пропонує рішення з бази знань.",
        "category": "Підтримка", "tags": "звернення, база знань",
        "icon_emoji": "🤖", "url": "https://agents.example.com/support-assistant",
        "link_scope": "external",
    },
    {
        "resource_type": "agent", "name": "Агент аналітики виробництва",
        "section": "Toolbox", "owner": "Антон Іщенко",
        "description": "Відповідає на запитання щодо показників зміни та формує добові зведення.",
        "category": "Виробництво", "icon_emoji": "🏭",
        "url": "https://agents.example.com/production-analytics",
        "link_scope": "external", "is_featured": True,
    },
    {
        "resource_type": "link", "name": "Портал знань Metinvest Digital",
        "section": "Power Links", "owner": "Ольга Островерхова",
        "description": "Внутрішня база регламентів, шаблонів і навчальних матеріалів.",
        "category": "Внутрішні ресурси", "icon_emoji": "🏛️",
        "url": "/portal/knowledge", "link_scope": "internal",
    },
    {
        "resource_type": "link", "name": "Hugging Face",
        "section": "Power Links", "owner": "Ольга Островерхова",
        "description": "Каталог відкритих моделей, датасетів і демо-застосунків.",
        "category": "Зовнішні сервіси", "tags": "моделі, ML",
        "icon_emoji": "🤗", "url": "https://huggingface.co", "link_scope": "external",
    },
    {
        "resource_type": "link", "name": "Заявка на доступ до моделі",
        "section": "Help & Feedback", "owner": "Стародубцева Ольга",
        "description": "Внутрішня форма запиту доступу до корпоративних LLM та квоти.",
        "category": "Внутрішні ресурси", "icon_emoji": "📨",
        "url": "/portal/ai-access-request", "link_scope": "internal",
    },
]


def _seed_catalog_resources(admin, sections):
    """Демо-наповнення каталогу: промпти, інструкції, кейси, агенти, посилання."""
    from datetime import datetime, timedelta
    from app.models import CatalogResource

    now = datetime.utcnow()
    created = 0
    for spec in CATALOG_DEMO:
        if CatalogResource.query.filter_by(name=spec["name"]).first():
            continue
        fields = dict(spec)
        section_name = fields.pop("section", None)
        db.session.add(CatalogResource(
            **fields, section_id=sections.get(section_name),
            status="published", published_at=now,
            reviewed_at=now, next_review_at=now + timedelta(days=180),
            author="Metinvest Digital", created_by=admin.id))
        created += 1
    if created:
        db.session.commit()
        print(f"  Каталог: додано ресурсів — {created}")

    # Навички теж належать до розділу — щоб вісь працювала для всього каталогу.
    toolbox_id = sections.get("Toolbox")
    if toolbox_id:
        unassigned = Skill.query.filter_by(section_id=None).all()
        for skill in unassigned:
            skill.section_id = toolbox_id
            skill.owner = skill.owner or "Антон Іщенко"
        if unassigned:
            db.session.commit()


if __name__ == "__main__":
    seed()
