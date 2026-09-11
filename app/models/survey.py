"""Опитування задоволеності: NPS, CSAT, CES (розділ 11 BRD).

Три шкали з різним змістом, тому нормалізувати їх в одну не можна:
NPS — 0–10 «чи порекомендуєте», CSAT — 1–5 «чи задоволені», CES — 1–5
«наскільки легко було». Зберігаємо сиру оцінку плюс контекст, де запитали.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


SURVEY_KINDS = ("nps", "csat", "ces")

# Межі шкал: (мінімум, максимум) для кожного типу опитування.
SURVEY_SCALES = {"nps": (0, 10), "csat": (1, 5), "ces": (1, 5)}

# Класифікація NPS: промоутери 9–10, нейтрали 7–8, критики 0–6.
NPS_PROMOTER_FROM = 9
NPS_PASSIVE_FROM = 7


class SurveyResponse(db.Model):
    __tablename__ = "survey_responses"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String, nullable=False)          # SURVEY_KINDS
    score = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    # Роль на момент відповіді: розріз «що думають менеджери проти всіх інших».
    user_role = db.Column(db.String)
    # Де саме запитали: catalog | resource | search — щоб оцінка мала контекст.
    context = db.Column(db.String)
    context_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    day = db.Column(db.Date, nullable=False, index=True)

    @staticmethod
    def nps_bucket(score):
        if score >= NPS_PROMOTER_FROM:
            return "promoter"
        if score >= NPS_PASSIVE_FROM:
            return "passive"
        return "detractor"

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "score": self.score,
            "comment": self.comment,
            "user_role": self.user_role,
            "context": self.context,
            "created_at": _iso(self.created_at),
        }


class SurveyPrompt(db.Model):
    """Коли користувача востаннє питали — щоб не питати частіше за інтервал.

    Окремий рядок на пару «користувач + тип опитування»: закрити NPS не має
    відкладати CSAT, бо це різні питання.
    """
    __tablename__ = "survey_prompts"
    __table_args__ = (db.UniqueConstraint("user_id", "kind"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    kind = db.Column(db.String, nullable=False)
    shown_at = db.Column(db.DateTime, nullable=False, default=_now)
    # True — відповів, False — закрив без відповіді. Відкладаємо в обох випадках,
    # але відповівшого можна не питати значно довше.
    answered = db.Column(db.Boolean, nullable=False, default=False)
