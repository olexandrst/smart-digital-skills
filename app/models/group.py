"""Групи, членство та запрошення."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    memberships = db.relationship(
        "GroupMembership", backref="group", lazy="select",
        cascade="all, delete-orphan",
    )

    def to_dict(self, include_members=False):
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_members:
            data["members"] = [m.to_dict() for m in self.memberships
                               if m.status == "active"]
        return data


class GroupMembership(db.Model):
    __tablename__ = "group_memberships"
    __table_args__ = (db.UniqueConstraint("group_id", "user_id"),)

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="CASCADE"),
                         nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    role = db.Column(db.String, nullable=False, default="member")  # manager | member
    status = db.Column(db.String, nullable=False, default="active")  # invited|active|removed
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    joined_at = db.Column(db.DateTime, nullable=False, default=_now)

    user = db.relationship("User", foreign_keys=[user_id], lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "full_name": self.user.full_name if self.user else None,
            "role": self.role,
            "status": self.status,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
        }


class Invitation(db.Model):
    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="CASCADE"),
                         nullable=False)
    email = db.Column(db.String, nullable=False)
    role = db.Column(db.String, nullable=False, default="member")
    token = db.Column(db.String, nullable=False, unique=True)
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String, nullable=False, default="pending")
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    accepted_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "email": self.email,
            "role": self.role,
            "token": self.token,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
