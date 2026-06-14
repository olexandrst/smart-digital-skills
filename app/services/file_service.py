"""FileService — постійне сховище файлів користувача.

Кожному користувачу відповідає підкаталог з GUID-назвою:
    <USER_FILES_DIR>/<user.storage_uid>/

Файли, створені скілами під час виконання, та завантажені користувачем
зберігаються тут назавжди й доступні для завантаження через API.
"""
import os
import re
import uuid
import mimetypes
from flask import current_app

from app.extensions import db
from app.core.errors import ApiError
from app.models import UserFile

_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def _safe_basename(name):
    base = os.path.basename(name or "").strip() or "file"
    base = _SAFE_RE.sub("_", base)
    return base[:120] or "file"


def ensure_storage_uid(user):
    if not user.storage_uid:
        user.storage_uid = uuid.uuid4().hex
        db.session.commit()
    return user.storage_uid


def user_dir(user):
    uid = ensure_storage_uid(user)
    path = os.path.join(current_app.config["USER_FILES_DIR"], uid)
    os.makedirs(path, exist_ok=True)
    return path


def save_bytes(user, filename, data, source="upload", skill_id=None):
    """Зберігає байти як файл користувача; повертає UserFile."""
    max_bytes = current_app.config.get("USER_FILE_MAX_BYTES", 25 * 1024 * 1024)
    if len(data) > max_bytes:
        raise ApiError(f"Файл '{filename}' перевищує ліміт розміру",
                       400, "file_too_large")

    directory = user_dir(user)
    uf = UserFile(
        user_id=user.id,
        filename=filename or "file",
        stored_name="",  # заповнимо після отримання id
        content_type=(mimetypes.guess_type(filename or "")[0] or "application/octet-stream"),
        size=len(data),
        source=source,
        skill_id=skill_id,
    )
    db.session.add(uf)
    db.session.flush()

    stored = f"{uf.id}_{_safe_basename(filename)}"
    with open(os.path.join(directory, stored), "wb") as fh:
        fh.write(data)
    uf.stored_name = stored
    db.session.commit()
    return uf


def save_path(user, src_path, display_name=None, source="skill_run", skill_id=None):
    with open(src_path, "rb") as fh:
        data = fh.read()
    return save_bytes(user, display_name or os.path.basename(src_path),
                      data, source=source, skill_id=skill_id)


def list_files(user):
    return (UserFile.query.filter_by(user_id=user.id)
            .order_by(UserFile.created_at.desc()).all())


def get_owned_file(user, file_id):
    uf = UserFile.query.filter_by(id=file_id, user_id=user.id).first()
    if uf is None:
        raise ApiError("Файл не знайдено", 404, "not_found")
    return uf


def file_abs_path(uf, user):
    return os.path.join(user_dir(user), uf.stored_name)


def delete_file(user, file_id):
    uf = get_owned_file(user, file_id)
    path = file_abs_path(uf, user)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    db.session.delete(uf)
    db.session.commit()
    return True
