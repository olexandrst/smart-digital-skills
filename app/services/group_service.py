"""GroupService — членство у групах (без ролей: усі члени рівноправні)."""
from app.extensions import db
from app.core.errors import ApiError
from app.models import Group, GroupMembership, GroupSkill, User


def add_member(group_id, user_id, role="member", invited_by=None):
    """Додає активного члена групи. `role` збережено для сумісності — не вживається."""
    from app.services import skill_service  # локальний імпорт уникає циклів
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
        membership.role = "member"
    else:
        membership = GroupMembership(
            group_id=group_id, user_id=user_id, role="member",
            status="active", invited_by=invited_by,
        )
        db.session.add(membership)
    db.session.commit()

    skill_service.sync_member_skills(group_id, user_id, assigned_by=invited_by)
    return membership


def remove_member(group_id, user_id):
    from app.services import skill_service
    membership = GroupMembership.query.filter_by(
        group_id=group_id, user_id=user_id, status="active").first()
    if membership is None:
        raise ApiError("Користувач не є активним членом групи", 404, "not_found")

    membership.status = "removed"
    db.session.commit()

    skill_service.remove_member_skills(group_id, user_id)
    return True


def rename_group(group_id, name=None, description=None):
    group = Group.query.get(group_id)
    if group is None:
        raise ApiError("Групу не знайдено", 404, "not_found")
    if name is not None:
        name = name.strip()
        if not name:
            raise ApiError("Назва групи не може бути порожньою", 400, "validation_error")
        clash = Group.query.filter(Group.name == name, Group.id != group_id).first()
        if clash:
            raise ApiError("Група з такою назвою вже існує", 409, "conflict")
        group.name = name
    if description is not None:
        group.description = description
    db.session.commit()
    return group


def delete_group(group_id):
    """Видаляє групу: знімає доступ до її навичок у членів і видаляє зв'язки."""
    from app.services import skill_service
    group = Group.query.get(group_id)
    if group is None:
        raise ApiError("Групу не знайдено", 404, "not_found")

    # Деактивуємо доступ членів до навичок, що надавались саме цією групою.
    for gs in GroupSkill.query.filter_by(group_id=group_id).all():
        skill_service.remove_from_group(group_id, gs.skill_id)
    GroupSkill.query.filter_by(group_id=group_id).delete(synchronize_session=False)

    db.session.delete(group)  # memberships — каскадом (relationship cascade)
    db.session.commit()
    return True
