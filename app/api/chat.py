"""Чат-сесії: перегляд історії діалогів користувача."""
from flask import Blueprint, jsonify
from app.core.permissions import require_auth
from app.core.security import current_user
from app.models import ChatSession

bp = Blueprint("chat", __name__)


@bp.get("/sessions")
@require_auth
def list_sessions():
    user = current_user()
    sessions = ChatSession.query.filter_by(user_id=user.id).order_by(
        ChatSession.updated_at.desc()).all()
    return jsonify([s.to_dict() for s in sessions])


@bp.get("/sessions/<int:session_id>")
@require_auth
def get_session(session_id):
    user = current_user()
    session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first_or_404()
    return jsonify(session.to_dict(include_messages=True))
