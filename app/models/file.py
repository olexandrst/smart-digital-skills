"""Вкладення до карток каталогу (FR-03).

Файл фізично лежить у сховищі того, хто його завантажив
(`<USER_FILES_DIR>/<user.storage_uid>/`), — окремого сховища для вкладень
не заводимо. Віддається лише через авторизований маршрут картки, тож бачать
його рівно ті, хто бачить саму картку.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class UserFile(db.Model):
    __tablename__ = "user_files"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    filename = db.Column(db.String, nullable=False)      # відображувана назва
    stored_name = db.Column(db.String, nullable=False)   # фізична назва у теці користувача
    content_type = db.Column(db.String)
    size = db.Column(db.Integer, nullable=False, default=0)
    # Походження файлу. Зараз єдине — attachment (вкладення до картки); колонку
    # лишено, щоб історичні записи («upload», «skill_run») не ламали читання.
    source = db.Column(db.String, nullable=False, default="attachment")
    # Картка каталогу, до якої прикріплено файл.
    resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    user = db.relationship("User", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "resource_id": self.resource_id,
            "uploaded_by": (self.user.full_name or self.user.username) if self.user else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "download_url": (f"/api/catalog/resources/{self.resource_id}/files/{self.id}/download"
                             if self.resource_id else None),
        }
