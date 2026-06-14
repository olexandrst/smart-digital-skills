---
name: Word Counter
description: Підраховує слова, символи та рядки у наданому тексті (виконується локально як Python-код).
version: 1.0.0
runtime: python
entrypoint: main.py
inputs:
  - name: text
    label: Текст
    required: true
    description: Текст для аналізу
  - name: uppercase
    label: У верхньому регістрі
    required: false
    default: ""
    description: Будь-яке значення → додати версію тексту у ВЕРХНЬОМУ регістрі
  - name: save_report
    label: Зберегти звіт у файл
    required: false
    default: ""
    description: Будь-яке значення → створити report.txt (зберігається у ваших файлах)
---

# Word Counter

Демонстраційний **скіл-пакет**. Показує, що система вміє виконувати Python-код,
запакований в архів разом зі `skill.md`.

## Контракт виконання
- На `stdin` надходить JSON виду `{"inputs": {...}}`.
- У `stdout` потрібно вивести JSON `{"output": "...", "usage": {...}}`
  (поле `usage` опціональне).

Допоміжна логіка винесена в `lib/counter.py`, щоб продемонструвати підтримку
вкладених файлів і папок усередині пакета.
