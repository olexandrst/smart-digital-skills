"""Воронка ідей (Idea Funnel) — вхідна точка процесу збору ідей (BR-15, FR-10).

Hub збирає й маршрутизує ідеї, але не веде їхнє опрацювання: коли ідею беруть
у роботу, її віддають у зовнішній процес оцінки (`external_url`), а тут
лишається статус і слід у журналі.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# Життєвий цикл ідеї. submitted — щойно подано; in_review — на розгляді;
# accepted — прийнято в роботу; rejected — відхилено; implemented — реалізовано
# (тоді зазвичай заповнене `resource_id` — картка, що з ідеї вийшла).
IDEA_STATUSES = ("submitted", "in_review", "accepted", "rejected", "implemented")

# Статуси, після яких ідея вважається закритою (для черги менеджера).
IDEA_CLOSED_STATUSES = ("rejected", "implemented")


class Idea(db.Model):
    __tablename__ = "ideas"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    body = db.Column(db.Text, nullable=False)            # суть ідеї
    problem = db.Column(db.Text)                         # бізнес-задача
    expected_effect = db.Column(db.Text)                 # очікуваний ефект
    contact = db.Column(db.String)                       # як зв'язатися з автором

    author_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    author_name = db.Column(db.String)                   # знімок імені на момент подання

    # Матеріал, з якого ідея виникла (необов'язково).
    source_resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id"))
    # Картка, що вийшла з ідеї, коли її реалізували.
    resource_id = db.Column(db.Integer, db.ForeignKey("catalog_resources.id"))
    # Посилання на зовнішній процес оцінки, куди ідею передали.
    external_url = db.Column(db.String)

    status = db.Column(db.String, nullable=False, default="submitted")
    # Останній коментар менеджера до зміни статусу — щоб автор бачив «чому».
    status_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    author = db.relationship("User", lazy="joined", foreign_keys=[author_id])
    source_resource = db.relationship("CatalogResource", lazy="joined",
                                      foreign_keys=[source_resource_id])
    resource = db.relationship("CatalogResource", lazy="joined",
                               foreign_keys=[resource_id])

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "problem": self.problem,
            "expected_effect": self.expected_effect,
            "contact": self.contact,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "source_resource_id": self.source_resource_id,
            "source_resource_name": (self.source_resource.name
                                     if self.source_resource else None),
            "resource_id": self.resource_id,
            "resource_name": self.resource.name if self.resource else None,
            "external_url": self.external_url,
            "status": self.status,
            "status_note": self.status_note,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }
