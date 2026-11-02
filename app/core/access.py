"""Хто що може в каталозі: ролі й розділи (фідбек тестування, п. 11).

Чотири ролі, призначає їх адміністратор вручну:

- Адміністратор — усе: користувачі, ролі, структура, довідники, маршрути,
  налаштування й модерація будь-якого розділу, зокрема матеріалів без розділу.
- Менеджер розділу — модерує матеріали й навички лише призначених йому
  розділів (одного чи кількох). У розділу є модератор і заступник: права
  однакові, хто з них діяв — фіксує журнал модерації (DEV-02).
- Користувач — переглядає каталог, створює чернетки й подає їх на розгляд.
- «Експерт» і «Відповідальний за ідеї» — ознаки в профілі, а не ролі.

Тут лише відповіді «так/ні»; ендпоінти вирішують, що з ними робити.
"""
from app.models import SectionManager, User

ADMIN = "admin"
SECTION_MANAGER = "section_manager"


def is_admin(user):
    return bool(user) and user.has_global_role(ADMIN)


def is_section_manager(user):
    return bool(user) and user.has_global_role(SECTION_MANAGER)


def managed_section_ids(user):
    """Розділи, які модерує людина. Адміністратору не потрібні — він може все."""
    if not is_section_manager(user):
        return set()
    return {s.id for s in user.managed_sections}


def is_moderator(user):
    """Чи модерує людина бодай щось: адміністратор або менеджер з розділами."""
    return is_admin(user) or bool(managed_section_ids(user))


def can_moderate_section(user, section_id):
    """Матеріали без розділу модерує лише адміністратор."""
    if is_admin(user):
        return True
    return section_id is not None and section_id in managed_section_ids(user)


def can_moderate(user, item):
    return can_moderate_section(user, getattr(item, "section_id", None))


def section_role(user, section_id):
    """'moderator' чи 'deputy' — хто людина в розділі; None — не модерує його."""
    if section_id is None or section_id not in managed_section_ids(user):
        return None
    row = SectionManager.query.filter_by(user_id=user.id, section_id=section_id).first()
    return "deputy" if row is not None and row.is_deputy else "moderator"


def actor_role(user, item):
    """У якій ролі людина діє щодо матеріалу — для журналу модерації."""
    if not user:
        return None
    role = section_role(user, getattr(item, "section_id", None))
    if role:
        return role
    if is_admin(user):
        return "admin"
    if getattr(item, "created_by", None) == user.id:
        return "author"
    return None


def section_staff(section_ids=None):
    """{section_id: {"moderator": User|None, "deputy": User|None}} — хто
    відповідає за розділи. Призначення без ролі менеджера чи вимкнених
    людей не рахуються: вони нічого не модерують."""
    query = SectionManager.query
    if section_ids is not None:
        query = query.filter(SectionManager.section_id.in_(list(section_ids)))
    out = {}
    for row in query.order_by(SectionManager.user_id).all():
        user = User.query.get(row.user_id)
        if user is None or not user.is_active or not is_section_manager(user):
            continue
        slot = out.setdefault(row.section_id, {"moderator": None, "deputy": None})
        key = "deputy" if row.is_deputy else "moderator"
        if slot[key] is None:
            slot[key] = user
    return out


def can_manage_ideas(user):
    return is_admin(user) or bool(user and user.is_ideas_owner)


def has_console(user):
    """Чи бачить людина режим «Керування»."""
    return is_admin(user) or is_section_manager(user) or can_manage_ideas(user)


def moderators_of(section_id, except_user=None):
    """Хто має розглянути матеріал розділу: його менеджери, а якщо їх немає
    (або розділу немає зовсім) — адміністратори. Так подання не губиться."""
    users = User.query.filter_by(is_active=True).all()
    managers = [u for u in users if section_id is not None
                and section_id in managed_section_ids(u)]
    if not managers:
        managers = [u for u in users if is_admin(u)]
    return [u for u in managers if except_user is None or u.id != except_user.id]
