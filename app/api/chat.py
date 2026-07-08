"""Чат: сесії з обраною моделлю, повідомлення та застосування скілів."""
from flask import Blueprint, request, jsonify
from app.core.permissions import require_auth
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import ChatSession
from app.services import chat_service

bp = Blueprint("chat", __name__)


@bp.get("/sessions")
@require_auth
def list_sessions():
    user = current_user()
    sessions = ChatSession.query.filter_by(user_id=user.id).order_by(
        ChatSession.updated_at.desc()).all()
    return jsonify([s.to_dict() for s in sessions])


@bp.post("/sessions")
@require_auth
def create_session():
    """Створює чат. `model_id` необовʼязковий — модель обирається автоматично."""
    data = request.get_json(silent=True) or {}
    session = chat_service.create_session(
        current_user(), model_id=data.get("model_id"), title=data.get("title"))
    return jsonify(session.to_dict()), 201


@bp.patch("/sessions/<int:session_id>")
@require_auth
def update_session(session_id):
    """Зміна моделі чату (перемикач моделі у верхній панелі)."""
    data = request.get_json(silent=True) or {}
    model_id = data.get("model_id")
    if not model_id:
        raise ApiError("Вкажіть model_id", 400, "validation_error")
    session = chat_service.set_session_model(current_user(), session_id, model_id)
    return jsonify(session.to_dict())


@bp.get("/sessions/<int:session_id>")
@require_auth
def get_session(session_id):
    user = current_user()
    session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first_or_404()
    return jsonify(session.to_dict(include_messages=True))


@bp.post("/sessions/<int:session_id>/messages")
@require_auth
def send_message(session_id):
    data = request.get_json(silent=True) or {}
    result = chat_service.send_message(
        current_user(), session_id,
        content=data.get("content", ""),
        skill_id=data.get("skill_id"),
    )
    return jsonify(result)


@bp.delete("/sessions/<int:session_id>")
@require_auth
def delete_session(session_id):
    chat_service.delete_session(current_user(), session_id)
    return jsonify({"message": "Сесію видалено"})
