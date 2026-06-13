"""Тести правил активацій: груповий + self доступ без подвійного підрахунку."""
from app.extensions import db
from app.models import User, Skill, Group, GroupMembership
from app.services import skill_service, group_service


def _ids(app):
    skill = Skill.query.filter_by(name="Summarizer").first()
    return skill.id


def test_self_activation_counts_once(app):
    skill_id = _ids(app)
    u1 = User.query.filter_by(username="u1").first()

    assert skill_service.self_activate(u1.id, skill_id) == 1
    # Повторна активація не подвоює лічильник.
    assert skill_service.self_activate(u1.id, skill_id) == 1


def test_group_assignment_materializes_for_members(app):
    skill_id = _ids(app)
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()
    u2 = User.query.filter_by(username="u2").first()

    group = Group(name="G1", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=admin.id,
                                   role="manager", status="active"))
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id,
                                   role="member", status="active"))
    db.session.commit()

    count = skill_service.assign_to_group(group.id, skill_id, admin.id)
    assert count == 2  # admin + u1

    # u2 не в групі → доступу немає.
    from app.models import UserSkill
    assert UserSkill.query.filter_by(user_id=u2.id, skill_id=skill_id,
                                     is_active=True).first() is None


def test_no_double_count_self_then_group(app):
    skill_id = _ids(app)
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()

    skill_service.self_activate(u1.id, skill_id)  # u1 вже має скіл (self)

    group = Group(name="G2", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id,
                                   role="member", status="active"))
    db.session.commit()

    count = skill_service.assign_to_group(group.id, skill_id, admin.id)
    # u1 вже рахувався — лічильник не подвоюється.
    assert count == 1


def test_remove_member_keeps_self_access(app):
    skill_id = _ids(app)
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()

    group = Group(name="G3", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=admin.id,
                                   role="manager", status="active"))
    db.session.commit()

    group_service.add_member(group.id, u1.id, role="member", invited_by=admin.id)
    skill_service.assign_to_group(group.id, skill_id, admin.id)
    skill_service.self_activate(u1.id, skill_id)  # додаткове self-джерело

    group_service.remove_member(group.id, u1.id)
    # u1 втратив групу, але self-доступ лишається активним.
    from app.models import UserSkill
    us = UserSkill.query.filter_by(user_id=u1.id, skill_id=skill_id).first()
    assert us.is_active is True
