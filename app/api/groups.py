"""Групи: створення/перейменування/видалення (Admin), членство (без ролей).

Ролей у групах немає — усі учасники рівноправні. Керування складом групи
(додати/вилучити учасника) виконує Admin.
"""
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role, require_group_membership
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Group, GroupMembership, GroupSkill, Skill
from app.services import group_service, skill_service

bp = Blueprint("groups", __name__)


@bp.get("")
@require_auth
def list_groups():
    """Admin бачить усі групи; інші — лише ті, де вони активні члени."""
    user = current_user()
    if user.has_global_role("admin"):
        groups = Group.query.order_by(Group.id).all()
    else:
        group_ids = [m.group_id for m in GroupMembership.query.filter_by(
            user_id=user.id, status="active").all()]
        groups = Group.query.filter(Group.id.in_(group_ids)).all()
    return jsonify([g.to_dict() for g in groups])


@bp.post("")
@require_global_role("admin")
def create_group():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Вкажіть назву групи", 400, "validation_error")
    if Group.query.filter_by(name=name).first():
        raise ApiError("Група з такою назвою вже існує", 409, "conflict")

    group = Group(name=name, description=data.get("description"),
                  created_by=current_user().id)
    db.session.add(group)
    db.session.commit()
    return jsonify(group.to_dict(include_members=True)), 201


@bp.get("/<int:group_id>")
@require_group_membership()
def get_group(group_id):
    group = Group.query.get_or_404(group_id)
    return jsonify(group.to_dict(include_members=True))


@bp.patch("/<int:group_id>")
@require_global_role("admin")
def rename_group(group_id):
    data = request.get_json(silent=True) or {}
    group = group_service.rename_group(
        group_id, name=data.get("name"), description=data.get("description"))
    return jsonify(group.to_dict(include_members=True))


@bp.delete("/<int:group_id>")
@require_global_role("admin")
def delete_group(group_id):
    group_service.delete_group(group_id)
    return jsonify({"message": "Групу видалено"})


@bp.post("/<int:group_id>/members")
@require_global_role("admin")
def add_member(group_id):
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        raise ApiError("Вкажіть user_id", 400, "validation_error")
    membership = group_service.add_member(
        group_id, user_id, invited_by=current_user().id)
    return jsonify(membership.to_dict()), 201


@bp.delete("/<int:group_id>/members/<int:user_id>")
@require_global_role("admin")
def remove_member(group_id, user_id):
    group_service.remove_member(group_id, user_id)
    return jsonify({"message": "Учасника вилучено"})


@bp.get("/<int:group_id>/skills")
@require_group_membership()
def list_group_skills(group_id):
    rows = GroupSkill.query.filter_by(group_id=group_id, is_active=True).all()
    skill_ids = [r.skill_id for r in rows]
    skills = Skill.query.filter(Skill.id.in_(skill_ids)).all()
    return jsonify([s.to_dict() for s in skills])


@bp.post("/<int:group_id>/skills")
@require_global_role("admin")
def assign_skill(group_id):
    data = request.get_json(silent=True) or {}
    skill_id = data.get("skill_id")
    if not skill_id:
        raise ApiError("Вкажіть skill_id", 400, "validation_error")
    count = skill_service.assign_to_group(group_id, skill_id, current_user().id)
    return jsonify({"message": "Навичку призначено групі", "activations_count": count}), 201


@bp.delete("/<int:group_id>/skills/<int:skill_id>")
@require_global_role("admin")
def remove_skill(group_id, skill_id):
    count = skill_service.remove_from_group(group_id, skill_id)
    return jsonify({"message": "Навичку знято з групи", "activations_count": count})
