"""AI-помічник пошуку по базі знань (BR-08, FR-09).

**Ключове архітектурне рішення: модель не обирає матеріали й не пише посилань.**
Картки добирає детермінований пошук (`search_service`), а модель отримує вже
готовий короткий список і пише лише пояснення «чому це підходить». Назви,
посилання й ідентифікатори береться з бази при складанні відповіді.

Через це вимога «жодного посилання, якого немає в базі» виконується структурно,
а не залежить від того, наскільки слухняною виявиться модель. Якщо модель
недоступна або працює в мок-режимі, помічник усе одно відповідає — пояснення
збираються з полів картки.
"""
from app.services import search_service

# Скільки карток показувати в одній відповіді.
MAX_SUGGESTIONS = 5
# Нижче цього балу картка вважається нерелевантною і не пропонується взагалі.
RELEVANCE_FLOOR = 2.0

# Запит настільки короткий, що з нього не зрозуміти задачу.
CLARIFY_TOKENS = 1

KIND_LABELS = {
    "prompt": "промпт", "instruction": "інструкція", "case": "кейс",
    "agent": "агент", "mcp": "MCP-сервер", "link": "корисне посилання",
}

SYSTEM_PROMPT = (
    "Ти — помічник пошуку у корпоративній базі знань AI Knowledge Hub. "
    "Тобі дають список уже знайдених матеріалів. Твоє завдання — для кожного "
    "написати одне речення, чому він підходить під задачу користувача. "
    "Не вигадуй назв, посилань чи матеріалів, яких немає у списку. "
    "Відповідай українською, стисло, без вступів."
)


def _plural(n):
    """«1 матеріал», «2 матеріали», «5 матеріалів» — без милиць на кшталт «(и)»."""
    tail10, tail100 = n % 10, n % 100
    if tail10 == 1 and tail100 != 11:
        word = "матеріал"
    elif 2 <= tail10 <= 4 and not 12 <= tail100 <= 14:
        word = "матеріали"
    else:
        word = "матеріалів"
    return f"{n} {word}"


def _reason_offline(item, matched):
    """Пояснення без моделі — з полів самої картки.

    Використовується в мок-режимі й коли модель недоступна: помічник має
    працювати завжди, просто без «людської» мови.
    """
    kind = KIND_LABELS.get(item.get("resource_type"), "матеріал")
    bits = []
    if matched:
        bits.append("збіг за: " + ", ".join(matched[:4]))
    if item.get("category"):
        bits.append(f"категорія «{item['category']}»")
    if item.get("tools"):
        bits.append(f"інструменти: {item['tools']}")
    tail = "; ".join(bits)
    return f"Це {kind}" + (f" — {tail}." if tail else ".")


def _ask_model(question, picked):
    """Просить модель пояснити добірку. Повертає список рядків або None.

    Помилка моделі не є помилкою помічника: краще віддати відповідь із
    офлайн-поясненнями, ніж нічого.
    """
    try:
        from app.models import Model
        from app.integrations import get_client_for_model

        model = (Model.query.filter_by(is_system=True, is_active=True).first()
                 or Model.query.filter_by(is_active=True).first())
        if model is None:
            return None

        listing = "\n".join(
            f"{i + 1}. {item.get('name')} — {item.get('description') or 'без опису'}"
            for i, (item, _score, _matched) in enumerate(picked))
        client = get_client_for_model(model)
        reply = client.chat(model.deployment_name, [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Задача користувача: {question}\n\n"
                                        f"Знайдені матеріали:\n{listing}\n\n"
                                        f"Дай рівно {len(picked)} речень, по одному "
                                        f"на матеріал, у тому самому порядку."},
        ])
        lines = [ln.strip(" -–—•\t") for ln in (reply.content or "").splitlines()
                 if ln.strip()]
        return lines if len(lines) >= len(picked) else None
    except Exception:
        return None


def answer(question, items, synonyms=(), use_model=True):
    """Відповідь помічника: добірка карток із поясненнями.

    Повертає словник із ключами `reply`, `items`, `clarify`, `grounded`.
    `items` містить лише те, що є в базі — жодних вигаданих посилань.
    """
    text = (question or "").strip()
    tokens = search_service.tokenize(text)

    if not tokens:
        return {"reply": "Опишіть, будь ласка, задачу кількома словами — "
                         "наприклад «підготувати протокол наради».",
                "items": [], "clarify": True, "grounded": True}

    if len(tokens) <= CLARIFY_TOKENS:
        # Уточнювальне питання замість здогадок на одному слові.
        return {"reply": f"Уточніть, будь ласка, що саме потрібно зробити "
                         f"з «{text}»: знайти готове рішення, навчитися чи "
                         f"автоматизувати процес?",
                "items": [], "clarify": True, "grounded": True}

    hits = [row for row in search_service.search(text, items, synonyms)
            if row[1] >= RELEVANCE_FLOOR][:MAX_SUGGESTIONS]

    if not hits:
        near = search_service.nearest(text, items, synonyms, limit=3)
        if not near:
            return {"reply": "У базі знань поки немає матеріалів під цю задачу. "
                             "Поділіться ідеєю — команда хабу побачить, "
                             "якого контенту бракує.",
                    "items": [], "clarify": False, "grounded": True,
                    "suggest_idea": True}
        reasons = [_reason_offline(item, matched) for item, _s, matched in near]
        return {
            "reply": "Точного збігу немає, але найближче за змістом — ось це. "
                     "Якщо не підходить, поділіться ідеєю.",
            "items": [dict(item, assistant_reason=reason)
                      for (item, _s, _m), reason in zip(near, reasons)],
            "clarify": False, "grounded": True, "suggest_idea": True,
        }

    reasons = _ask_model(text, hits) if use_model else None
    if reasons is None:
        reasons = [_reason_offline(item, matched) for item, _s, matched in hits]

    return {
        "reply": f"Знайшов {_plural(len(hits))} під цю задачу:",
        "items": [dict(item, assistant_reason=reason)
                  for (item, _s, _m), reason in zip(hits, reasons)],
        "clarify": False, "grounded": True,
    }
