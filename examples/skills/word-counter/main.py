"""Точка входу навички-пакета Word Counter.

Читає JSON {"inputs": {...}} зі stdin і друкує JSON {"output": "..."}.
Запускається у власному середовищі користувача — хаб пакет лише зберігає
й віддає на завантаження.
"""
import sys
import json
import os

# Дозволяємо імпорт із підпапки lib/ (демонстрація вкладених модулів).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.counter import analyze


def main():
    raw = sys.stdin.read() or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    inputs = data.get("inputs", {})
    text = inputs.get("text", "")

    stats = analyze(text)
    lines = [
        f"Слів: {stats['words']}",
        f"Символів: {stats['chars']}",
        f"Рядків: {stats['lines']}",
    ]
    if inputs.get("uppercase"):
        lines.append("")
        lines.append("ВЕРХНІЙ РЕГІСТР:")
        lines.append(text.upper())

    output = "\n".join(lines)

    # Якщо задано save_report — звіт пишеться в поточну теку, поруч із місцем запуску.
    if inputs.get("save_report"):
        with open("report.txt", "w", encoding="utf-8") as fh:
            fh.write(output + "\n")

    print(json.dumps({"output": output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
