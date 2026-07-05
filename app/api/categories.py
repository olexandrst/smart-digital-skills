"""Категорії навичок: керований довідник (Admin/Skill Manager).

Список категорій використовується при редагуванні навичок (вибір зі списку).
При першому зверненні довідник «самозаповнюється» наявними категоріями навичок,
щоб історичні значення не губилися.
"""
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.errors import ApiError
from app.models import SkillCategory, Skill

bp = Blueprint("categories", __name__)


def _backfill_from_skills():
    """Створює відсутні категорії з наявних значень Skill.category (ідемпотентно)."""
    existing = {c.name for c in SkillCategory.query.all()}
    used = {row[0] for row in db.session.query(Skill.category).distinct().all()
            if row[0] and row[0].strip()}
    created = False
    for name in used - existing:
        db.session.add(SkillCategory(name=name.strip()))
        created = True
    if created:
        db.session.commit()


@bp.get("")
@require_auth
def list_categories():
    _backfill_from_skills()
    # Сортуємо в Python: SQLite lower()/сортування не враховує кирилицю.
    cats = sorted(SkillCategory.query.all(), key=lambda c: c.name.casefold())
    return jsonify([c.to_dict() for c in cats])


@bp.post("")
@require_global_role("admin", "skill_manager")
def create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Назва категорії не може бути порожньою", 400, "validation_error")
    # Порівняння без урахування регістру в Python (SQLite lower() лишає кирилицю).
    if any(c.name.casefold() == name.casefold() for c in SkillCategory.query.all()):
        raise ApiError("Така категорія вже існує", 409, "category_exists")
    cat = SkillCategory(name=name)
    db.session.add(cat)
    db.session.commit()
    return jsonify(cat.to_dict()), 201


@bp.delete("/<int:category_id>")
@require_global_role("admin", "skill_manager")
def delete_category(category_id):
    """Видаляє категорію з довідника. Навички зберігають свою назву категорії."""
    cat = SkillCategory.query.get_or_404(category_id)
    db.session.delete(cat)
    db.session.commit()
    return jsonify({"message": "Категорію видалено"})
