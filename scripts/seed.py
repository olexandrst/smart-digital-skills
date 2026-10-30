"""Ідемпотентне наповнення початковими даними (п.8 архітектури).

Запуск: python -m scripts.seed
Створює: ролі, супер-адміна, демо-користувачів, демо-навички, розділи,
колекції, довідники, демо-матеріали каталогу та навчальні маршрути.
"""
import os
from datetime import datetime
from app import create_app
from app.extensions import db
from app.core.security import hash_password
from app.models import Role, User, UserRole, Skill, CatalogSection


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
            defaults={"name": "Адміністратор",
                      "description": "Повний контроль над системою й користувачами"})
        sm_role, _ = _get_or_create(
            Role, code="section_manager",
            defaults={"name": "Менеджер розділу",
                      "description": "Модерація матеріалів і навичок призначених розділів"})

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

        # 3. Демо-менеджер розділу (розділи призначаються нижче, після їх створення)
        sm_user, sm_created = _get_or_create(
            User, username="skillmanager",
            defaults={
                "full_name": "Демо менеджер розділу",
                "email": "skill@profihub.local",
                "password_hash": hash_password("Skill123!"),
                "is_active": True,
            })
        if sm_created:
            db.session.add(UserRole(user_id=sm_user.id, role_id=sm_role.id))

        # 4. Демо-користувачі
        _get_or_create(
            User, username="user1",
            defaults={"full_name": "Олена Мембер",
                      "email": "user1@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})
        _get_or_create(
            User, username="user2",
            defaults={"full_name": "Іван Мембер",
                      "email": "user2@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})

        # 5. Демо-навичка без пакета — лише інструкції, які копіюють у свій
        # інструмент. Показує, як виглядає навичка-«рецепт» у каталозі.
        _get_or_create(
            Skill, name="Summarizer",
            defaults={
                "description": "Стисло підсумовує наданий текст.",
                "author": "Metinvest Digital",
                "category": "Текст",
                "prompt_template": "Зроби стислий підсумок тексту мовою {language}:\n\n{text}",
                "input_spec": "Будь-який текст (стаття, лист, нотатки) та, опційно, "
                              "мова відповіді.",
                "output_spec": "Стислий підсумок наданого тексту в кілька речень.",
                "starter_prompt": "Підсумуй, будь ласка, цей текст українською:",
                "status": "published",
                "published_at": datetime.utcnow(),
                "version": "1.0.0",
                "created_by": admin.id,
            })
        db.session.commit()

        # 6. Демо-навичка-пакет (архів зі skill.md, який завантажують із каталогу)
        _seed_package_skill(admin)

        # 7. Розділи каталогу (дзеркало меню корпоративного AI Knowledge Hub)
        sections = _seed_catalog_sections()

        # Демо-менеджер веде два розділи — так видно, що права обмежені ними.
        if sm_created:
            sm_user.managed_sections = [CatalogSection.query.get(sections[name])
                                        for name in ("Toolbox", "Insight Center")
                                        if name in sections]
            db.session.commit()

        # 8. Колекції та довідники метаданих
        folders = _seed_catalog_folders(sections)
        from app.core.schema import seed_catalog_terms
        seed_catalog_terms()

        # 9. Наповнення каталогу: промпти, інструкції, кейси, агенти, посилання
        _seed_catalog_resources(admin, sections, folders)

        # 10. Навчальні маршрути поверх наявних карток
        _seed_learning_paths()

        print("✓ Seed завершено.")
        print(f"  Адмін:          {admin_username} / {admin_password}")
        print("  Менеджер розділів Toolbox і Insight Center: skillmanager / Skill123!")
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
    """Збирає приклад examples/skills/word-counter в архів і реєструє як навичку-пакет."""
    from app.services import package_service

    if Skill.query.filter_by(name="Word Counter").first():
        return

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    example_dir = os.path.join(base_dir, "examples", "skills", "word-counter")
    if not os.path.isdir(example_dir):
        print("  (приклад word-counter не знайдено — пропускаю навичку-пакет)")
        return

    file_bytes = _zip_dir_to_bytes(example_dir)
    meta = package_service.parse_package(file_bytes)

    skill = Skill(
        name=meta["name"], description=meta["description"],
        author=meta.get("author") or "Metinvest Digital",
        category=meta.get("category") or "Загальне",
        skill_kind="package", version=meta["version"],
        prompt_template=meta.get("instructions"),
        status="published", published_at=datetime.utcnow(),
        package_filename="word-counter.zip", created_by=admin.id,
    )
    db.session.add(skill)
    db.session.flush()
    skill.package_path = package_service.store_package(skill.id, file_bytes)
    db.session.commit()


# Розділи каталогу — дзеркало меню корпоративного AI Knowledge Hub.
# `url` заповнено там, де розділ поки живе у зовнішньому порталі (гібридний режим):
# плитка веде туди. Коли контент переїде до нас — посилання прибирається.
# Три зовнішні розділи колеги вирішили поки не показувати (30.10.2026):
# вони приховані, а повернути їх можна в «Керування → Структура каталогу».
CATALOG_SECTIONS = [
    {"name": "Про проєкт «AI Culture Acceleration»", "icon_emoji": "🚀", "accent": "red",
     "description": "Мета, етапи та команда програми розвитку AI-культури.",
     "url": "/portal/ai-culture-acceleration", "is_active": False},
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
     "url": "https://web.yammer.com/main/groups/ai-partners-network", "is_active": False},
    {"name": "AI Community", "icon_emoji": "☕", "accent": "green",
     "description": "AI Hub Weekly та зустрічі спільноти: "
                    "розклад, записи та матеріали заходів."},
    {"name": "Help & Feedback", "icon_emoji": "🆘", "accent": "amber",
     "description": "Технічна підтримка та зворотний зв'язок щодо хабу.",
     "url": "/portal/ai-help-feedback", "is_active": False},
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


# Типові колекції з BRD (BR-04) та розділ, у якому кожна живе.
CATALOG_FOLDERS = [
    ("Toolbox", "Created Agents", "Готові агенти, створені в компанії", "🤖"),
    ("Toolbox", "Practical Skills", "Навички щоденної роботи з AI", "🎯"),
    ("Toolbox", "AI Solutions", "Впроваджені AI-рішення та сервіси", "⚙️"),
    ("Insight Center", "Templates & Prompts", "Шаблони документів і перевірені промпти", "📝"),
    ("Insight Center", "Use Cases", "Кейси застосування AI у бізнес-процесах", "💼"),
    ("Learning Space", "Cataloging Knowledge", "Правила ведення бази знань і метадані", "🗂️"),
    ("AI Community", "AI Hub Weekly", "Щотижневий дайджест новин хабу", "📰"),
]


def _seed_catalog_folders(sections):
    """Створює типові колекції (ідемпотентно). Повертає мапу назва → id."""
    from app.models import CatalogFolder

    created = 0
    for position, (section_name, name, description, emoji) in enumerate(CATALOG_FOLDERS):
        section_id = sections.get(section_name)
        if section_id is None:
            continue
        if CatalogFolder.query.filter_by(section_id=section_id, name=name).first():
            continue
        db.session.add(CatalogFolder(
            section_id=section_id, name=name, description=description,
            icon_emoji=emoji, position=position))
        created += 1
    if created:
        db.session.commit()
        print(f"  Каталог: додано колекцій — {created}")
    return {f.name: f.id for f in CatalogFolder.query.all()}


# Куди складати демо-матеріали: (розділ, вид матеріалу) → колекція.
# Частина матеріалів навмисно лишається без колекції — вони мають бути
# доступні на рівні розділу, а не зникати з каталогу.
DEMO_FOLDER_BY_TYPE = {
    ("Insight Center", "prompt"): "Templates & Prompts",
    ("Insight Center", "case"): "Use Cases",
    ("Toolbox", "agent"): "Created Agents",
    ("Toolbox", "mcp"): "AI Solutions",
    ("AI Community", "instruction"): "AI Hub Weekly",
}

# Рівні AI-зрілості, для яких призначено матеріал (за видом).
DEMO_MATURITY_BY_TYPE = {
    "prompt": ["User"],
    "instruction": ["Aware", "User"],
    "case": ["Integrator", "Innovator"],
    "agent": ["User", "Integrator"],
    "mcp": ["Innovator", "Transformer"],
    "link": ["Aware"],
}

# Демо-маршрути: назва, опис, емодзі, рівень, стартовий, кроки за назвами карток.
LEARNING_PATHS = [
    {
        "name": "Старт з AI за тиждень", "icon_emoji": "🚀", "is_starter": True,
        "maturity": "Aware",
        "description": "Мінімум, щоб почати користуватися AI у щоденній роботі.",
        "steps": [
            ("Як писати ефективні промпти", "Прочитайте і спробуйте на власному завданні."),
            ("Робота з конфіденційними даними в ШІ", "Звірте свої дані з правилами."),
            ("Протокол наради за стенограмою", "Застосуйте до найближчої наради."),
        ],
    },
    {
        "name": "Від користувача до інтегратора", "icon_emoji": "⚙️",
        "maturity": "Integrator",
        "description": "Як перейти від готових рішень до вбудовування AI у свої процеси.",
        "steps": [
            ("Кейс: автоматизація обробки заявок у HR", "Розберіть, як влаштовано рішення."),
            ("Асистент технічної підтримки", "Подивіться готового агента в дії."),
            ("MCP: Корпоративна база знань", "Підключіть сервер до свого клієнта."),
            ("Заявка на доступ до моделі", "Оформіть доступ під власний пілот."),
        ],
    },
]

# Демо-значення керованих довідників за видом матеріалу.
DEMO_TERMS_BY_TYPE = {
    "prompt": ("Базовий", "Економія часу"),
    "instruction": ("Базовий", "Якість рішень"),
    "case": ("Середній", "Масштабування досвіду"),
    "agent": ("Середній", "Економія часу"),
    "mcp": ("Просунутий", "Нова можливість"),
    "link": ("Базовий", "Якість рішень"),
}


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
        "resource_type": "mcp", "name": "MCP: Корпоративна база знань",
        "section": "Toolbox", "owner": "Антон Іщенко",
        "description": "Дає агентам доступ до регламентів, шаблонів і документації компанії.",
        "category": "Внутрішні ресурси", "tags": "MCP, база знань",
        "icon_emoji": "🔌", "url": "https://mcp.metinvest.local/knowledge/sse",
        "link_scope": "internal", "tools": "Copilot Studio, Claude Desktop",
        "reuse_level": "ready",
        "body": ("## Що надає сервер\n\n"
                 "- `search_documents` — пошук по регламентах і шаблонах.\n"
                 "- `get_document` — повний текст документа за ідентифікатором.\n"
                 "- `list_policies` — перелік чинних політик компанії.\n\n"
                 "## Як підключити\n\n"
                 "1. Скопіюйте endpoint кнопкою «Копіювати».\n"
                 "2. У Copilot Studio: **Tools → Add tool → Model Context Protocol** "
                 "і вставте адресу.\n"
                 "3. Авторизація — через корпоративний обліковий запис, окремий "
                 "ключ не потрібен.\n\n"
                 "Доступ лише з внутрішньої мережі або через VPN."),
    },
    {
        "resource_type": "mcp", "name": "MCP: GitHub",
        "section": "Toolbox", "owner": "Антон Іщенко",
        "description": "Робота з репозиторіями, issue та pull request прямо з агента.",
        "category": "Зовнішні сервіси", "tags": "MCP, розробка",
        "icon_emoji": "🐙", "url": "https://api.githubcopilot.com/mcp/",
        "link_scope": "external", "tools": "Copilot Studio, Claude Desktop, VS Code",
        "reuse_level": "ready",
        "body": ("## Що надає сервер\n\n"
                 "Публічний MCP-сервер GitHub: читання репозиторіїв, пошук по коду, "
                 "робота з issue та pull request.\n\n"
                 "## Як підключити\n\n"
                 "1. Скопіюйте endpoint кнопкою «Копіювати».\n"
                 "2. Додайте його у свого MCP-клієнта.\n"
                 "3. Потрібен персональний токен GitHub із мінімально достатніми "
                 "правами.\n\n"
                 "> Перед підключенням до робочих репозиторіїв узгодьте з "
                 "Governance & Security."),
    },
    {
        # AI Coffee — тимчасовий формат лише для AI-партнерів, тож у базі знань
        # його немає (фідбек тестування, п. 7).
        "resource_type": "instruction", "name": "AI Hub Weekly: щотижневий дайджест хабу",
        "section": "AI Community", "owner": "Катерина Сумарєва",
        "owner_contact": "ai-community@metinvest.example",
        "description": "Що виходить у щотижневому дайджесті хабу і як на нього підписатися.",
        "category": "Спільнота", "tags": "спільнота, навчання",
        "icon_emoji": "📰",
        "body": ("## Що таке AI Hub Weekly\n\n"
                 "Щотижневий дайджест: нові матеріали хабу, кейси колег і "
                 "найкорисніші питання з каналу спільноти.\n\n"
                 "### Як підписатися\n\n"
                 "1. Відкрийте канал спільноти.\n"
                 "2. Увімкніть сповіщення про нові дописи.\n"
                 "3. Хочете, щоб у дайджест потрапив ваш кейс — напишіть "
                 "організаторам."),
    },
    {
        "resource_type": "case", "name": "Кейс: автоматизація обробки заявок у HR",
        "section": "Insight Center", "owner": "Марина Кондратенко",
        "description": "Як HR скоротив час опрацювання типових звернень удвічі за допомогою агента.",
        "category": "HR", "tags": "кейс, автоматизація", "icon_emoji": "📈",
        "reuse_level": "adaptable", "tools": "Copilot Studio, SharePoint",
        "owner_contact": "hr-automation@metinvest.example",
        "reuse_guidance": (
            "### Передумови\n\n"
            "- Потік однотипних звернень від 100 на місяць.\n"
            "- Наявна база знань або регламент, з якого агент братиме відповіді.\n"
            "- Погоджений перелік звернень, які **не** можна закривати без людини.\n\n"
            "### Кроки\n\n"
            "1. Зберіть 200–300 реальних звернень і розмітьте їх за типами.\n"
            "2. Попросіть шаблон агента у власника цього кейсу.\n"
            "3. Замініть базу знань на свою, лишивши структуру відповідей.\n"
            "4. Два тижні тримайте агента в режимі підказки оператору.\n"
            "5. Відкрийте автовідповідь лише для типів із точністю понад 90%.\n\n"
            "### До кого звертатися\n\n"
            "Власник кейсу — за шаблоном агента; команда хабу — за доступами."),
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


def _seed_catalog_resources(admin, sections, folders):
    """Демо-наповнення каталогу: промпти, інструкції, кейси, агенти, посилання."""
    from datetime import datetime, timedelta
    from app.models import CatalogResource, CatalogTerm, CatalogResourceMaturity
    from app.core.schema import migrate_legacy_tags

    term_ids = {(t.kind, t.name): t.id for t in CatalogTerm.query.all()}
    now = datetime.utcnow()
    created = 0
    for spec in CATALOG_DEMO:
        if CatalogResource.query.filter_by(name=spec["name"]).first():
            continue
        fields = dict(spec)
        section_name = fields.pop("section", None)
        rtype = fields["resource_type"]
        complexity, value = DEMO_TERMS_BY_TYPE.get(rtype, (None, None))
        maturity = DEMO_MATURITY_BY_TYPE.get(rtype, [])
        resource = CatalogResource(
            **fields, section_id=sections.get(section_name),
            folder_id=folders.get(DEMO_FOLDER_BY_TYPE.get((section_name, rtype))),
            complexity_id=term_ids.get(("complexity", complexity)),
            business_value_id=term_ids.get(("business_value", value)),
            status="published", published_at=now,
            reviewed_at=now, next_review_at=now + timedelta(days=180),
            author="Metinvest Digital", created_by=admin.id)
        db.session.add(resource)
        db.session.flush()
        for level in maturity:
            term_id = term_ids.get(("maturity", level))
            if term_id:
                db.session.add(CatalogResourceMaturity(resource_id=resource.id,
                                                       term_id=term_id))
        created += 1
    if created:
        db.session.commit()
        print(f"  Каталог: додано ресурсів — {created}")

    # Теги демо-матеріалів задані рядком через кому — тим самим переносом,
    # що й для старих баз, розкладаємо їх у керований довідник.
    migrate_legacy_tags()

    # Навички теж належать до розділу й колекції — щоб обидві осі навігації
    # працювали для всього каталогу, а не лише для ресурсів.
    toolbox_id = sections.get("Toolbox")
    if toolbox_id:
        unassigned = Skill.query.filter_by(section_id=None).all()
        for skill in unassigned:
            skill.section_id = toolbox_id
            skill.owner = skill.owner or "Антон Іщенко"
        if unassigned:
            db.session.commit()
    skills_folder = folders.get("Practical Skills")
    if skills_folder:
        homeless = Skill.query.filter_by(folder_id=None, section_id=toolbox_id).all()
        for skill in homeless:
            skill.folder_id = skills_folder
        if homeless:
            db.session.commit()

def _seed_learning_paths():
    """Демо-маршрути з кроків, що посилаються на вже створені картки."""
    from app.models import (
        LearningPath, LearningPathStep, CatalogResource, CatalogTerm,
    )

    levels = {t.name: t.id for t in CatalogTerm.query.filter_by(kind="maturity")}
    created = 0
    for position, spec in enumerate(LEARNING_PATHS):
        if LearningPath.query.filter_by(name=spec["name"]).first():
            continue
        path = LearningPath(
            name=spec["name"], description=spec["description"],
            icon_emoji=spec["icon_emoji"], position=position,
            is_starter=spec.get("is_starter", False),
            maturity_level_id=levels.get(spec.get("maturity")),
        )
        db.session.add(path)
        db.session.flush()
        step_index = 0
        for resource_name, note in spec["steps"]:
            resource = CatalogResource.query.filter_by(name=resource_name).first()
            if resource is None:
                continue      # картки немає — крок просто не створюємо
            db.session.add(LearningPathStep(
                path_id=path.id, position=step_index,
                resource_id=resource.id, note=note))
            step_index += 1
        created += 1
    if created:
        db.session.commit()
        print(f"  Каталог: додано навчальних маршрутів — {created}")


def _base_is_empty():
    """Чи немає в базі жодного користувача.

    Ознака першого запуску. Потрібна для безперервного розгортання: контейнер
    перезапускається на кожен push, і беззастережний seed щоразу повертав би
    демо-користувачів, яких адміністратор перед тим видалив.
    """
    app = create_app()
    with app.app_context():
        return User.query.count() == 0


if __name__ == "__main__":
    import sys
    if "--if-empty" in sys.argv and not _base_is_empty():
        print("· База вже наповнена — початкові дані пропущено")
    else:
        seed()
