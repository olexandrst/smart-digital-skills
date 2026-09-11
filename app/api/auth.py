"""Автентифікація: корпоративний вхід через Entra ID та логін/пароль."""
from datetime import datetime
from flask import Blueprint, request, jsonify, redirect, session, current_app
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity,
)
from app.extensions import db
from app.core.security import verify_password, current_user
from app.core.permissions import require_auth
from app.core.errors import ApiError
from app.models import User, Role, UserRole
from app.services import entra_service

bp = Blueprint("auth", __name__)


@bp.get("/config")
def auth_config():
    """Які способи входу доступні — щоб екран входу не вгадував."""
    return jsonify({
        "entra_enabled": entra_service.is_enabled(),
        "password_enabled": entra_service.password_login_enabled(),
    })


@bp.post("/login")
def login():
    if not entra_service.password_login_enabled():
        raise ApiError("Вхід за паролем вимкнено — скористайтеся корпоративним "
                       "обліковим записом", 403, "password_login_disabled")
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise ApiError("Вкажіть логін і пароль", 400, "validation_error")

    user = User.query.filter_by(username=username).first()
    if user is None or not verify_password(user.password_hash, password):
        raise ApiError("Невірний логін або пароль", 401, "invalid_credentials")
    if not user.is_active:
        raise ApiError("Обліковий запис деактивовано", 403, "inactive")

    user.last_login_at = datetime.utcnow()
    db.session.commit()

    identity = str(user.id)
    return jsonify({
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
        "user": user.to_dict(),
    })


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    return jsonify({"access_token": create_access_token(identity=identity)})


@bp.get("/me")
@require_auth
def me():
    return jsonify(current_user().to_dict())


# ----------------------- Microsoft Entra ID (SSO) -----------------------

def _redirect_uri():
    """URI повернення має точно збігатися з тим, що зареєстровано в застосунку."""
    configured = current_app.config.get("ENTRA_REDIRECT_URI")
    return configured or request.url_root.rstrip("/") + "/api/auth/entra/callback"


@bp.get("/entra/start")
def entra_start():
    """Починає вхід: PKCE, state і перенаправлення в каталог."""
    if not entra_service.is_enabled():
        raise ApiError("Корпоративний вхід не налаштований", 400, "entra_disabled")
    verifier, challenge = entra_service.new_pkce_pair()
    state = entra_service.new_state()
    # Верифікатор і state — у серверній сесії: у посиланні їх бути не має.
    session["entra_verifier"] = verifier
    session["entra_state"] = state
    return redirect(entra_service.authorize_url(_redirect_uri(), state, challenge))


@bp.get("/entra/callback")
def entra_callback():
    """Повернення з каталогу: перевірка токена, створення користувача, вхід."""
    if not entra_service.is_enabled():
        raise ApiError("Корпоративний вхід не налаштований", 400, "entra_disabled")
    if request.args.get("error"):
        raise ApiError(request.args.get("error_description")
                       or "Каталог відхилив вхід", 401, "entra_error")

    state = request.args.get("state")
    expected = session.pop("entra_state", None)
    verifier = session.pop("entra_verifier", None)
    # state захищає від підробленого повернення; без нього вхід не завершуємо.
    if not state or not expected or state != expected or not verifier:
        raise ApiError("Сесія входу застаріла — спробуйте ще раз",
                       400, "entra_state_mismatch")

    code = request.args.get("code")
    if not code:
        raise ApiError("Каталог не повернув код авторизації", 400, "entra_no_code")

    tokens = entra_service.exchange_code(code, _redirect_uri(), verifier)
    claims = entra_service.verify_id_token(tokens["id_token"])
    user = upsert_entra_user(claims)

    identity = str(user.id)
    # Токени передаються фрагментом URL: так вони не потрапляють ні в логи
    # сервера, ні в заголовок Referer.
    from urllib.parse import urlencode as _urlencode
    fragment = _urlencode({
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
    })
    return redirect("/#" + fragment)


def upsert_entra_user(claims):
    """Створює або оновлює користувача за даними каталогу.

    Ролі перераховуються при кожному вході: членство в групі каталогу — єдине
    джерело правди, тож видалення з групи одразу знімає доступ.
    """
    profile = entra_service.profile_from_claims(claims)
    if not profile["external_id"]:
        raise ApiError("Каталог не повернув ідентифікатор користувача",
                       400, "entra_no_subject")

    user = User.query.filter_by(external_id=profile["external_id"]).first()
    if user is None and profile["email"]:
        # Той самий співробітник міг спершу зайти за паролем — прив'язуємо
        # корпоративний акаунт до наявного запису, а не плодимо другий.
        user = User.query.filter_by(email=profile["email"]).first()
    if user is None:
        user = User(username=profile["username"] or profile["external_id"])
        db.session.add(user)

    user.external_id = profile["external_id"]
    user.auth_provider = "entra"
    user.email = profile["email"] or user.email
    user.full_name = profile["full_name"] or user.full_name
    if profile["department"]:
        user.department = profile["department"]
    user.is_active = True
    user.last_login_at = datetime.utcnow()
    db.session.flush()

    codes = entra_service.role_codes_for(claims)
    roles = {r.code: r.id for r in Role.query.all()}
    UserRole.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    for code in codes:
        if code in roles:
            db.session.add(UserRole(user_id=user.id, role_id=roles[code]))
    user.is_system_admin = "admin" in codes

    db.session.commit()
    return user


@bp.get("/entra/logout")
@require_auth
def entra_logout():
    """Посилання виходу, що завершує й корпоративну сесію."""
    if not entra_service.is_enabled():
        return jsonify({"logout_url": None})
    root = request.url_root.rstrip("/")
    return jsonify({"logout_url": entra_service.logout_url(root + "/")})
