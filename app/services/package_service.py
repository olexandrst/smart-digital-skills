"""PackageService — пакети навичок (архів зі skill.md) для хостингу в каталозі.

Формат пакета (архів .zip або .skill):
    skill.md            # YAML-фронтматер (name, description, version,
                        # author, category) + інструкції у тілі
    ...                 # будь-які інші файли та папки

Застосунок пакети не виконує: зберігає архів, показує його опис, інструкції
та перелік файлів і віддає на завантаження. Перевірка безпеки архіву
(шляхи з «..», абсолютні шляхи, розмір у розпакованому вигляді) лишається:
пакет розпаковуватиме вже користувач у себе, і небезпечний архів не має
потрапити в каталог.
"""
import io
import os
import zipfile
from flask import current_app

from app.core.errors import ApiError

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

SKILL_MD_NAMES = ("skill.md", "SKILL.md", "skil.md")
ALLOWED_EXTENSIONS = (".zip", ".skill")


# ----------------------------- Розбір архіву -----------------------------

def _read_zip(file_bytes):
    try:
        return zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise ApiError("Файл не є коректним zip/skill-архівом", 400,
                       "invalid_archive") from exc


def _find_skill_md(zf):
    """Знаходить skill.md найменшої глибини; повертає шлях в архіві."""
    candidates = []
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1]
        if base in SKILL_MD_NAMES:
            candidates.append((name.count("/"), name))
    if not candidates:
        raise ApiError("В архіві відсутній skill.md", 400, "missing_skill_md")
    candidates.sort()  # найменша глибина першою
    return candidates[0][1]


def _parse_frontmatter(text):
    """Витягує YAML-фронтматер між рядками '---'. Повертає (meta, body)."""
    meta, body = {}, text
    stripped = text.lstrip()
    if stripped.startswith("---"):
        rest = stripped[3:]
        end = rest.find("\n---")
        if end != -1:
            raw = rest[:end]
            body = rest[end + 4:]
            if yaml is not None:
                try:
                    meta = yaml.safe_load(raw) or {}
                except yaml.YAMLError as exc:
                    raise ApiError(f"Помилка YAML у skill.md: {exc}", 400,
                                   "invalid_skill_md") from exc
            else:  # дуже простий fallback без PyYAML
                for line in raw.splitlines():
                    if ":" in line and not line.strip().startswith("-"):
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
    if not isinstance(meta, dict):
        raise ApiError("Фронтматер skill.md має бути словником", 400,
                       "invalid_skill_md")
    return meta, body


def parse_package(file_bytes):
    """Розбирає архів: повертає метадані навички зі skill.md.

    Обов'язкове лише `name`; опис за відсутності дорівнює назві. Інші ключі
    фронтматера (runtime, entrypoint, inputs тощо) ігноруються — вони
    стосувалися запуску, якого більше немає.
    """
    zf = _read_zip(file_bytes)
    _validate_zip_safety(zf)
    text = zf.read(_find_skill_md(zf)).decode("utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)

    name = str(meta.get("name") or "").strip()
    if not name:
        raise ApiError("У SKILL.md не вказано name", 400, "invalid_skill_md")
    description = str(meta.get("description") or "").strip() or name

    return {
        "name": name,
        "description": description,
        "author": (str(meta.get("author") or "").strip() or None),
        "category": (str(meta.get("category") or "").strip() or None),
        "version": str(meta.get("version", "1.0.0")),
        "instructions": body.strip(),
    }


# --------------------------- Безпека архіву ---------------------------

def _validate_zip_safety(zf):
    """Захист від zip-slip та zip-bomb."""
    max_unzipped = current_app.config.get("SKILL_PACKAGE_MAX_UNZIPPED",
                                          50 * 1024 * 1024)
    total = 0
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
            raise ApiError(f"Небезпечний шлях в архіві: {name}", 400, "unsafe_archive")
        total += info.file_size
        if total > max_unzipped:
            raise ApiError("Архів перевищує дозволений розмір у розпакованому вигляді",
                           400, "archive_too_large")


# --------------------------- Збереження ---------------------------

def store_package(skill_id, file_bytes):
    packages_dir = current_app.config["SKILL_PACKAGES_DIR"]
    os.makedirs(packages_dir, exist_ok=True)
    path = os.path.join(packages_dir, f"skill_{skill_id}.zip")
    with open(path, "wb") as fh:
        fh.write(file_bytes)
    return path


def delete_package_file(skill):
    if skill.package_path and os.path.exists(skill.package_path):
        try:
            os.remove(skill.package_path)
        except OSError:
            pass


def list_package_files(skill):
    """Файли всередині пакета (без тек) — щоб видно було, що завантажуєш."""
    if not skill.package_path or not os.path.exists(skill.package_path):
        return []
    with open(skill.package_path, "rb") as fh:
        zf = _read_zip(fh.read())
    return [{"name": i.filename, "size": i.file_size}
            for i in zf.infolist() if not i.filename.endswith("/")]


# ----------------------------- Іконки навичок -----------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def store_icon(skill_id, file_bytes):
    """Зберігає PNG-іконку навички. Валідує сигнатуру та розмір."""
    max_bytes = current_app.config.get("SKILL_ICON_MAX_BYTES", 2 * 1024 * 1024)
    if not file_bytes or not file_bytes.startswith(_PNG_MAGIC):
        raise ApiError("Іконка має бути у форматі PNG", 400, "invalid_icon")
    if len(file_bytes) > max_bytes:
        raise ApiError("Іконка завелика (максимум 2 МБ)", 400, "icon_too_large")
    icons_dir = current_app.config["SKILL_ICONS_DIR"]
    os.makedirs(icons_dir, exist_ok=True)
    path = os.path.join(icons_dir, f"skill_{skill_id}.png")
    with open(path, "wb") as fh:
        fh.write(file_bytes)
    return path


def delete_icon(skill):
    if skill.icon_path and os.path.exists(skill.icon_path):
        try:
            os.remove(skill.icon_path)
        except OSError:
            pass
