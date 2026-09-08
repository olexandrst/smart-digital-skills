# Розгортання AI Knowledge Hub на Azure App Service

Інструкція для розгортання з **ZIP-архіву, завантаженого з GitHub**.
Конфігурація розрахована на **пілот**: один інстанс, база SQLite у
персистентному сховищі `/home`. Розділ [«Обмеження»](#обмеження-цієї-конфігурації)
пояснює, коли цього перестане вистачати.

Орієнтовний час: **20–30 хвилин**.

---

## Передумови

- Підписка Azure та роль **Contributor** на групі ресурсів.
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
  (`az login`) — або портал, якщо CLI недоступний: усі кроки мають
  альтернативу через портал.
- Архів проєкту з GitHub (кнопка **Code → Download ZIP** на потрібній гілці).

---

## Крок 0. Підготувати архів

> **Найчастіша помилка.** ZIP із GitHub містить **вкладену теку** —
> `smart-digital-skills-<гілка>/`, а всередині неї вже застосунок. Azure очікує,
> що `requirements.txt` і `wsgi.py` лежать **у корені архіву**. Якщо залити ZIP
> як є, збірка не знайде залежностей і застосунок не підніметься.

Розпакуйте архів і **перепакуйте його вміст**, а не саму теку.

**Windows PowerShell:**

```powershell
Expand-Archive .\smart-digital-skills-main.zip -DestinationPath .\unpacked
Compress-Archive -Path .\unpacked\smart-digital-skills-main\* -DestinationPath .\app.zip -Force
```

**macOS / Linux:**

```bash
unzip smart-digital-skills-main.zip
cd smart-digital-skills-main
zip -r ../app.zip . -x '*.git*' '*__pycache__*' '*.pytest_cache*'
cd ..
```

Перевірте, що в корені `app.zip` лежать `requirements.txt`, `wsgi.py`,
`startup.sh` і тека `app/`.

---

## Крок 1. Створити App Service

**CLI:**

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

> **Не беріть план F1 (Free).** Він засинає без трафіку, а разом із ним
> зупиняється фоновий планувальник тижневого скидання квот. Мінімум — **B1**.

---

## Крок 2. Змінні середовища

**Обов'язкові:**

| Змінна | Значення | Навіщо |
|---|---|---|
| `SECRET_KEY` | випадковий рядок ≥ 32 символи | Підпис сесій Flask |
| `JWT_SECRET_KEY` | інший випадковий рядок ≥ 32 символи | Підпис токенів доступу |
| `FLASK_ENV` | `production` | Вимикає режим налагодження |
| `INSTANCE_DIR` | `/home/data` | **Ключова змінна.** Переносить базу, пакети й іконки скілів, файли користувачів і тимчасову теку в персистентне сховище |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `1` | Каже Azure встановити залежності з `requirements.txt` при деплої |

Згенерувати секрети:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Рекомендовані:**

| Змінна | Значення | Коментар |
|---|---|---|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | власні | Обліковий запис адміністратора, що створить `seed`. **Задайте до кроку 5** |
| `LLM_MOCK` | `1` або `0` | `1` — демо без ключів; `0` — реальні виклики моделей |
| `LLM_TIMEOUT` | `200` | Див. [обмеження](#обмеження-цієї-конфігурації): за замовчуванням 1800 с, але Azure обриває запит раніше |
| `SKILL_EXEC_ENABLED` | `0` | Вимикає виконання Python-коду зі скілів-пакетів. Лишайте `1`, лише якщо ця функція потрібна |
| `ENABLE_SCHEDULER` | `1` | Тижневе скидання лічильників квот |

**Для реальних моделей** (за `LLM_MOCK=0`) додайте `AZURE_FOUNDRY_ENDPOINT`
та `AZURE_FOUNDRY_API_KEY`. Повний перелік змінних — у `.env.example`.

**CLI:**

```bash
az webapp config appsettings set --name $APP --resource-group $RG --settings \
  SECRET_KEY="<згенерований>" \
  JWT_SECRET_KEY="<інший згенерований>" \
  FLASK_ENV=production \
  INSTANCE_DIR=/home/data \
  SCM_DO_BUILD_DURING_DEPLOYMENT=1 \
  ADMIN_USERNAME=admin \
  ADMIN_PASSWORD="<надійний пароль>" \
  LLM_MOCK=1 \
  LLM_TIMEOUT=200 \
  SKILL_EXEC_ENABLED=0
```

**Через портал:** *Settings → Environment variables → App settings*.

> Секрети краще тримати в **Azure Key Vault** і підключати через
> Key Vault reference, а не значенням у налаштуваннях.

---

## Крок 3. Команда запуску

```bash
az webapp config set --name $APP --resource-group $RG --startup-file "bash startup.sh"
```

**Через портал:** *Settings → Configuration → General settings → Startup Command* →
`bash startup.sh`.

> Саме `bash startup.sh`, а не просто `startup.sh`. Якщо ви пакували архів у
> Windows, `Compress-Archive` **не зберігає біт виконання** — і скрипт, запущений
> напряму, впаде з `Permission denied`. Виклик через `bash` працює в обох
> випадках.

Скрипт уже є в репозиторії. Він запускає gunicorn з одним воркером і
таймаутом 240 с — чому саме так, пояснено коментарями всередині `startup.sh`
та в розділі [«Обмеження»](#обмеження-цієї-конфігурації).

---

## Крок 4. Залити архів

```bash
az webapp deploy --name $APP --resource-group $RG --src-path app.zip --type zip
```

**Через портал:** Kudu — `https://<APP>.scm.azurewebsites.net/ZipDeployUI` —
перетягніть `app.zip` у вікно.

Перша збірка триває 3–5 хвилин: Azure встановлює залежності з
`requirements.txt`.

> **Не вмикайте `WEBSITE_RUN_FROM_PACKAGE`.** Ця опція монтує застосунок
> **тільки для читання**, а він створює робочу теку при старті — і не
> підніметься. Якщо змінна вже є в налаштуваннях, приберіть її.

---

## Крок 5. Створити початкові дані

Схема бази створюється автоматично при першому старті — окремої команди для
цього не потрібно. А ось наповнення (ролі, адміністратор, демо-каталог)
запускається один раз вручну.

Підключіться до контейнера:

```bash
az webapp ssh --name $APP --resource-group $RG
```

**Через портал:** *Development Tools → SSH → Go*.

У консолі:

```bash
cd /home/site/wwwroot
python -m scripts.seed
```

Скрипт **ідемпотентний** — повторний запуск нічого не зіпсує й не перезапише.

Він створює: ролі, адміністратора (з `ADMIN_USERNAME` / `ADMIN_PASSWORD`),
трьох демо-користувачів, дев'ять розділів каталогу та демо-наповнення —
промпти, інструкції, кейс, агентів, MCP-сервери й корисні посилання.

> **Демо-користувачі** (`skillmanager`, `user1`, `user2`) створюються з
> паролями за замовчуванням із `scripts/seed.py`. Перед реальним запуском
> видаліть їх або змініть паролі у вкладці «Користувачі».

---

## Крок 6. Перевірити

```bash
curl https://$APP.azurewebsites.net/api/health
# {"service":"smart-profihub","status":"ok"}
```

Далі у браузері `https://<APP>.azurewebsites.net`:

1. Вхід під адміністратором.
2. **Каталог** — видно розділи й матеріали з демо-наповнення.
3. **Навички → Каталог** — створіть тестовий матеріал.
4. Перезапустіть застосунок (`az webapp restart --name $APP --resource-group $RG`)
   і переконайтеся, що матеріал **лишився на місці** — це підтверджує, що
   `INSTANCE_DIR` справді вказує на персистентне сховище.

Крок 4 — найважливіший. Якщо після перезапуску дані зникли, значить
`INSTANCE_DIR` не застосувався: перевірте написання змінної та перезапустіть.

---

## Обмеження цієї конфігурації

Це конфігурація для **пілота**, і вона має чіткі межі. Краще знати їх наперед.

**Один воркер, без масштабування.** SQLite не тримає паралельний запис із
кількох процесів, а фоновий планувальник стартує в кожному з них — на двох
воркерах тижневе скидання квот виконалося б двічі. Тому `startup.sh` запускає
один процес із чотирма потоками. **Не збільшуйте кількість інстансів
(scale out)** — вони працюватимуть із різними копіями бази. Обидва обмеження
знімаються переїздом на PostgreSQL.

**Ліміт ~230 секунд на запит.** Azure App Service обриває HTTP-запит приблизно
на 230-й секунді, і цей ліміт **не налаштовується**. У застосунку
`LLM_TIMEOUT` за замовчуванням 1800 с — тобто модель ще думає, а користувач уже
отримав помилку. Тому в кроці 2 рекомендовано `LLM_TIMEOUT=200`. Якщо потрібні
довші генерації, їх доведеться виносити у фонову обробку.

**Виконання коду скілів.** `SKILL_EXEC_ENABLED=1` дозволяє застосунку виконувати
Python-код із завантажених скілів-пакетів у підпроцесі. Функція корисна, але
розширює поверхню атаки. Якщо скіли-пакети не потрібні — ставте `0`.

**Резервне копіювання.** Уся база — один файл `/home/data/profihub.db`.
Налаштуйте *Backups* у App Service або періодично копіюйте файл через SSH.

---

## Оновлення на нову версію

1. Завантажте новий ZIP із GitHub і перепакуйте (крок 0).
2. `az webapp deploy ... --src-path app.zip --type zip`.

Схема бази **доганяється сама**: при старті виконується легка additive-міграція,
яка дописує нові колонки й таблиці, не чіпаючи наявних даних. `/home/data`
деплой не перезаписує, тож контент і користувачі зберігаються.

Перед оновленнями, що змінюють схему, зробіть копію файлу бази:

```bash
cp /home/data/profihub.db /home/data/profihub-$(date +%F).db
```

---

## Діагностика

**Живі логи:**

```bash
az webapp log tail --name $APP --resource-group $RG
```

| Симптом | Найімовірніша причина |
|---|---|
| «Application Error» одразу після деплою | Архів залитий разом із вкладеною текою (крок 0), або не задано `SCM_DO_BUILD_DURING_DEPLOYMENT=1` |
| У логах `exec: gunicorn: not found` | Залежності не встановились — перевірте попередній пункт і перезалийте |
| `startup.sh: Permission denied` | Startup-команда задана як `startup.sh`; замініть на `bash startup.sh` |
| Застосунок не стартує, помилка запису | Увімкнено `WEBSITE_RUN_FROM_PACKAGE` — приберіть змінну |
| 504 на довгих відповідях моделі | Ліміт Azure ~230 с; зменште `LLM_TIMEOUT` |
| Після редеплою зник увесь контент | `INSTANCE_DIR` не задано або задано неправильно — дані пішли в ефемерну теку |
| Вхід не працює після рестарту | Змінився `JWT_SECRET_KEY` — старі токени недійсні; це очікувано, увійдіть заново |

---

## Коли переростете: міграція на PostgreSQL

Ознаки, що час: більше кількох десятків активних користувачів, потреба в
масштабуванні або в кількох воркерах.

Що знадобиться:

1. Створити **Azure Database for PostgreSQL — Flexible Server**.
2. Додати `psycopg2-binary` у `requirements.txt`.
3. Задати `DATABASE_URL=postgresql+psycopg2://<user>:<pass>@<host>/<db>?sslmode=require`.
4. **Перевірити автоміграцію.** Функція `relax_not_null()` у
   `app/core/schema.py` написана під SQLite — вона перебудовує таблицю через
   перейменування. На PostgreSQL цей шлях треба замінити на звичайний
   `ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL` або перейти на Alembic.
5. Перенести наявні дані з SQLite і збільшити кількість воркерів у
   `startup.sh`.
