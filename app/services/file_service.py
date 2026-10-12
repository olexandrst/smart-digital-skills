"""FileService — фізичне сховище вкладень до карток каталогу.

Кожному користувачу відповідає підкаталог з GUID-назвою:
    <USER_FILES_DIR>/<user.storage_uid>/
Вкладення лежить у теці того, хто його завантажив; права на читання
визначає картка, до якої воно прикріплене (див. app/api/catalog.py).
"""
import os
import re
import uuid
import mimetypes
from flask import current_app

from app.extensions import db
from app.core.errors import ApiError
from app.models import UserFile

# Дозволяємо Unicode-літери/цифри (\w), крапку та дефіс; решту → "_".
_SAFE_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def _safe_basename(name):
    base = (name or "").replace("\\", "/").split("/")[-1].strip() or "file"
    base = _SAFE_RE.sub("_", base.replace(" ", "_"))
    base = base.strip("._") or "file"
    return base[:120]


def _unique_name(directory, name):
    """Унікальна назва у теці: report.txt → report_1.txt, report_2.txt, …"""
    if not os.path.exists(os.path.join(directory, name)):
        return name
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(os.path.join(directory, f"{stem}_{i}{ext}")):
        i += 1
    return f"{stem}_{i}{ext}"


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


def save_attachment(user, filename, data, resource_id):
    """Зберігає байти як вкладення до картки; повертає UserFile."""
    max_bytes = current_app.config.get("USER_FILE_MAX_BYTES", 25 * 1024 * 1024)
    if len(data) > max_bytes:
        raise ApiError(f"Файл '{filename}' перевищує ліміт розміру",
                       400, "file_too_large")

    directory = user_dir(user)
    stored = _unique_name(directory, _safe_basename(filename))
    with open(os.path.join(directory, stored), "wb") as fh:
        fh.write(data)

    uf = UserFile(
        user_id=user.id,
        filename=stored,
        stored_name=stored,
        content_type=(mimetypes.guess_type(stored)[0] or "application/octet-stream"),
        size=len(data),
        source="attachment",
        resource_id=resource_id,
    )
    db.session.add(uf)
    db.session.commit()
    return uf


def remove_stored_file(uf):
    """Прибирає фізичний файл будь-якого власника (для вкладень і каскадів).

    Права не перевіряє — виклик має сам вирішити питання доступу.
    """
    owner = uf.user
    if owner is None:
        return
    path = os.path.join(user_dir(owner), uf.stored_name)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
