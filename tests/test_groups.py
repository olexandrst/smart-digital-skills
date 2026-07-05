"""Тест правила «мінімум один менеджер у групі»."""
from app.extensions import db
from app.models import User, Group, GroupMembership
from app.services import group_service


def test_last_manager_removal_promotes_admin(app):
    admin = User.query.filter_by(username="admin").first()
    u1 = User.query.filter_by(username="u1").first()

    # Група, де менеджер — НЕ системний admin.
    group = Group(name="GM", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=u1.id,
                                   role="manager", status="active"))
    db.session.commit()

    group_service.remove_member(group.id, u1.id)

    # Після видалення останнього менеджера системний admin призначається менеджером.
    admin_m = GroupMembership.query.filter_by(
        group_id=group.id, user_id=admin.id, role="manager", status="active").first()
    assert admin_m is not None
