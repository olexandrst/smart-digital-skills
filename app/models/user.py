"""Користувачі, глобальні ролі та refresh-токени."""
import uuid
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _uid():
    return uuid.uuid4().hex


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, nullable=False, unique=True)  # 'admin', 'skill_manager'
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text)

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name,
                "description": self.description}


class UserRole(db.Model):
    __tablename__ = "user_roles"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"),
                        primary_key=True)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, nullable=False, unique=True)
    email = db.Column(db.String, unique=True)
    password_hash = db.Column(db.String)  # NULL для Entra ID (Етап 2)
    full_name = db.Column(db.String)
    auth_provider = db.Column(db.String, nullable=False, default="local")
    external_id = db.Column(db.String)  # Entra ID object id (Етап 2)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_system_admin = db.Column(db.Boolean, nullable=False, default=False)
    # GUID-назва підкаталогу для файлів користувача (instance/user_files/<uid>).
    storage_uid = db.Column(db.String, unique=True, default=_uid)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    roles = db.relationship(
        "Role", secondary="user_roles", lazy="joined",
        backref=db.backref("users", lazy="select"),
    )

    @property
    def role_codes(self):
        codes = {r.code for r in self.roles}
        if self.is_system_admin:
            codes.add("admin")
        return sorted(codes)

    def has_global_role(self, code):
        if code == "admin" and self.is_system_admin:
            return True
        return any(r.code == code for r in self.roles)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "auth_provider": self.auth_provider,
            "is_active": self.is_active,
            "is_system_admin": self.is_system_admin,
            "roles": self.role_codes,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RefreshToken(db.Model):
    __tablename__ = "refresh_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    token_hash = db.Column(db.String, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
