"""RBAC-декоратори: автентифікація, глобальні ролі й перевірки з app/core/access.py."""
from functools import wraps
from flask_jwt_extended import verify_jwt_in_request
from app.core.security import current_user
from app.core.errors import ApiError


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


def require_check(check, message="Недостатньо прав"):
    """Доступ, якщо `check(user)` повертає True (див. app/core/access.py)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user = current_user()
            if user is None or not user.is_active:
                raise ApiError("Неавторизовано", 401, "unauthorized")
            if not check(user):
                raise ApiError(message, 403, "forbidden")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(fn):
    return require_global_role("admin")(fn)


def require_moderator(fn):
    """Адміністратор або менеджер розділу з призначеними розділами."""
    from app.core.access import is_moderator
    return require_check(is_moderator)(fn)
