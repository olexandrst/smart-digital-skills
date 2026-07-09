"""Прості системні налаштування (ключ → значення)."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String, nullable=False, unique=True)
    value = db.Column(db.String)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    @staticmethod
    def get(key, default=None):
        row = AppSetting.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = AppSetting.query.filter_by(key=key).first()
        if row is None:
            row = AppSetting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value
        return row
