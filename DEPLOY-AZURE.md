# Розгортання AI Knowledge Hub на Azure App Service (SQLite)

Покрокова інструкція для **пілота**: один інстанс Linux App Service, база
**SQLite** у персистентному сховищі `/home`. Розділ
[«Межі цієї конфігурації»](#межі-цієї-конфігурації) чесно пояснює, де вона
закінчується.

Орієнтовний час: **25–35 хвилин**, з них 5 хвилин чекання на першу збірку.

> **Перед початком прочитайте одне.** `/home` в App Service — це не локальний
> диск, а мережева шара **Azure Files (SMB)**. SQLite на мережевій ФС працює,
> але з умовами: один процес, без WAL, з великим таймаутом очікування. Усе це
> вже налаштовано в коді й у `startup.sh` — але **не змінюйте кількість
> воркерів і не масштабуйте інстанси**, інакше отримаєте пошкоджену базу.
> Деталі — у розділі [«Чому саме так»](#чому-саме-так).

---

## Зміст

1. [Передумови](#крок-0-передумови)
2. [Підготувати архів](#крок-1-підготувати-архів)
3. [Створити App Service](#крок-2-створити-app-service)
4. [Змінні середовища](#крок-3-змінні-середовища)
5. [Команда запуску](#крок-4-команда-запуску)
6. [Залити застосунок](#крок-5-залити-застосунок)
7. [Перший запуск: міграції та початкові дані](#крок-6-перший-запуск)
8. [Перевірити](#крок-7-перевірити)
9. [Закрити пілот від зайвих очей](#крок-8-закрити-пілот-від-зайвих-очей)
10. [Резервне копіювання](#резервне-копіювання)
11. [Оновлення на нову версію](#оновлення-на-нову-версію)
12. [Діагностика](#діагностика)
13. [Межі цієї конфігурації](#межі-цієї-конфігурації)
14. [Вхід через Microsoft Entra ID](#вхід-через-microsoft-entra-id)

---

## Крок 0. Передумови

- Підписка Azure і роль **Contributor** на групі ресурсів.
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli):
  `az login`. Кожен крок має альтернативу через портал, якщо CLI недоступний.
- Архів проєкту: на GitHub кнопка **Code → Download ZIP** на потрібній гілці.

---

## Крок 1. Підготувати архів

> **Найчастіша помилка розгортання.** ZIP із GitHub містить **вкладену теку**
> `smart-digital-skills-<гілка>/`, а застосунок уже всередині неї. Azure
> очікує `requirements.txt` і `wsgi.py` **у корені архіву**. Якщо залити ZIP як
> є, збірка не знайде залежностей — і ви отримаєте «Application Error» без
> зрозумілої причини.

Розпакуйте архів і перепакуйте **його вміст**, а не саму теку.

**Windows PowerShell:**

```powershell
Expand-Archive .\smart-digital-skills-main.zip -DestinationPath .\unpacked
Compress-Archive -Path .\unpacked\smart-digital-skills-main\* -DestinationPath .\app.zip -Force
```

**macOS / Linux:**

```bash
unzip smart-digital-skills-main.zip
cd smart-digital-skills-main
zip -r ../app.zip . -x '*.git*' '*__pycache__*' '*.pytest_cache*' 'instance/*'
cd ..
```

Перевірте, що в корені `app.zip` лежать `requirements.txt`, `wsgi.py`,
`startup.sh`, `alembic.ini` і теки `app/`, `migrations/`, `scripts/`.

---

## Крок 2. Створити App Service

```bash
RG=rg-ai-knowledge-hub          # група ресурсів
APP=ai-knowledge-hub            # має бути унікальним у *.azurewebsites.net
LOC=westeurope

az group create --name $RG --location $LOC

az appservice plan create \
  --name plan-$APP --resource-group $RG \
  --location $LOC --is-linux --sku B1

az webapp create \
  --name $APP --resource-group $RG \
  --plan plan-$APP --runtime "PYTHON:3.11"
```

**Через портал:** *Create a resource → Web App*; **Publish** — Code,
**Runtime stack** — Python 3.11, **Operating System** — Linux.

Увімкніть **Always On** — без нього застосунок засинає, а разом із ним
зупиняється планувальник тижневого скидання квот і щоденних нагадувань:

```bash
az webapp config set --name $APP --resource-group $RG --always-on true
```

> **Не беріть план F1 (Free).** На ньому немає Always On, а застосунок
> вивантажується з пам'яті після 20 хвилин без трафіку. Мінімум — **B1**.

---

## Крок 3. Змінні середовища

### Обов'язкові

| Змінна | Значення | Навіщо |
|---|---|---|
| `INSTANCE_DIR` | `/home/data` | **Найважливіша.** Переносить базу, файли користувачів, пакети й іконки навичок у персистентне сховище. Без неї все це лежатиме в `/home/site/wwwroot/instance` і зникне при наступному деплої |
| `SECRET_KEY` | випадковий рядок ≥ 32 символи | Підпис сесій Flask |
| `JWT_SECRET_KEY` | **інший** випадковий рядок ≥ 32 символи | Підпис токенів доступу |
| `FLASK_ENV` | `production` | Вимикає debug, вмикає кеш статики й довіру до `X-Forwarded-Proto` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `1` | Каже Azure встановити залежності з `requirements.txt` |

Згенерувати секрети:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Рекомендовані

| Змінна | Значення | Коментар |
|---|---|---|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | власні | Обліковий запис адміністратора, який створить `seed`. **Задайте до кроку 6** |
| `SEED_ON_START` | `1` на перший запуск, потім `0` | Створює ролі, адміністратора й демо-каталог. Лишати `1` не можна: видалені демо-користувачі поверталися б після кожного рестарту |
| `LLM_MOCK` | `1` або `0` | `1` — демо без ключів моделей; `0` — реальні виклики |
| `LLM_TIMEOUT` | `200` | Azure обриває HTTP-запит приблизно на 230-й секунді; типове значення застосунку (1800 с) більше за цей ліміт |
| `SKILL_EXEC_ENABLED` | `0` | Вимикає виконання Python-коду зі скілів-пакетів. Лишайте `1`, лише якщо ця функція справді потрібна |
| `WEBSITE_TIME_ZONE` | `Europe/Kyiv` | Час у логах. Планувальник усередині працює за UTC незалежно від цього |

Для реальних моделей (`LLM_MOCK=0`) додайте `AZURE_FOUNDRY_ENDPOINT` і
`AZURE_FOUNDRY_API_KEY`. Повний перелік змінних — у `.env.example`.

### Одна команда

```bash
az webapp config appsettings set --name $APP --resource-group $RG --settings \
  INSTANCE_DIR=/home/data \
  SECRET_KEY="<згенерований>" \
  JWT_SECRET_KEY="<інший згенерований>" \
  FLASK_ENV=production \
  SCM_DO_BUILD_DURING_DEPLOYMENT=1 \
  ADMIN_USERNAME=hubadmin \
  ADMIN_PASSWORD="<надійний пароль>" \
  SEED_ON_START=1 \
  LLM_MOCK=1 \
  LLM_TIMEOUT=200 \
  SKILL_EXEC_ENABLED=0 \
  WEBSITE_TIME_ZONE=Europe/Kyiv
```

**Через портал:** *Settings → Environment variables → App settings*.

> Секрети правильніше тримати в **Azure Key Vault** і підключати через
> Key Vault reference, а не значенням у налаштуваннях.

---

## Крок 4. Команда запуску

```bash
az webapp config set --name $APP --resource-group $RG \
  --startup-file "bash startup.sh"
```

**Через портал:** *Settings → Configuration → General settings →
Startup Command* → `bash startup.sh`.

> Саме `bash startup.sh`, а не `startup.sh`. Якщо ви пакували архів у Windows,
> `Compress-Archive` **не зберігає біт виконання**, і скрипт, запущений
> напряму, впаде з `Permission denied`. Виклик через `bash` працює в обох
> випадках.

`startup.sh` робить три речі по черзі: застосовує міграції, за потреби
наповнює базу початковими даними, запускає gunicorn з одним воркером.
Чому так — у [«Чому саме так»](#чому-саме-так).

---

## Крок 5. Залити застосунок

```bash
az webapp deploy --name $APP --resource-group $RG --src-path app.zip --type zip
```

**Через портал:** Kudu — `https://<APP>.scm.azurewebsites.net/ZipDeployUI` —
перетягніть `app.zip` у вікно.

Перша збірка триває **3–5 хвилин**: Azure встановлює залежності.

> **Не вмикайте `WEBSITE_RUN_FROM_PACKAGE`.** Ця опція монтує застосунок
> тільки для читання, а він створює робочі теки при старті — і не підніметься.
> Якщо змінна вже є в налаштуваннях, приберіть її.

---

## Крок 6. Перший запуск

Нічого робити не потрібно: `startup.sh` сам застосував міграції та, якщо ви
поставили `SEED_ON_START=1`, створив початкові дані. Подивіться лог:

```bash
az webapp log tail --name $APP --resource-group $RG
```

Очікуваний вивід:

```
[startup] Міграції та довідники…
INFO  [alembic.runtime.migration] Running upgrade  -> d7f976c479ef, baseline schema
✓ Міграції застосовано
✓ Довідники синхронізовано: sqlite:////home/data/profihub.db
[startup] Початкові дані…
✓ Seed завершено.
  Адмін:          hubadmin / <ваш пароль>
[startup] gunicorn на порту 8000
[INFO] Booting worker with pid: …
```

**Одразу після цього вимкніть seed**, інакше видалені демо-користувачі
повертатимуться при кожному рестарті:

```bash
az webapp config appsettings set --name $APP --resource-group $RG \
  --settings SEED_ON_START=0
```

<details>
<summary>Якщо волієте робити це вручну</summary>

Поставте `RUN_MIGRATIONS_ON_START=0` і `SEED_ON_START=0`, а потім:

```bash
az webapp ssh --name $APP --resource-group $RG
cd /home/site/wwwroot
python -m scripts.init_db     # міграції + довідники
python -m scripts.seed        # ролі, адміністратор, демо-каталог
```

Обидва скрипти ідемпотентні. `init_db` сам розрізняє чисту базу
(`alembic upgrade head`) і базу, створену до переходу на міграції (доганяє
колонки старим способом і ставить штамп версії).
</details>

> **Демо-користувачі** `skillmanager`, `user1`, `user2` створюються з паролями
> за замовчуванням зі `scripts/seed.py`. Перед реальним запуском видаліть їх
> або змініть паролі у вкладці «Керування → Користувачі».

---

## Крок 7. Перевірити

```bash
curl https://$APP.azurewebsites.net/api/health
# {"service":"smart-profihub","status":"ok"}
```

Далі у браузері `https://<APP>.azurewebsites.net`:

1. Увійдіть під адміністратором.
2. **База знань** — видно розділи в бічній панелі й матеріали.
3. **Керування → Матеріали** — створіть тестовий матеріал.
4. **Головна перевірка.** Перезапустіть застосунок і переконайтеся, що
   матеріал **лишився**:

```bash
az webapp restart --name $APP --resource-group $RG
```

Крок 4 найважливіший. Якщо після перезапуску дані зникли — `INSTANCE_DIR` не
застосувався, і все пішло в теку, яку деплой перезаписує. Перевірте написання
змінної й повторіть.

Переконатися, що база справді там, де треба:

```bash
az webapp ssh --name $APP --resource-group $RG
ls -la /home/data           # має бути profihub.db і теки user_files, skill_packages…
```

---

## Крок 8. Закрити пілот від зайвих очей

```bash
# Тільки HTTPS
az webapp update --name $APP --resource-group $RG --https-only true

# Сучасний TLS
az webapp config set --name $APP --resource-group $RG --min-tls-version 1.2

```

**Health check** (Azure перезапустить інстанс, якщо застосунок «ліг») зручніше
ввімкнути в порталі: *Monitoring → Health check → Enable*, шлях `/api/health`.
Через CLI те саме робиться так, але перевірте результат у порталі — синтаксис
цієї опції залежить від версії CLI:

```bash
az webapp config set --name $APP --resource-group $RG \
  --generic-configurations '{"healthCheckPath": "/api/health"}'
```

Якщо пілот тільки для внутрішніх користувачів, обмежте доступ за IP:
*Networking → Access restrictions* у порталі.

---

## Резервне копіювання

Уся база — **один файл** `/home/data/profihub.db`. Це і зручність, і ризик.

**Не копіюйте його через `cp`.** На працюючому застосунку звичайне копіювання
дає знімок посеред транзакції: копія може виявитися пошкодженою, і дізнаєтеся
ви про це тоді, коли вона знадобиться.

У репозиторії є скрипт, який робить узгоджену копію онлайн-бекапом SQLite:

```bash
az webapp ssh --name $APP --resource-group $RG
cd /home/site/wwwroot
python -m scripts.backup_db
# ✓ Копію збережено: /home/data/backups/profihub-2026-09-30.db (0.3 МБ)
```

Забрати копію на свою машину — через Kudu:
`https://<APP>.scm.azurewebsites.net` → *Debug console → Bash* → перейти в
`/home/data/backups` і натиснути значок завантаження біля файлу.

Додатково увімкніть штатні **Backups** App Service (*Settings → Backups*) —
вони знімають усю `/home`, включно з файлами користувачів.

---

## Оновлення на нову версію

```bash
# 1. Копія перед змінами
az webapp ssh --name $APP --resource-group $RG
cd /home/site/wwwroot && python -m scripts.backup_db && exit

# 2. Новий архів (крок 1) і деплой
az webapp deploy --name $APP --resource-group $RG --src-path app.zip --type zip
```

Міграції застосуються самі при старті нового контейнера — окремий крок не
потрібен. `/home/data` деплой не перезаписує, тож контент і користувачі
зберігаються. Довідники синхронізуються при старті: це дані, а не схема.

Після оновлення перевірте лог (`az webapp log tail`): рядок
`✓ Міграції застосовано` має бути там.

---

## Діагностика

**Живі логи:**

```bash
az webapp log tail --name $APP --resource-group $RG
```

**Файли логів і консоль:** `https://<APP>.scm.azurewebsites.net`.

| Симптом | Найімовірніша причина |
|---|---|
| «Application Error» одразу після деплою | Архів залитий разом із вкладеною текою (крок 1), або не задано `SCM_DO_BUILD_DURING_DEPLOYMENT=1` |
| У логах `exec: gunicorn: not found` | Залежності не встановились — див. попередній пункт, перезалийте |
| `startup.sh: Permission denied` | Startup-команда задана як `startup.sh`; має бути `bash startup.sh` |
| Застосунок не стартує, помилка запису | Увімкнено `WEBSITE_RUN_FROM_PACKAGE` — приберіть змінну |
| Після редеплою зник увесь контент | `INSTANCE_DIR` не задано або задано з помилкою |
| `database is locked` у логах | Хтось збільшив кількість воркерів або інстансів. Поверніть один воркер і один інстанс; за потреби підніміть `SQLITE_BUSY_TIMEOUT` |
| `disk I/O error` у логах | Майже завжди — увімкнений WAL на `/home`. Перевірте, що `SQLITE_JOURNAL_MODE` не задано в `WAL` |
| 504 на довгих відповідях моделі | Ліміт Azure ~230 с; зменште `LLM_TIMEOUT` |
| Вхід не працює після рестарту | Змінився `JWT_SECRET_KEY` — старі токени недійсні. Очікувано, увійдіть заново |
| Вхід через Entra повертає помилку redirect_uri | `ENTRA_REDIRECT_URI` не заданий або не збігається із зареєстрованим у Entra |
| Застосунок «засинає» вночі | Не ввімкнено Always On (крок 2) |

---

## Межі цієї конфігурації

Це конфігурація для **пілота**. Її межі краще знати наперед, ніж з'ясувати
під навантаженням.

### Чому саме так

`/home` в App Service — мережева шара **Azure Files (SMB)**, а не локальний
диск. З цього випливають три налаштування, які вже зроблено в коді:

| Налаштування | Значення | Чому |
|---|---|---|
| Журнал SQLite | `DELETE` (не WAL) | WAL потребує спільної пам'яті між процесами, якої CIFS не надає. На мережевій шарі WAL дає `disk I/O error` і ризик пошкодження |
| `synchronous` | `FULL` | `NORMAL` безпечний лише разом із WAL. Без WAL він означає ризик втратити останні транзакції при збої хоста |
| Очікування блокування | 30 секунд | Блокування на SMB знімається не миттєво. Без таймаута запит падає з «database is locked» замість того, щоб зачекати |

Змінити їх можна через `SQLITE_JOURNAL_MODE`, `SQLITE_SYNCHRONOUS`,
`SQLITE_BUSY_TIMEOUT` — але на Azure Files не вмикайте WAL.

Якщо запис здається повільним, `SQLITE_SYNCHRONOUS=NORMAL` його прискорить.
Ціна: при аварійному падінні хоста можна втратити кілька останніх транзакцій.
Для пілота це прийнятний обмін, для бойової системи — ні.

### Один воркер, один інстанс

SQLite не тримає паралельний запис із кількох **процесів**, а два інстанси
App Service працювали б із різними копіями файлу на шарі. Тому:

- `startup.sh` запускає **один** процес gunicorn із чотирма потоками;
- **не робіть scale out** (*Scale out (App Service plan)* має лишатися `1`);
- вертикальне масштабування (більший план) — можна, воно не додає процесів.

Обидва обмеження знімаються переїздом на PostgreSQL.

### Ліміт ~230 секунд на запит

Azure App Service обриває HTTP-запит приблизно на 230-й секунді, і цей ліміт
**не налаштовується**. Тому в кроці 3 рекомендовано `LLM_TIMEOUT=200`: інакше
модель ще думає, а користувач уже отримав помилку. Довші генерації доведеться
виносити у фонову обробку.

### Виконання коду скілів

`SKILL_EXEC_ENABLED=1` дозволяє виконувати Python-код із завантажених
скілів-пакетів у підпроцесі. Функція корисна, але розширює поверхню атаки.
Якщо скіли-пакети не потрібні — ставте `0`.

### Відома проблема схеми

Сім зв'язків у схемі оголошені без правила `ON DELETE`:
`ideas.source_resource_id`, `ideas.resource_id`,
`learning_path_steps.resource_id`, `user_files.resource_id`,
`chat_sessions.group_id`, `user_skills.group_id`, `token_usage_logs.group_id`.

SQLite за замовчуванням перевірку зовнішніх ключів **не виконує**, тому зараз
це ні на що не впливає: видалення матеріалу чи групи працює. Але:

- увімкнення `SQLITE_FOREIGN_KEYS=1` зламає ці видалення
  (`FOREIGN KEY constraint failed`);
- **на PostgreSQL перевірка увімкнена завжди й вимкнути її не можна** — тому
  ці зв'язки треба полагодити міграцією (`SET NULL` там, де запис має пережити
  батька) **до** переїзду.

### Коли переростете: PostgreSQL

Ознаки, що час: більше кількох десятків активних користувачів, потреба в
масштабуванні або в кількох воркерах.

1. Полагодити сім зв'язків із попереднього пункту (окрема міграція).
2. Створити **Azure Database for PostgreSQL — Flexible Server**.
3. Додати `psycopg2-binary` у `requirements.txt`.
4. Задати `DATABASE_URL=postgresql+psycopg2://<user>:<pass>@<host>/<db>?sslmode=require`.
   Налаштування SQLite (`SQLITE_*`) при цьому автоматично не застосовуються —
   фабрика вмикає їх лише для SQLite.
5. Перевірити `relax_not_null()` у `app/core/schema.py`: вона написана під
   SQLite (перебудова таблиці через перейменування). На PostgreSQL потрібен
   звичайний `ALTER TABLE … ALTER COLUMN … DROP NOT NULL`.
6. Перенести дані та підняти `GUNICORN_WORKERS` (змінна середовища, код
   міняти не треба).

---

## Вхід через Microsoft Entra ID

Корпоративний вхід вмикається змінними середовища — код міняти не треба.
Поки `ENTRA_TENANT_ID` або `ENTRA_CLIENT_ID` порожні, застосунок працює на
парольному вході.

### Реєстрація застосунку в Entra ID

1. **Azure Portal → Microsoft Entra ID → App registrations → New registration.**
2. **Redirect URI:** тип *Web*, значення
   `https://<APP>.azurewebsites.net/api/auth/entra/callback`.
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
| `ENTRA_REDIRECT_URI` | `https://<APP>.azurewebsites.net/api/auth/entra/callback` | Має **точно** збігатися зі зареєстрованим |
| `ENTRA_ADMIN_GROUPS` | `<object id групи>` | Групи, що дають роль **Admin** (через кому) |
| `ENTRA_MANAGER_GROUPS` | `<object id>,<object id>` | Групи, що дають роль **Skill Manager** |
| `ENTRA_ALLOW_PASSWORD_LOGIN` | `1` / `0` | `0` вимикає вхід за паролем повністю |

```bash
az webapp config appsettings set --name $APP --resource-group $RG --settings \
  ENTRA_TENANT_ID="<tenant>" \
  ENTRA_CLIENT_ID="<client>" \
  ENTRA_REDIRECT_URI="https://$APP.azurewebsites.net/api/auth/entra/callback" \
  ENTRA_ADMIN_GROUPS="<group-object-id>" \
  ENTRA_MANAGER_GROUPS="<group-object-id>"
```

> `ENTRA_REDIRECT_URI` краще задавати явно. Без нього застосунок збирає адресу
> з запиту; за `FLASK_ENV=production` він довіряє заголовку
> `X-Forwarded-Proto` і отримає правильну `https`-схему, але явне значення
> усуває цілий клас проблем із проксі.

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
