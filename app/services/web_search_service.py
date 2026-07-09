"""Веб-пошук через DuckDuckGo (пакет `ddgs`).

Використовується для «онлайн»-режиму чату (гібридний RAG): за запитом
користувача формується пошуковий запит, результати передаються моделі як
контекст. У режимі моку (WEB_SEARCH_MOCK / LLM_MOCK) або за відсутності
пакета/мережі повертаються детерміновані демо-результати, тож чат не падає.
"""
from flask import current_app


def _mock_enabled():
    cfg = current_app.config
    return cfg.get("WEB_SEARCH_MOCK", cfg.get("LLM_MOCK", True))


def _mock_results(query, max_results):
    q = (query or "").strip() or "запит"
    slug = q.replace(" ", "_")
    demo = [
        {"title": f"{q} — огляд (Вікіпедія)",
         "url": f"https://uk.wikipedia.org/wiki/{slug}",
         "snippet": f"Демонстраційний результат веб-пошуку (режим моку) за запитом «{q}». "
                    "Підключіть реальний DuckDuckGo (пакет ddgs, WEB_SEARCH_MOCK=0)."},
        {"title": f"{q}: практичний посібник",
         "url": "https://example.com/guide",
         "snippet": f"Мок-джерело №2 з коротким описом теми «{q}»."},
        {"title": f"Часті питання про {q}",
         "url": "https://example.org/faq",
         "snippet": "Мок-джерело №3 — типові запитання та відповіді."},
        {"title": f"{q} — новини та аналітика",
         "url": "https://example.net/news",
         "snippet": "Мок-джерело №4 з оглядом останніх подій."},
        {"title": f"Порівняння підходів: {q}",
         "url": "https://example.com/compare",
         "snippet": "Мок-джерело №5 з порівняльною таблицею."},
    ]
    return demo[:max_results]


def _real_results(query, max_results):
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # старіша назва пакета
        except ImportError:
            return []
    try:
        rows = DDGS().text(query, max_results=max_results)
    except Exception:  # мережеві/сервісні помилки — не ламаємо чат
        return []
    out = []
    for r in rows or []:
        out.append({
            "title": r.get("title") or "",
            "url": r.get("href") or r.get("url") or "",
            "snippet": r.get("body") or r.get("snippet") or "",
        })
    return out


def search(query, max_results=None):
    """Повертає список результатів [{title, url, snippet}]."""
    if max_results is None:
        max_results = current_app.config.get("WEB_SEARCH_MAX_RESULTS", 5)
    query = (query or "").strip()
    if not query:
        return []
    if _mock_enabled():
        return _mock_results(query, max_results)
    return _real_results(query, max_results)


def format_results(results):
    """Форматує результати як текстовий контекст для LLM."""
    if not results:
        return "(веб-пошук не дав результатів)"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\nURL: {r['url']}\n{r['snippet']}")
    return "\n\n".join(lines)
