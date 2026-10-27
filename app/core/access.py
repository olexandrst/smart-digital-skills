"""Хто що може в каталозі: ролі й розділи (фідбек тестування, п. 11).

Чотири ролі, призначає їх адміністратор вручну:

- Адміністратор — усе: користувачі, ролі, структура, довідники, маршрути,
  налаштування й модерація будь-якого розділу, зокрема матеріалів без розділу.
- Менеджер розділу — модерує матеріали й навички лише призначених йому
  розділів (одного чи кількох); заступник — та сама роль у тих самих розділах.
- Користувач — переглядає каталог, створює чернетки й подає їх на розгляд.
- «Експерт» і «Відповідальний за ідеї» — ознаки в профілі, а не ролі.

Тут лише відповіді «так/ні»; ендпоінти вирішують, що з ними робити.
"""
from app.models import User

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
