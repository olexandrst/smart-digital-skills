# Розгортання AI Knowledge Hub на Azure App Service (Linux, Python)

Застосунок уже готовий до App Service — у коді нічого міняти не треба:

- **дані** (SQLite, файли користувачів, пакети й іконки навичок) автоматично
  кладуться в `/home/data`. `/home` на App Service — постійне сховище, що
  переживає рестарти й деплої; код у `/home/site/wwwroot` кожен деплой
  перезаписує, тож дані свідомо винесені поза нього;
- **режим журналу SQLite** — `DELETE`, а не WAL: `/home` це мережева шара
  Azure Files, а WAL на мережевих ФС не працює (потребує спільної пам'яті) і
  дає `disk I/O error`. Очікування зайнятої бази — 30 секунд, бо на SMB
  блокування знімаються не миттєво;
- **`startup.sh`** сам застосовує міграції, за потреби наповнює порожню базу
  початковими даними й запускає gunicorn рівно з **одним воркером** × 4 потоки;
- **робота за проксі**: Azure термінує TLS на фронті, застосунок довіряє
  `X-Forwarded-Proto` і будує зовнішні посилання з правильною схемою.

App Service визначається за змінною `WEBSITE_SITE_NAME`, яку платформа задає
сама. Будь-що з наведеного можна перевизначити явно (`INSTANCE_DIR`,
`FLASK_ENV`, `SQLITE_JOURNAL_MODE`, `GUNICORN_WORKERS`).

## Три правила, які не можна порушувати

| Правило | Чому |
|---|---|
| **Рівно 1 екземпляр** (без Scale out / Autoscale) | Два екземпляри — це дві копії файлу SQLite на мережевій шарі й два незалежні планувальники. Гарантована розбіжність даних |
| **Startup Command = `bash startup.sh`** | Без неї Azure запускає (2 × ядра) + 1 воркерів gunicorn. SQLite не тримає паралельний запис із кількох процесів — отримаєте `database is locked` і пошкоджену базу |
| **Always On = On** (план B1 або вищий) | Без нього App Service вивантажує застосунок після ~20 хв тиші, і разом із ним зупиняється планувальник тижневого скидання квот та щоденних нагадувань власникам |

Безкоштовний план **F1 не підходить**: у ньому немає Always On і діє ліміт
60 хв процесорного часу на добу. Мінімум — **B1** (Basic).

---

## Варіант А. Через портал Azure + GitHub (рекомендовано)

### Крок 1. Створити Web App

1. [portal.azure.com](https://portal.azure.com) → **Create a resource** → **Web App**.
2. Вкладка **Basics**:
   - **Subscription / Resource Group** — ваші; групу можна створити нову, напр. `ai-hub-rg`;
   - **Name** — унікальне ім'я, напр. `ai-knowledge-hub` → адреса буде
     `https://ai-knowledge-hub.azurewebsites.net`;
   - **Publish** — `Code`;
   - **Runtime stack** — `Python 3.11`;
   - **Operating System** — `Linux`;
   - **Region** — найближчий до користувачів, напр. `Poland Central` або `West Europe`;
   - **Pricing plan** — новий, **Basic B1** або вищий (не Free F1).
3. **Review + create** → **Create**. Дочекайтеся завершення й відкрийте ресурс.

### Крок 2. Змінні середовища

**Settings → Environment variables → App settings → + Add**:

| Name | Value |
|---|---|
| `SECRET_KEY` | довгий випадковий рядок — `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JWT_SECRET_KEY` | **інший** такий самий рядок |
| `ADMIN_USERNAME` | логін першого адміністратора, напр. `hubadmin` |
| `ADMIN_PASSWORD` | його пароль |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` — **обов'язково**: Azure встановить залежності з `requirements.txt`, інакше `startup.sh` не знайде gunicorn |
| `LLM_TIMEOUT` | `200` — Azure обриває HTTP-запит приблизно на 230-й секунді |
| `SKILL_EXEC_ENABLED` | `0` — вимикає виконання Python-коду зі скілів-пакетів. Поставте `1`, лише якщо ця функція потрібна |
| `WEBSITE_TIME_ZONE` | `Europe/Kyiv` — час у логах |

`SECRET_KEY` і `JWT_SECRET_KEY` мають бути **сталими**: якщо вони зміняться,
усіх користувачів розлогінить.

Для реальних моделей додайте `LLM_MOCK=0`, `AZURE_FOUNDRY_ENDPOINT` і
`AZURE_FOUNDRY_API_KEY`. Без них застосунок працює в демо-режимі
(`LLM_MOCK=1` за замовчуванням) — каталог, пошук і аналітика повноцінні,
мокуються лише відповіді моделей.

**Не задавайте** `WEBSITES_ENABLE_APP_SERVICE_STORAGE=false` — він вимикає
постійне `/home`, і дані зникатимуть. **Не вмикайте**
`WEBSITE_RUN_FROM_PACKAGE` — він монтує застосунок тільки для читання, і той
не підніметься.

`INSTANCE_DIR` задавати не треба: на App Service він сам стає `/home/data`.

Натисніть **Apply** → **Confirm**.

### Крок 3. Команда запуску й Always On

**Settings → Configuration → General settings**:

- **Startup Command** — `bash startup.sh`
- **Always On** — `On`
- **HTTPS Only** — `On`
- **Session affinity** — можна `Off` (екземпляр один)

**Save** → **Continue**.

### Крок 4. Перевірка стану (необов'язково, але корисно)

**Monitoring → Health check** → **Enable**, **Path** = `/api/health` → **Save**.
Azure перезапустить інстанс, якщо застосунок перестане відповідати.

### Крок 5. Підключити код із GitHub

1. **Deployment → Deployment Center**.
2. **Source** — `GitHub` → **Authorize** (увійти в GitHub).
3. **Organization** — `olexandrst`, **Repository** — `smart-digital-skills`,
   **Branch** — гілка, з якої деплоїти (напр. `main` після злиття або
   `claude/skills-catalog-redesign-vcvian`).
4. **Authentication type** — `User-assigned identity` (якщо спитає).
5. **Save**.

Azure додасть у репозиторій файл `.github/workflows/…yml` окремим комітом і
запустить перший деплой. **Зробіть `git pull`** — інакше ваш наступний push
відхилиться. Далі кожен push у цю гілку деплоїться автоматично; хід — у
GitHub → **Actions** або в Deployment Center → **Logs**. Перший білд триває
кілька хвилин.

<details>
<summary>Якщо деплой довгий або падає на розмірі артефакту</summary>

Згенерований Azure workflow створює віртуальне середовище в репозиторії й
пакує його в артефакт разом із кодом. Оскільки залежності однаково
встановлюються на сервері (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`), ці кроки
зайві. Приберіть із `.github/workflows/…yml` блок:

```yaml
      - name: Create and start virtual environment
        run: |
          python -m venv venv
          source venv/bin/activate

      - name: Install dependencies
        run: pip install -r requirements.txt
```

Артефакт стане в рази меншим, а деплой — швидшим.
</details>

### Крок 6. Перший вхід

Відкрийте **Monitoring → Log stream** і дочекайтеся:

```
[startup] Міграції та довідники…
INFO  [alembic.runtime.migration] Running upgrade  -> d7f976c479ef, baseline schema
✓ Міграції застосовано
✓ Довідники синхронізовано: sqlite:////home/data/profihub.db
[startup] Початкові дані (лише якщо база порожня)…
✓ Seed завершено.
  Адмін:          hubadmin / <ваш пароль>
[startup] gunicorn на порту 8000
[INFO] Booting worker with pid: …
```

Відкрийте `https://<ім'я>.azurewebsites.net`, увійдіть під адміністратором із
кроку 2.

Початкові дані наповнюються **лише тоді, коли база порожня** — вимикати щось
після першого запуску не потрібно. Видалені демо-користувачі не повернуться
при наступному деплої.

> **Демо-користувачі** `skillmanager`, `user1`, `user2` створюються з паролями
> за замовчуванням зі `scripts/seed.py`. Перед реальним запуском видаліть їх
> або змініть паролі в розділі **Керування → Користувачі**.

### Крок 7. Переконатися, що дані постійні

У розділі **Керування → Матеріали** створіть тестовий матеріал, потім
**Overview → Restart**. Після рестарту матеріал має лишитися на місці.

Це підтверджує, що база лежить у `/home/data`, а не в тимчасовій ФС. Якщо
дані зникли — хтось задав `INSTANCE_DIR` усередині `/home/site/wwwroot` або
вимкнув `WEBSITES_ENABLE_APP_SERVICE_STORAGE`.

---

## Варіант Б. Через Azure CLI

Замініть `<NAME>` на унікальне ім'я застосунку.

```bash
az login

az group create -n ai-hub-rg -l polandcentral
az appservice plan create -g ai-hub-rg -n ai-hub-plan --is-linux --sku B1
az webapp create -g ai-hub-rg -p ai-hub-plan -n <NAME> --runtime "PYTHON|3.11"

az webapp config set -g ai-hub-rg -n <NAME> \
    --startup-file "bash startup.sh" --always-on true
az webapp update -g ai-hub-rg -n <NAME> --https-only true
az webapp config set -g ai-hub-rg -n <NAME> \
    --generic-configurations '{"healthCheckPath": "/api/health"}'

az webapp config appsettings set -g ai-hub-rg -n <NAME> --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    ADMIN_USERNAME="hubadmin" \
    ADMIN_PASSWORD="<пароль>" \
    LLM_TIMEOUT=200 \
    SKILL_EXEC_ENABLED=0 \
    WEBSITE_TIME_ZONE=Europe/Kyiv

# підключити GitHub (замість ручного деплою)
az webapp deployment source config -g ai-hub-rg -n <NAME> \
    --repo-url https://github.com/olexandrst/smart-digital-skills \
    --branch main --git-token <GitHub PAT>

# або разовий деплой поточного коміту архівом (з кореня репозиторію)
git archive --format zip -o hub.zip HEAD
az webapp deploy -g ai-hub-rg -n <NAME> --src-path hub.zip --type zip
```

---

## Експлуатація

**Логи.** **Monitoring → Log stream** — живий потік логів gunicorn і
застосунку. Якщо порожньо: **Monitoring → App Service logs →
Application logging → File System** → **Save**.

**Оновлення.** Push у підключену гілку → автоматичний деплой. Міграції
застосуються самі при старті нового контейнера; окремий крок не потрібен.
База в `/home/data` не зачіпається, контент і користувачі зберігаються.
Після деплою перевірте в Log stream рядок `✓ Міграції застосовано`.

**Резервна копія бази.** **Development Tools → SSH → Go**:

```bash
cd /home/site/wwwroot
python -m scripts.backup_db
# ✓ Копію збережено: /home/data/backups/profihub-2026-09-30.db (0.3 МБ)
```

Скрипт користується онлайн-бекапом SQLite. **Не копіюйте базу через `cp`**:
на працюючому застосунку звичайне копіювання дає знімок посеред транзакції,
і копія може виявитися пошкодженою — а дізнаєтеся ви про це тоді, коли вона
знадобиться.

Забрати копію (і теку `user_files/` з файлами користувачів) можна через
**Advanced Tools (Kudu) → Go → Debug console → Bash** → `/home/data`.
Додатково варто ввімкнути штатні **Backups** App Service — вони знімають
усю `/home`.

**Ліміти.** Фронтенд Azure обриває HTTP-запит через ~230 с, і цей ліміт не
налаштовується. Тому `LLM_TIMEOUT=200`: інакше модель ще думає, а користувач
уже бачить помилку.

**Корпоративний вхід** через Microsoft Entra ID — див.
[останній розділ](#вхід-через-microsoft-entra-id).

---

## Типові проблеми

| Симптом | Причина → рішення |
|---|---|
| «Application Error» одразу після деплою | Не встановились залежності → перевірте `SCM_DO_BUILD_DURING_DEPLOYMENT=true` і передеплойте; деталі — у Log stream |
| У логах `exec: gunicorn: not found` | Те саме: без збірки на сервері залежностей немає |
| `startup.sh: Permission denied` | Startup Command задано як `startup.sh` → має бути `bash startup.sh` |
| Дані зникли після деплою | Хтось задав `INSTANCE_DIR` усередині `/home/site/wwwroot` або `WEBSITES_ENABLE_APP_SERVICE_STORAGE=false` → приберіть |
| Застосунок не стартує, помилка запису | Увімкнено `WEBSITE_RUN_FROM_PACKAGE` → приберіть змінну |
| `database is locked` у логах | Кілька воркерів або екземплярів → Startup Command `bash startup.sh`, Scale out = 1; за потреби підніміть `SQLITE_BUSY_TIMEOUT` |
| `disk I/O error` у логах | Майже завжди — увімкнений WAL на `/home` → переконайтеся, що `SQLITE_JOURNAL_MODE` не задано в `WAL` |
| Застосунок «засинає» вночі, квоти не скидаються | Вимкнено Always On або план Free → увімкніть Always On (B1+) |
| Усіх розлогінило | Змінився `JWT_SECRET_KEY` → задайте сталий в App settings |
| 504 на довгих відповідях моделі | Ліміт Azure ~230 с → зменште `LLM_TIMEOUT` |
| Демо-користувачі повернулися після деплою | Задано `SEED_ON_START=1` → приберіть змінну (типове значення `auto` наповнює лише порожню базу) |
| Вхід через Entra: помилка `redirect_uri` | `ENTRA_REDIRECT_URI` не заданий або не збігається зі зареєстрованим у Entra |
| Push у GitHub відхиляється після підключення | Azure додав workflow-файл окремим комітом → `git pull` |

---

## Межі цієї конфігурації

Це конфігурація для **пілота**, і межі краще знати наперед.

### Один воркер, один екземпляр

SQLite не тримає паралельний запис із кількох **процесів**, а два екземпляри
App Service працювали б із різними копіями файлу на шарі. Вертикальне
масштабування (більший план) — можна, воно не додає процесів. Горизонтальне —
ні. Обмеження знімається переїздом на PostgreSQL.

### Налаштування SQLite і чому вони такі

| Налаштування | Значення | Чому |
|---|---|---|
| `SQLITE_JOURNAL_MODE` | `DELETE` | WAL потребує спільної пам'яті між процесами, якої CIFS не надає |
| `SQLITE_SYNCHRONOUS` | `FULL` | `NORMAL` безпечний лише разом із WAL |
| `SQLITE_BUSY_TIMEOUT` | `30` (секунд) | Без таймаута запит падає з «database is locked» замість того, щоб зачекати |

Якщо запис здаватиметься повільним, `SQLITE_SYNCHRONOUS=NORMAL` його
прискорить. Ціна: при аварійному падінні хоста можна втратити кілька
останніх транзакцій. Для пілота це прийнятний обмін, для бойової системи — ні.

### Відома проблема схеми

Сім зв'язків оголошені без правила `ON DELETE`:
`ideas.source_resource_id`, `ideas.resource_id`,
`learning_path_steps.resource_id`, `user_files.resource_id`,
`chat_sessions.group_id`, `user_skills.group_id`, `token_usage_logs.group_id`.

SQLite за замовчуванням перевірку зовнішніх ключів не виконує, тому зараз це
ні на що не впливає — видалення матеріалів і груп працює. Але:

- `SQLITE_FOREIGN_KEYS=1` зламає ці видалення (`FOREIGN KEY constraint failed`);
- **на PostgreSQL перевірка увімкнена завжди й вимкнути її не можна** — тому
  зв'язки треба полагодити міграцією (`SET NULL` там, де запис має пережити
  батька) **до** переїзду.

### Коли переростете: PostgreSQL

Ознаки: більше кількох десятків активних користувачів, потреба в
масштабуванні або кількох воркерах.

1. Полагодити сім зв'язків із попереднього пункту (окрема міграція).
2. Створити **Azure Database for PostgreSQL — Flexible Server**.
3. Додати `psycopg2-binary` у `requirements.txt`.
4. Задати `DATABASE_URL=postgresql+psycopg2://<user>:<pass>@<host>/<db>?sslmode=require`.
   Налаштування `SQLITE_*` при цьому не застосовуються — вони діють лише для SQLite.
5. Перевірити `relax_not_null()` у `app/core/schema.py`: вона написана під
   SQLite (перебудова таблиці через перейменування). На PostgreSQL потрібен
   звичайний `ALTER TABLE … ALTER COLUMN … DROP NOT NULL`.
6. Перенести дані й підняти `GUNICORN_WORKERS` (змінна середовища, код міняти
   не треба).

---

## Вхід через Microsoft Entra ID

Корпоративний вхід вмикається змінними середовища — код міняти не треба.
Поки `ENTRA_TENANT_ID` або `ENTRA_CLIENT_ID` порожні, застосунок працює на
парольному вході.

### Реєстрація застосунку в Entra ID

1. **Azure Portal → Microsoft Entra ID → App registrations → New registration.**
2. **Redirect URI:** тип *Web*, значення
   `https://<ім'я>.azurewebsites.net/api/auth/entra/callback`.
3. **Token configuration** → додайте claim **groups** (або **roles**, якщо
   використовуєте App Roles) — без нього ролі за групами не працюватимуть.
4. **Certificates & secrets** → створіть client secret, якщо не використовуєте
   PKCE без секрету (застосунок підтримує обидва варіанти).
5. Скопіюйте **Directory (tenant) ID** і **Application (client) ID**.

### Змінні середовища

| Змінна | Приклад | Призначення |
| --- | --- | --- |
| `ENTRA_TENANT_ID` | `72f988bf-…` | Directory (tenant) ID |
| `ENTRA_CLIENT_ID` | `a1b2c3d4-…` | Application (client) ID |
| `ENTRA_CLIENT_SECRET` | *(необов'язково)* | Client secret, якщо застосунок його вимагає |
| `ENTRA_REDIRECT_URI` | `https://<ім'я>.azurewebsites.net/api/auth/entra/callback` | Має **точно** збігатися зі зареєстрованим |
| `ENTRA_ADMIN_GROUPS` | `<object id групи>` | Групи, що дають роль **Admin** (через кому) |
| `ENTRA_MANAGER_GROUPS` | `<object id>,<object id>` | Групи, що дають роль **Skill Manager** |
| `ENTRA_ALLOW_PASSWORD_LOGIN` | `1` / `0` | `0` вимикає вхід за паролем повністю |

### Що відбувається при вході

Користувач створюється при першому вході за даними каталогу (ім'я, пошта,
підрозділ). **Ролі перераховуються щоразу**: членство в групі каталогу —
єдине джерело правди, тому видалення з групи знімає доступ при наступному
вході. Якщо співробітник раніше заходив за паролем, обліковий запис
прив'язується за поштою, а не дублюється.

Вихід через `/api/auth/entra/logout` завершує й корпоративну сесію.

> **Перед вимкненням парольного входу** (`ENTRA_ALLOW_PASSWORD_LOGIN=0`)
> переконайтеся, що хоча б один адміністратор входить через каталог — інакше
> в застосунок не зайде ніхто.
