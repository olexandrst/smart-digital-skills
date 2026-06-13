"""Автентифікація: логін/пароль, видача та оновлення JWT."""
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity,
)
from app.extensions import db
from app.core.security import verify_password, current_user
from app.core.permissions import require_auth
from app.core.errors import ApiError
from app.models import User

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise ApiError("Вкажіть логін і пароль", 400, "validation_error")

    user = User.query.filter_by(username=username).first()
    if user is None or not verify_password(user.password_hash, password):
        raise ApiError("Невірний логін або пароль", 401, "invalid_credentials")
    if not user.is_active:
        raise ApiError("Обліковий запис деактивовано", 403, "inactive")

    user.last_login_at = datetime.utcnow()
    db.session.commit()

    identity = str(user.id)
    return jsonify({
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
        "user": user.to_dict(),
    })


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    return jsonify({"access_token": create_access_token(identity=identity)})


@bp.get("/me")
@require_auth
def me():
    return jsonify(current_user().to_dict())
