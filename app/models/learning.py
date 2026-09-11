"""Навчальні маршрути: упорядковані послідовності матеріалів (BR-9).

Маршрут веде користувача від «не знаю, з чого почати» до конкретного рівня
зрілості. Крок посилається або на картку каталогу, або на зовнішнє посилання —
щоб маршрут можна було скласти навіть із того, що поки живе поза хабом.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


class LearningPath(db.Model):
    __tablename__ = "learning_paths"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    description = db.Column(db.Text)
    icon_emoji = db.Column(db.String)
    # Для якого рівня зрілості та ролі маршрут (обидва — необов'язкові).
    maturity_level_id = db.Column(db.Integer, db.ForeignKey("catalog_terms.id"))
    audience_role = db.Column(db.String)
    # Стартовий маршрут пропонується тим, хто ще жодного не почав.
    is_starter = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    maturity_level = db.relationship("CatalogTerm", lazy="joined")
    steps = db.relationship("LearningPathStep", lazy="selectin",
                            order_by="LearningPathStep.position",
                            cascade="all, delete-orphan", back_populates="path")

    def to_dict(self, progress=None):
        steps = [s.to_dict(done=(progress or set())) for s in self.steps]
        done = sum(1 for s in steps if s["done"])
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon_emoji": self.icon_emoji,
            "maturity_level_id": self.maturity_level_id,
            "maturity_level_name": (self.maturity_level.name
                                    if self.maturity_level else None),
            "audience_role": self.audience_role,
            "is_starter": self.is_starter,
            "position": self.position,
            "is_active": self.is_active,
            "steps": steps,
            "steps_total": len(steps),
            "steps_done": done,
            "progress_pct": round(done / len(steps) * 100) if steps else 0,
            # Наступний незавершений крок — щоб «продовжити» вело саме туди,
            # де користувач зупинився, а не на початок маршруту.
            "next_step_id": next((s["id"] for s in steps if not s["done"]), None),
        }
        return data


class LearningPathStep(db.Model):
    """Крок маршруту: картка каталогу або зовнішнє посилання.

    `resource_id` навмисно без каскадного видалення: якщо картку прибрали з
    каталогу, крок лишається на місці й позначається недоступним — інакше
    маршрут мовчки коротшав би, а прогрес користувачів «зсувався».
    """
    __tablename__ = "learning_path_steps"

    id = db.Column(db.Integer, primary_key=True)
    path_id = db.Column(db.Integer,
                        db.ForeignKey("learning_paths.id", ondelete="CASCADE"),
                        nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    title = db.Column(db.String)            # власна назва кроку (необов'язково)
    note = db.Column(db.Text)               # що саме зробити на цьому кроці
    resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id"))
    external_url = db.Column(db.String)     # якщо матеріал поки живе поза хабом

    path = db.relationship("LearningPath", back_populates="steps")
    resource = db.relationship("CatalogResource", lazy="joined")

    def to_dict(self, done=()):
        available = self.resource is not None or bool(self.external_url)
        return {
            "id": self.id,
            "position": self.position,
            "title": self.title or (self.resource.name if self.resource else None),
            "note": self.note,
            "resource_id": self.resource_id,
            "resource_name": self.resource.name if self.resource else None,
            "resource_type": (self.resource.resource_type if self.resource else None),
            "external_url": self.external_url,
            # Крок, чия картка зникла з каталогу, лишається видимим, але
            # позначеним: користувач має розуміти, чому він не відкривається.
            "available": available,
            "done": self.id in done,
        }


class LearningProgress(db.Model):
    """Пройдений крок конкретного користувача."""
    __tablename__ = "learning_progress"
    __table_args__ = (db.UniqueConstraint("user_id", "step_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    path_id = db.Column(db.Integer,
                        db.ForeignKey("learning_paths.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    step_id = db.Column(db.Integer,
                        db.ForeignKey("learning_path_steps.id", ondelete="CASCADE"),
                        nullable=False)
    completed_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {"path_id": self.path_id, "step_id": self.step_id,
                "completed_at": _iso(self.completed_at)}


class HiddenRecommendation(db.Model):
    """Рекомендація, яку користувач приховав (AKH-15).

    Зберігаємо назавжди: «більше не показувати» має означати саме це, а не
    «не показувати до наступного перерахунку».
    """
    __tablename__ = "hidden_recommendations"
    __table_args__ = (db.UniqueConstraint("user_id", "item_type", "item_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    item_type = db.Column(db.String, nullable=False, default="resource")
    item_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
