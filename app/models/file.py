"""Файли користувачів (зберігаються назавжди у підкаталозі з GUID-назвою)."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class UserFile(db.Model):
    __tablename__ = "user_files"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    filename = db.Column(db.String, nullable=False)      # відображувана назва (може містити шлях)
    stored_name = db.Column(db.String, nullable=False)   # фізична назва у теці користувача
    content_type = db.Column(db.String)
    size = db.Column(db.Integer, nullable=False, default=0)
    # upload — завантажив користувач; skill_run — створив скіл;
    # attachment — вкладення до картки каталогу (FR-03).
    source = db.Column(db.String, nullable=False, default="upload")
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"))     # якщо створено скілом
    # Картка каталогу, до якої прикріплено файл. Файл фізично лишається у сховищі
    # користувача, який його завантажив, — другого сховища не заводимо.
    resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    user = db.relationship("User", lazy="joined")

    @property
    def public_url(self):
        """Публічне посилання /files/<GUID>/<FILENAME> (без /api, клікабельне)."""
        guid = self.user.storage_uid if self.user else ""
        return f"/files/{guid}/{self.stored_name}"

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "source": self.source,
            "skill_id": self.skill_id,
            "resource_id": self.resource_id,
            "uploaded_by": (self.user.full_name or self.user.username) if self.user else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "url": self.public_url,
            "download_url": (f"/api/catalog/resources/{self.resource_id}/files/{self.id}/download"
                             if self.resource_id else f"/api/files/{self.id}/download"),
        }
