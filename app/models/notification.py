"""Сповіщення всередині застосунку (AKH-18).

Основне призначення — нагадування власникам контенту про перегляд матеріалів.
Модель навмисно загальна: `kind` дозволяє додати інші приводи без нової
таблиці, а `dedupe_key` гарантує, що те саме нагадування не прийде двічі —
зокрема коли застосунок працює в кількох процесах і планувальник спрацював у
кожному з них.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# review_due / review_overdue — нагадування власникові про перегляд;
# review_requested — менеджерам розділу: матеріал подано на розгляд або
#   автор змінив опубліковане й чекає схвалення змін;
# content_updated — менеджерам розділу: до опублікованого матеріалу додали файл;
# content_moderated — авторові: рішення менеджера (схвалено, повернуто, знято
#   з публікації, перенесено в архів, видалено) з причиною, якщо вказана;
# comment_added — авторові: новий коментар до його матеріалу.
# content_published лишається в історії з часів пост-модерації.
NOTIFICATION_KINDS = ("review_due", "review_overdue", "review_requested",
                      "content_published", "content_updated", "content_moderated",
                      "comment_added")


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (db.UniqueConstraint("user_id", "dedupe_key"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    kind = db.Column(db.String, nullable=False)
    title = db.Column(db.String, nullable=False)
    body = db.Column(db.Text)
    # Матеріал або навичка, якого стосується сповіщення (якщо застосовно).
    resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id",
                                                      ondelete="CASCADE"))
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="CASCADE"))
    # Ключ проти повторів: «цей привід для цього матеріалу на цю дату».
    dedupe_key = db.Column(db.String, nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    resource = db.relationship("CatalogResource", lazy="joined")
    skill = db.relationship("Skill", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "resource_id": self.resource_id,
            "resource_name": self.resource.name if self.resource else None,
            "skill_id": self.skill_id,
            "skill_name": self.skill.name if self.skill else None,
            "is_read": self.is_read,
            "created_at": _iso(self.created_at),
        }
