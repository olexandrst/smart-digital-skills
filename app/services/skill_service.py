"""SkillService — доступ до навичок (власний + за групами) та лічильник активацій.

Ефективний доступ користувача = власні активації (user_skills, is_active)
∪ навички груп, де він активний учасник (group_skills, обчислюється динамічно).
Тому самостійне вилучення навички НЕ прибирає доступ, наданий групою.
"""
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, GroupSkill, GroupMembership, AppSetting,
)

# Ключ налаштування «Дозволити індивідуальний доступ» (Каталог + самоактивація).
INDIVIDUAL_ACCESS_KEY = "skills_individual_access"


def individual_access_enabled():
    """Чи можуть користувачі самі встановлювати/вилучати навички (Каталог)."""
    return AppSetting.get(INDIVIDUAL_ACCESS_KEY, "1") == "1"


def set_individual_access(enabled):
    AppSetting.set(INDIVIDUAL_ACCESS_KEY, "1" if enabled else "0")
    db.session.commit()
    return individual_access_enabled()


def effective_skill_ids(user_id):
    """Ефективний доступ: власні активації ∪ навички активних груп користувача."""
    self_ids = {us.skill_id for us in UserSkill.query.filter_by(
        user_id=user_id, is_active=True).all()}
    gids = [m.group_id for m in GroupMembership.query.filter_by(
        user_id=user_id, status="active").all()]
    group_ids = set()
    if gids:
        group_ids = {gs.skill_id for gs in GroupSkill.query.filter(
            GroupSkill.group_id.in_(gids), GroupSkill.is_active == True).all()}  # noqa: E712
    return self_ids | group_ids


def recompute_activations(skill_id):
    """activations_count = к-сть унікальних користувачів з ефективним доступом
    (власні активації ∪ активні учасники груп з активним призначенням)."""
    self_users = {us.user_id for us in UserSkill.query.filter_by(
        skill_id=skill_id, is_active=True).all()}
    gids = [gs.group_id for gs in GroupSkill.query.filter_by(
        skill_id=skill_id, is_active=True).all()]
    group_users = set()
    if gids:
        group_users = {m.user_id for m in GroupMembership.query.filter(
            GroupMembership.group_id.in_(gids),
            GroupMembership.status == "active").all()}
    count = len(self_users | group_users)
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
