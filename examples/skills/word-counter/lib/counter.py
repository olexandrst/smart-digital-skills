"""Допоміжний модуль пакета (демонструє вкладені файли/папки)."""


def analyze(text: str) -> dict:
    return {
        "words": len(text.split()),
        "chars": len(text),
        "lines": len(text.splitlines()) or (1 if text else 0),
    }
