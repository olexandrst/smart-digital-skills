"""RBAC-декоратори: глобальні ролі та ролі в межах групи."""
from functools import wraps
from flask_jwt_extended import verify_jwt_in_request
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import GroupMembership


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        user = current_user()
        if user is None or not user.is_active:
            raise ApiError("Користувача не знайдено або деактивовано", 401, "unauthorized")
        return fn(*args, **kwargs)
    return wrapper


def require_global_role(*codes):
    """Доступ лише користувачам із однією з глобальних ролей (admin враховує is_system_admin)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user = current_user()
            if user is None or not user.is_active:
                raise ApiError("Неавторизовано", 401, "unauthorized")
            if not any(user.has_global_role(c) for c in codes):
                raise ApiError("Недостатньо прав", 403, "forbidden")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_group_role(role, group_id_arg="group_id"):
    """Доступ лише менеджеру/мемберу конкретної групи (admin — завжди дозволено).

    role: 'manager' або 'member'. 'manager' не вимагає окремо 'member'.
    group_id береться з kwargs ендпоінта (за іменем group_id_arg).
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user = current_user()
            if user is None or not user.is_active:
                raise ApiError("Неавторизовано", 401, "unauthorized")
            if user.has_global_role("admin"):
                return fn(*args, **kwargs)

            group_id = kwargs.get(group_id_arg)
            membership = GroupMembership.query.filter_by(
                group_id=group_id, user_id=user.id, status="active"
            ).first()
            if membership is None:
                raise ApiError("Ви не є членом цієї групи", 403, "forbidden")
            if role == "manager" and membership.role != "manager":
                raise ApiError("Потрібні права менеджера групи", 403, "forbidden")
            return fn(*args, **kwargs)
        return wrapper
    return decorator
