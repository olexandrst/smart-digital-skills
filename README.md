# Smart-ProfiHub — MVP

Внутрішня корпоративна AI-платформа: централізований доступ до моделей ШІ
(LLM/CV) у контурі Azure AI Foundry, рольова модель (RBAC), групи, каталог
скілів та базовий облік токенів.

Цей репозиторій містить **MVP веб-додатка**: REST API на Flask + простий
веб-інтерфейс (SPA) + мок інтеграції з Azure AI Foundry, що дозволяє
запустити систему без реального ключа.

> Повні вимоги — у [`PRD.md`](PRD.md), архітектура та схема БД —
> у [`Architecture.md`](Architecture.md).

---

## Стек

| Шар | Технологія |
| --- | --- |
| Backend | Python 3.11+, Flask 3, Blueprints |
| ORM | Flask-SQLAlchemy (SQLite, сумісно з PostgreSQL) |
| Авторизація | Flask-JWT-Extended (JWT access + refresh) |
| Паролі | Werkzeug (PBKDF2) |
| AI-інтеграція | Azure AI Foundry адаптер (у MVP — мок) |
| Frontend | Vanilla JS SPA (без збірки), сервиться Flask |

---

## Можливості MVP

- 🔐 Автентифікація логін/пароль із видачею JWT
- 👤 Менеджмент користувачів, ролей і скидання паролів (Admin)
- 👥 Групи, членство, ролі в групі, правило «мінімум 1 менеджер»
- 🧩 Каталог скілів із життєвим циклом `draft → testing → published → delisted`
- 🔗 Підключення моделей Azure AI Foundry (тільки Admin)
- ⚡ Подвійне призначення скілів: групове + самостійна активація, з коректним
  лічильником унікальних активацій
- 💬 Запуск скілів (чат із моделлю через мок Foundry)
- 📊 Базовий облік токенів: власний / по групі / глобальний

---

## Швидкий старт

### 1. Передумови
- Python **3.11+**
- `pip` та `venv`

### 2. Клонування та віртуальне середовище

```bash
git clone <repo-url>
cd smart-digital-skills

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Встановлення залежностей

```bash
pip install -r requirements.txt
```

### 4. Налаштування середовища

```bash
cp .env.example .env
```

За потреби відредагуйте `.env` (секрети, початковий пароль адміна).
Для MVP усе працює зі значеннями за замовчуванням, а Azure AI Foundry —
у режимі моку (`AZURE_FOUNDRY_MOCK=1`).

### 5. Ініціалізація бази та початкові дані

```bash
python -m scripts.init_db    # створює схему БД у instance/profihub.db
python -m scripts.seed       # наповнює демо-даними (ролі, користувачі, скіл)
```

### 6. Запуск

```bash
python wsgi.py
```

Відкрийте **http://localhost:5000** у браузері.

> Production-запуск: `gunicorn wsgi:app` (додайте `gunicorn` у середовище).

---

## Демо-облікові записи

Створюються скриптом `seed`:

| Роль | Логін | Пароль |
| --- | --- | --- |
| Адміністратор системи | `admin` | `Admin123!` |
| Skill-менеджер | `skillmanager` | `Skill123!` |
| Користувач (member) | `user1` | `User123!` |
| Користувач (member) | `user2` | `User123!` |

> Паролі адміна можна змінити через `ADMIN_USERNAME` / `ADMIN_PASSWORD` у `.env`
> **перед** запуском `seed`.

### Що подивитися після входу
- **admin** — усі вкладки: користувачі, моделі, групи, управління скілами, токени.
- **skillmanager** — створення/публікація скілів (без доступу до користувачів).
- **user1 / user2** — «Мої скіли» та «Каталог»: запуск Summarizer і самостійна
  активація скілів.

---

## Тестування

```bash
pip install pytest
python -m pytest -q
```

Покрито критичні бізнес-правила: автентифікація та скоуп прав, правило
«мінімум 1 менеджер у групі», активації скілів (груповий + self без подвійного
підрахунку).

---

## Структура проєкту

```
smart-digital-skills/
├── app/
│   ├── __init__.py            # фабрика застосунку create_app
│   ├── config.py              # конфіги Dev/Prod
│   ├── extensions.py          # db, jwt, cors
│   ├── models/                # SQLAlchemy моделі
│   ├── api/                   # Blueprints: auth, users, groups, skills, models, chat, usage
│   ├── services/              # бізнес-логіка (skill/group/chat)
│   ├── core/                  # security, permissions (RBAC), errors
│   ├── integrations/          # azure_foundry.py (мок + точка реальної інтеграції)
│   └── static/                # веб-інтерфейс (index.html, app.js, style.css)
├── scripts/
│   ├── init_db.py             # створення схеми
│   └── seed.py                # початкові дані
├── tests/                     # pytest
├── requirements.txt
├── .env.example
└── wsgi.py                    # точка входу
```

---

## Огляд REST API

База: `/api`. Усі захищені ендпоінти потребують заголовок
`Authorization: Bearer <access_token>`.

| Метод | Шлях | Опис | Доступ |
| --- | --- | --- | --- |
| POST | `/auth/login` | Вхід, видача JWT | всі |
| POST | `/auth/refresh` | Оновлення access-токена | refresh-токен |
| GET | `/auth/me` | Поточний користувач | авторизовані |
| GET/POST | `/users` | Список / створення користувачів | Admin |
| PATCH | `/users/{id}` | Оновлення | Admin |
| POST | `/users/{id}/reset-password` | Скидання пароля | Admin |
| GET/POST | `/models` | Реєстр / підключення моделей | GET: всі, POST: Admin |
| GET/POST | `/skills` | Каталог / створення скілів | створення: Admin/Skill Manager |
| GET | `/skills/mine` | Активні скіли користувача | авторизовані |
| POST | `/skills/{id}/status` | Зміна статусу (життєвий цикл) | Admin/Skill Manager |
| POST | `/skills/{id}/activate` | Самостійна активація | авторизовані |
| POST | `/skills/{id}/run` | Запуск скіла (чат) | авторизовані з доступом |
| GET/POST | `/groups` | Список / створення груп | створення: Admin |
| POST/DELETE | `/groups/{id}/members[/{uid}]` | Керування учасниками | Group Manager |
| POST/DELETE | `/groups/{id}/skills[/{sid}]` | Призначення скілів групі | Group Manager |
| GET | `/usage/me` | Власні токени | авторизовані |
| GET | `/usage/group/{id}` | Токени групи | Group Manager |
| GET | `/usage/global` | Глобальні токени | Admin |

---

## Підключення реального Azure AI Foundry

У MVP працює мок (`app/integrations/azure_foundry.py`). Щоб увімкнути реальну
інтеграцію:

1. Встановіть SDK: `pip install openai`.
2. У `.env` задайте `AZURE_FOUNDRY_ENDPOINT`, `AZURE_FOUNDRY_API_KEY` і
   `AZURE_FOUNDRY_MOCK=0`.
3. `deployment_name` у зареєстрованих моделях має відповідати деплойментам у Foundry.

---

## Наступні етапи (поза MVP)

- **Етап 2:** SSO через Entra ID (OIDC), детальні місячні ліміти токенів
  (`token_limits`) та аналітика/графіки. Поля під це вже закладені у схему.
- **iOS-застосунок:** той самий REST API.
- **Міграції:** для production рекомендовано перейти з `init_db` на Alembic
  (Flask-Migrate).
