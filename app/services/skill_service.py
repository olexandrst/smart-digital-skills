"""SkillService — центральна логіка подвійного призначення та лічильника активацій.

Ефективний доступ матеріалізується в user_skills (одна стрічка на user+skill).
Це дає природний підрахунок унікальних активацій та реалізує правило
«плюс усі члени групи, крім тих, у кого скіл уже був».
"""
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, GroupSkill, GroupMembership,
)


def recompute_activations(skill_id):
    """activations_count = к-сть унікальних користувачів з активним доступом."""
    count = UserSkill.query.filter_by(skill_id=skill_id, is_active=True).count()
    skill = Skill.query.get(skill_id)
    if skill:
        skill.activations_count = count
    return count


def _require_published(skill_id):
    skill = Skill.query.get(skill_id)
    if skill is None:
        raise ApiError("Навичку не знайдено", 404, "not_found")
    if skill.status != "published":
        raise ApiError("Призначати/активувати можна лише опубліковані навички",
                        400, "skill_not_published")
    return skill


def self_activate(user_id, skill_id):
    """Самостійна активація published-скіла користувачем для себе."""
    _require_published(skill_id)
    existing = UserSkill.query.filter_by(user_id=user_id, skill_id=skill_id).first()
    if existing:
        # Самостійна активація — явна дія користувача: робимо доступ "власним",
        # щоб зняття скіла з групи його не прибирало (правило "інше джерело").
        existing.is_active = True
        existing.source = "self"
        existing.group_id = None
        existing.assigned_by = user_id
    else:
        db.session.add(UserSkill(
            user_id=user_id, skill_id=skill_id, source="self",
            assigned_by=user_id, is_active=True,
        ))
    db.session.flush()
    recompute_activations(skill_id)
    db.session.commit()
    return recompute_activations(skill_id)


def self_deactivate(user_id, skill_id):
    """Самостійне вилучення навички: деактивує доступ користувача до неї."""
    us = UserSkill.query.filter_by(user_id=user_id, skill_id=skill_id,
                                   is_active=True).first()
    if us:
        us.is_active = False
    db.session.flush()
    count = recompute_activations(skill_id)
    db.session.commit()
    return count


def assign_to_group(group_id, skill_id, assigned_by):
    """Призначення скіла групі: group_skills + матеріалізація для активних мемберів."""
    _require_published(skill_id)

    gs = GroupSkill.query.filter_by(group_id=group_id, skill_id=skill_id).first()
    if gs is None:
        gs = GroupSkill(group_id=group_id, skill_id=skill_id,
                        assigned_by=assigned_by, is_active=True)
        db.session.add(gs)
    else:
        gs.is_active = True

    members = GroupMembership.query.filter_by(group_id=group_id, status="active").all()
    for m in members:
        _ensure_user_skill(m.user_id, skill_id, source="group",
                           group_id=group_id, assigned_by=assigned_by)

    db.session.flush()
    count = recompute_activations(skill_id)
    db.session.commit()
    return count


def remove_from_group(group_id, skill_id):
    """Зняття скіла з групи: деактивує group-доступ для тих, хто не має іншого джерела."""
    gs = GroupSkill.query.filter_by(group_id=group_id, skill_id=skill_id).first()
    if gs:
        gs.is_active = False

    members = GroupMembership.query.filter_by(group_id=group_id, status="active").all()
    for m in members:
        _deactivate_group_access(m.user_id, skill_id, group_id)

    db.session.flush()
    count = recompute_activations(skill_id)
    db.session.commit()
    return count


def sync_member_skills(group_id, user_id, assigned_by=None):
    """Новий активний мембер успадковує всі активні скіли групи."""
    group_skills = GroupSkill.query.filter_by(group_id=group_id, is_active=True).all()
    affected = []
    for gs in group_skills:
        _ensure_user_skill(user_id, gs.skill_id, source="group",
                           group_id=group_id, assigned_by=assigned_by)
        affected.append(gs.skill_id)
    db.session.flush()
    for skill_id in affected:
        recompute_activations(skill_id)
    db.session.commit()
    return affected


def remove_member_skills(group_id, user_id):
    """При виході мембера: знімаємо group-доступ цієї групи, якщо немає іншого джерела."""
    group_skills = GroupSkill.query.filter_by(group_id=group_id).all()
    affected = []
    for gs in group_skills:
        if _deactivate_group_access(user_id, gs.skill_id, group_id):
            affected.append(gs.skill_id)
    db.session.flush()
    for skill_id in affected:
        recompute_activations(skill_id)
    db.session.commit()
    return affected


def _ensure_user_skill(user_id, skill_id, source, group_id=None, assigned_by=None):
    """INSERT OR IGNORE-семантика: не дублюємо доступ, не подвоюємо активації."""
    us = UserSkill.query.filter_by(user_id=user_id, skill_id=skill_id).first()
    if us is None:
        db.session.add(UserSkill(
            user_id=user_id, skill_id=skill_id, source=source,
            group_id=group_id, assigned_by=assigned_by, is_active=True,
        ))
        return True
    if not us.is_active:
        us.is_active = True
        us.source = source
        us.group_id = group_id
        return True
    return False


def _deactivate_group_access(user_id, skill_id, group_id):
    """Деактивує доступ, лише якщо він прийшов саме з цієї групи (не self/інша група)."""
    us = UserSkill.query.filter_by(user_id=user_id, skill_id=skill_id,
                                   is_active=True).first()
    if us and us.source == "group" and us.group_id == group_id:
        us.is_active = False
        return True
    return False
