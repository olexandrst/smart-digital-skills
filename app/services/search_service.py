"""Пошук мовою бізнес-задачі (BR-07).

Користувач шукає не назву матеріалу, а свою задачу: «хочу автоматизувати збір
ідей», «як швидше обробляти документи». Тому пошук працює у три кроки:

1. **Нормалізація** — нижній регістр, зняття пунктуації, відкидання службових
   слів і українських закінчень (легкий стемінг замість повної морфології:
   словника словоформ у застосунку немає, а «документи/документів/документами»
   мають збігатися).
2. **Розширення синонімами** — керований словник (`SearchSynonym`), який
   менеджер поповнює з інтерфейсу: формулювання задачі → канонічні слова.
3. **Ранжування за полями** — збіг у назві важить більше за збіг у тексті.

Пошук навмисно детермінований і без зовнішніх викликів: він є фундаментом для
AI-помічника, який лише пояснює знайдене, а не вигадує посилання.
"""
import re
import unicodedata

# Вага поля в підсумковому балі: точний збіг у назві має важити більше за
# випадкову згадку в тілі документа.
FIELD_WEIGHTS = {
    "name": 5.0,
    "tags": 3.0,
    "category": 3.0,
    "tools": 2.0,
    "description": 2.0,
    "body": 1.0,
}

# Бонус за те, що запит цілою фразою входить у назву — сильніший сигнал,
# ніж збіг окремих слів.
PHRASE_BONUS = 6.0

# Службові слова, які нічого не додають до змісту запиту.
STOP_WORDS = {
    "і", "й", "та", "а", "але", "або", "чи", "що", "як", "де", "коли", "щоб",
    "це", "цей", "ця", "той", "так", "для", "на", "в", "у", "з", "із", "зі",
    "до", "від", "за", "по", "при", "про", "над", "під", "без", "не", "ні",
    "я", "ми", "ти", "ви", "він", "вона", "воно", "вони", "мені", "нам",
    "хочу", "хочемо", "треба", "потрібно", "потрібен", "потрібна", "потрібні",
    "можна", "може", "мати", "бути", "є", "буде", "було", "були", "зробити",
    "робити", "мене", "тебе", "його", "її", "їх", "них", "усе", "все", "щось",
    "the", "a", "an", "to", "of", "for", "and", "or", "how", "i", "we", "my",
}

# Закінчення, які відкидаємо при стемінгу. Порядок має значення: довші перші,
# інакше «-ами» перетвориться на «-и» замість того, щоб зникнути цілком.
# Свідомо БЕЗ «-ти»/«-ть»: вони зрізають іменники («документи» → «докумен»),
# а дієслова й так зводяться разом за спільним префіксом основи.
_ENDINGS = (
    "ування", "ованих", "ованого", "ується", "ються", "ається", "увати",
    "ністю", "ності", "ність", "ями", "ами", "ові", "еві", "ією",
    "ого", "ому", "ими", "ої", "ій", "их", "ім", "ах", "ях", "ем", "ом",
    "ів", "ей", "ям", "ся", "ці", "ки", "ку", "ка", "ко",
    "и", "і", "а", "о", "у", "е", "я", "ю", "ї", "й", "ь",
)

# Мінімальна довжина основи після стемінгу — коротші обрізки дають шум.
MIN_STEM = 4

# З якої довжини основи вважаємо збіг за спільним початком.
# Легкий стемінг не зводить «документ» і «документація» до одного кореня —
# зате вони мають спільний початок, і саме це робить пошук стійким до
# української морфології без повного словника словоформ.
MIN_PREFIX = 5

# З якої довжини слова допускаємо одну помилку (одрук).
TYPO_MIN_LEN = 6

_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def _fold(text):
    """Нижній регістр у канонічній композиції.

    Саме NFC, а не NFKD: декомпозиція розкладає «й» на «и» + комбінований знак,
    після чого слово стає довшим на невидимий символ, а «й» зливається з «и».
    """
    return unicodedata.normalize("NFC", (text or "").casefold())


def stem(word):
    """Легкий стемінг української словоформи."""
    word = _fold(word)
    if len(word) <= MIN_STEM:
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[: -len(ending)]
    return word


