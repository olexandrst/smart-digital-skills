"""Помічник пошуку по базі знань (BR-08, FR-09): «опишіть задачу своїми словами».

Добірку робить детермінований пошук (`search_service`) зі словником
формулювань, а пояснення «чому це підходить» збирається з полів самої
картки. Назви, посилання й ідентифікатори беруться з бази, тож вимога
«жодного посилання, якого немає в каталозі» виконується структурно.
Мовна модель тут не потрібна й не використовується.
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


def _reason(item, matched):
    """Пояснення з полів самої картки: за якими словами збіг, категорія,
    інструменти."""
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


def answer(question, items, synonyms=()):
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
        reasons = [_reason(item, matched) for item, _s, matched in near]
        return {
            "reply": "Точного збігу немає, але найближче за змістом — ось це. "
                     "Якщо не підходить, поділіться ідеєю.",
            "items": [dict(item, assistant_reason=reason)
                      for (item, _s, _m), reason in zip(near, reasons)],
            "clarify": False, "grounded": True, "suggest_idea": True,
        }

    reasons = [_reason(item, matched) for item, _s, matched in hits]
    return {
        "reply": f"Знайшов {_plural(len(hits))} під цю задачу:",
        "items": [dict(item, assistant_reason=reason)
                  for (item, _s, _m), reason in zip(hits, reasons)],
        "clarify": False, "grounded": True,
    }
