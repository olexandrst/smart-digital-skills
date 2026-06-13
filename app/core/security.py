"""Хешування паролів та поточний користувач із JWT."""
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import get_jwt_identity
from app.models import User


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def current_user():
    """Повертає об'єкт User за identity з JWT (identity зберігаємо як рядок)."""
    identity = get_jwt_identity()
    if identity is None:
        return None
    return db_get_user(int(identity))


def db_get_user(user_id: int):
    return User.query.get(user_id)