def tokenize(text):
    """Слова тексту без пунктуації, службових слів і закінчень."""
    words = [w for w in _WORD_RE.split(_fold(text)) if w]
    return [stem(w) for w in words if w not in STOP_WORDS and len(w) > 1]


def same_root(a, b):
    """Чи це одне слово: точний збіг основ або спільний початок.

    Спільний початок покриває те, чого не робить легкий стемінг:
    «документ» ↔ «документація», «аналіз» ↔ «аналітика».
    """
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= MIN_PREFIX and longer.startswith(shorter)


def _close_enough(a, b):
    """Чи це те саме слово з одним одруком (вставка, пропуск або заміна)."""
    if a == b:
        return True
    if min(len(a), len(b)) < TYPO_MIN_LEN or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = sum(1 for x, y in zip(a, b) if x != y)
        return diff == 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(longer)):
        if longer[:i] + longer[i + 1:] == shorter:
            return True
    return False


def expand_tokens(tokens, synonyms):
    """Додає канонічні слова зі словника синонімів.

    `synonyms` — список пар (фраза, [канонічні слова]) вже у стемованому вигляді.
    Спрацьовує і на цілу фразу запиту, і на окремі слова.
    """
    out = set(tokens)
    token_set = set(tokens)
    for phrase_tokens, terms in synonyms:
        if not phrase_tokens:
            continue
        # Фраза підходить, якщо всі її слова є в запиті.
        if set(phrase_tokens) <= token_set:
            out.update(terms)
    return out


def _field_tokens(item):
    """Стемовані слова кожного поля картки — рахуються один раз на матеріал."""
    return {
        "name": tokenize(item.get("name")),
        "tags": tokenize(" ".join(item.get("tags") or [])),
        "category": tokenize(item.get("category")),
        "tools": tokenize(item.get("tools")),
        "description": tokenize(item.get("description")),
        "body": tokenize(item.get("body")),
    }


def score_item(item, query_tokens, raw_query):
    """Бал відповідності картки запиту та слова, які справді збіглися."""
    fields = _field_tokens(item)
    score = 0.0
    matched = set()

    for field, weight in FIELD_WEIGHTS.items():
        field_tokens = set(fields[field])
        if not field_tokens:
            continue
        for token in query_tokens:
            if token in field_tokens:
                score += weight
                matched.add(token)
            elif any(same_root(token, other) for other in field_tokens):
                # Той самий корінь в іншій формі — майже повноцінний збіг.
                score += weight * 0.85
                matched.add(token)
            elif any(_close_enough(token, other) for other in field_tokens):
                # Одрук коштує дешевше за точний збіг, але не ігнорується.
                score += weight * 0.6
                matched.add(token)

    phrase = _fold(raw_query).strip()
    if phrase and phrase in _fold(item.get("name") or ""):
        score += PHRASE_BONUS
    return score, matched


def build_synonym_index(rows):
    """Готує словник синонімів до пошуку: стемує і фрази, і канонічні слова."""
    index = []
    for row in rows:
        phrase_tokens = tokenize(row.phrase)
        terms = []
        for term in (row.terms or "").split(","):
            terms.extend(tokenize(term))
        if phrase_tokens and terms:
            index.append((phrase_tokens, terms))
    return index


def search(query, items, synonyms=(), limit=None, min_score=1.0):
    """Ранжований пошук. Повертає список (item, score, matched_tokens).

    `items` — словники карток (те саме, що віддає API). Порожній запит повертає
    порожній результат: показувати «все підряд» як результат пошуку — оманливо.
    """
    tokens = tokenize(query)
    if not tokens:
        return []
    expanded = expand_tokens(tokens, synonyms)

    scored = []
    for item in items:
        score, matched = score_item(item, expanded, query)
        if score >= min_score:
            scored.append((item, round(score, 2), sorted(matched)))
    scored.sort(key=lambda row: (-row[1], (row[0].get("name") or "")))
    return scored[:limit] if limit else scored


def nearest(query, items, synonyms=(), limit=3):
    """Найближчі за змістом матеріали, коли точних збігів немає.

    Поріг знижено: краще показати три приблизні картки, ніж порожній екран.
    """
    return search(query, items, synonyms, limit=limit, min_score=0.5)
