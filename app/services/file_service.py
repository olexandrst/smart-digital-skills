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
from datetime import datetime, timedelta
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


# ----------------------------- Перегляд медіа -----------------------------
#
# Зображення й відео з вкладень показуються в картці прямо: мініатюри,
# перегляд на весь екран, відеоплеєр. Теги <img> і <video> не вміють
# передати JWT, тому доступ до перегляду дає підписане посилання з обмеженим
# строком дії (як presigned URL в об'єктних сховищах). Підпис прив'язаний до
# файлу й користувача, а права на картку перевіряються при кожному запиті.
#
# Лише растрові зображення та відео, які браузер показує сам. SVG свідомо не
# входить: це документ, що може містити скрипти, і відкритий напряму з нашого
# домену він виконав би їх від імені користувача. Тип визначаємо за
# розширенням, а не за заголовком, який надіслав браузер при завантаженні.

MEDIA_TYPES = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"), ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"), ".avif": ("image", "image/avif"),
    ".bmp": ("image", "image/bmp"),
    ".mp4": ("video", "video/mp4"), ".m4v": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"), ".ogv": ("video", "video/ogg"),
    ".mov": ("video", "video/quicktime"),
}
# Скільки живе посилання на перегляд: картку не тримають відкритою довше.
PREVIEW_MAX_AGE = 6 * 3600


def media_of(uf):
    """(«image» | «video», MIME) для вкладення з переглядом, інакше None."""
    ext = os.path.splitext(uf.filename or uf.stored_name or "")[1].lower()
    return MEDIA_TYPES.get(ext)


def _preview_signer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"],
                                  salt="attachment-preview")


def preview_url(uf, user):
    token = _preview_signer().dumps({"f": uf.id, "u": user.id})
    return f"/api/catalog/files/{uf.id}/preview?sig={token}"


def read_preview_token(token):
    """Розшифровує підпис посилання; None — якщо підробка чи строк минув."""
    from itsdangerous import BadSignature
    try:
        return _preview_signer().loads(token or "", max_age=PREVIEW_MAX_AGE)
    except BadSignature:   # SignatureExpired — теж BadSignature
        return None


# -------------------------- Зображення в тексті --------------------------
#
# Картинка всередині тексту матеріалу — це файл картки з позначкою «inline»
# (фідбек тестування, п. 3.2). У тексті (Markdown) лежить постійне посилання
# `/api/catalog/files/<id>/preview`, а показує його інтерфейс через підписане
# посилання — так само, як прев'ю вкладень. Поки картки ще немає (майстер
# створення), картинка «чекає» без прив'язки й видна лише тому, хто її
# завантажив; під час збереження матеріалу вона прив'язується до картки, а
# прибрані з тексту картинки видаляються.

INLINE_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
INLINE_REF_RE = re.compile(r"/api/catalog/files/(\d+)/preview")
# Скільки чекає картинка, вставлена в незбережений матеріал.
INLINE_PENDING_TTL = timedelta(days=2)

# Перші байти форматів: тип визначаємо за вмістом, а не лише за назвою.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"), (b"GIF89a", ".gif"),
)


def _image_ext(data):
    for sig, ext in _MAGIC:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return None


def inline_ref(uf):
    return f"/api/catalog/files/{uf.id}/preview"


def inline_ids(*texts):
    """Номери файлів, на які посилаються тексти матеріалу."""
    ids = set()
    for text in texts:
        ids.update(int(m) for m in INLINE_REF_RE.findall(text or ""))
    return ids


def save_inline_image(user, filename, data):
    """Зберігає картинку для тексту матеріалу (ще без прив'язки до картки)."""
    max_bytes = current_app.config.get("INLINE_IMAGE_MAX_BYTES", 10 * 1024 * 1024)
    if len(data) > max_bytes:
        raise ApiError(f"Зображення більше за {max_bytes // (1024 * 1024)} МБ — "
                       "зменште його або додайте до картки файлом", 400, "file_too_large")
    ext = _image_ext(data)
    if ext is None:
        raise ApiError("У текст можна вставити зображення PNG, JPEG, GIF, WebP або AVIF",
                       400, "unsupported_media")
    _purge_pending(user)
    base = os.path.splitext(_safe_basename(filename or "image"))[0] or "image"
    directory = user_dir(user)
    stored = _unique_name(directory, f"{base}{'.jpg' if ext == '.jpeg' else ext}")
    with open(os.path.join(directory, stored), "wb") as fh:
        fh.write(data)
    uf = UserFile(user_id=user.id, filename=stored, stored_name=stored,
                  content_type=mimetypes.guess_type(stored)[0] or "application/octet-stream",
                  size=len(data), source="inline", resource_id=None)
    db.session.add(uf)
    db.session.commit()
    return uf


def _purge_pending(user):
    """Прибирає картинки з незбережених матеріалів, старші за дві доби."""
    limit = datetime.utcnow() - INLINE_PENDING_TTL
    stale = UserFile.query.filter(UserFile.user_id == user.id, UserFile.source == "inline",
                                  UserFile.resource_id.is_(None),
                                  UserFile.created_at < limit).all()
    for uf in stale:
        remove_stored_file(uf)
        db.session.delete(uf)


def sync_inline_images(res, user):
    """Після збереження тексту: прив'язує до картки картинки, вставлені цим
    користувачем, і видаляє картинки картки, яких у тексті більше немає."""
    used = inline_ids(res.body)
    if used:
        pending = UserFile.query.filter(UserFile.id.in_(used), UserFile.source == "inline",
                                        UserFile.resource_id.is_(None),
                                        UserFile.user_id == user.id).all()
        for uf in pending:
            uf.resource_id = res.id
    for uf in UserFile.query.filter_by(resource_id=res.id, source="inline").all():
        if uf.id not in used:
            remove_stored_file(uf)
            db.session.delete(uf)
