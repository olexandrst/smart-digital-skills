"""GroupService — членство та правило «мінімум один менеджер у групі»."""
from app.extensions import db
from app.core.errors import ApiError
from app.models import Group, GroupMembership, User
from app.services import skill_service


def add_member(group_id, user_id, role="member", invited_by=None):
    group = Group.query.get(group_id)
    if group is None:
        raise ApiError("Групу не знайдено", 404, "not_found")
    if User.query.get(user_id) is None:
        raise ApiError("Користувача не знайдено", 404, "not_found")

    membership = GroupMembership.query.filter_by(
        group_id=group_id, user_id=user_id).first()
    if membership and membership.status == "active":
        raise ApiError("Користувач уже в групі", 409, "conflict")

    if membership:
        membership.status = "active"
        membership.role = role
    else:
        membership = GroupMembership(
            group_id=group_id, user_id=user_id, role=role,
            status="active", invited_by=invited_by,
        )
        db.session.add(membership)
    db.session.commit()

    skill_service.sync_member_skills(group_id, user_id, assigned_by=invited_by)
    return membership


def remove_member(group_id, user_id):
    membership = GroupMembership.query.filter_by(
        group_id=group_id, user_id=user_id, status="active").first()
    if membership is None:
        raise ApiError("Користувач не є активним членом групи", 404, "not_found")

    membership.status = "removed"
    db.session.commit()

    skill_service.remove_member_skills(group_id, user_id)
    _ensure_manager_exists(group_id)
    return True


def change_role(group_id, user_id, role):
    if role not in ("manager", "member"):
        raise ApiError("Невідома роль", 400, "validation_error")
    membership = GroupMembership.query.filter_by(
        group_id=group_id, user_id=user_id, status="active").first()
    if membership is None:
        raise ApiError("Користувач не є активним членом групи", 404, "not_found")

    membership.role = role
    db.session.commit()
    _ensure_manager_exists(group_id)
    return membership


def _ensure_manager_exists(group_id):
    """Бізнес-правило: якщо менеджерів не лишилось — системний admin стає менеджером."""
    has_manager = GroupMembership.query.filter_by(
        group_id=group_id, role="manager", status="active").first()
    if has_manager:
        return

    admin = User.query.filter_by(is_system_admin=True, is_active=True).first()
    if admin is None:
        return

    membership = GroupMembership.query.filter_by(
        group_id=group_id, user_id=admin.id).first()
    if membership:
        membership.role = "manager"
        membership.status = "active"
    else:
        db.session.add(GroupMembership(
            group_id=group_id, user_id=admin.id, role="manager", status="active",
        ))
    db.session.commit()
    skill_service.sync_member_skills(group_id, admin.id, assigned_by=admin.id)
