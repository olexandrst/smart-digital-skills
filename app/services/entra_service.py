"""Вхід через Microsoft Entra ID (OIDC authorization code + PKCE).

Реалізовано без SDK: потрібні лише два HTTP-виклики (обмін коду на токени та
отримання ключів для перевірки підпису), тож зайва залежність не виправдана.

**Перевірка токена — обов'язкова й повна.** `id_token` перевіряється за
підписом із JWKS каталогу, з контролем `aud`, `iss` та строку дії. Розбирати
JWT без перевірки підпису означало б пускати в застосунок будь-кого, хто вміє
зібрати рядок із трьох частин.
"""
import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

from flask import current_app

# Кеш ключів каталогу: JWKS змінюється рідко, а тягнути його на кожен вхід —
# зайва затримка й залежність від мережі в найгіршу мить.
_JWKS_CACHE = {"keys": None, "fetched_at": 0}
JWKS_TTL_SECONDS = 3600

SCOPES = "openid profile email User.Read"


def is_enabled():
    """Чи налаштований вхід через Entra ID."""
    cfg = current_app.config
    return bool(cfg.get("ENTRA_TENANT_ID") and cfg.get("ENTRA_CLIENT_ID"))


def password_login_enabled():
    """Чи лишається парольний вхід (для сервісних облікових записів)."""
    return current_app.config.get("ENTRA_ALLOW_PASSWORD_LOGIN", True)


def _authority():
    return (f"https://login.microsoftonline.com/"
            f"{current_app.config['ENTRA_TENANT_ID']}/v2.0")


def new_pkce_pair():
    """Верифікатор і виклик PKCE: захист коду авторизації від перехоплення."""
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorize_url(redirect_uri, state, challenge):
    params = {
        "client_id": current_app.config["ENTRA_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_authority()}/authorize?{urlencode(params)}"


def logout_url(redirect_uri):
    """Вихід, що завершує й корпоративну сесію, а не лише локальну."""
    return (f"{_authority()}/logout?"
            + urlencode({"post_logout_redirect_uri": redirect_uri}))


def new_state():
    return secrets.token_urlsafe(24)


def _http_post(url, data):
    import urllib.request
    body = urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _http_get(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _jwks():
    now = time.time()
    if (_JWKS_CACHE["keys"] is not None
            and now - _JWKS_CACHE["fetched_at"] < JWKS_TTL_SECONDS):
        return _JWKS_CACHE["keys"]
    meta = _http_get(f"{_authority()}/.well-known/openid-configuration")
    keys = _http_get(meta["jwks_uri"])["keys"]
    _JWKS_CACHE.update(keys=keys, fetched_at=now)
    return keys


def exchange_code(code, redirect_uri, verifier):
    """Міняє код авторизації на токени."""
    data = {
        "client_id": current_app.config["ENTRA_CLIENT_ID"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "scope": SCOPES,
    }
    secret = current_app.config.get("ENTRA_CLIENT_SECRET")
    if secret:
        data["client_secret"] = secret
    return _http_post(f"{_authority()}/token", data)


def verify_id_token(id_token):
    """Перевіряє підпис, отримувача, видавця й строк дії. Повертає claims."""
    import jwt
    from jwt import PyJWKClient  # noqa: F401  (перевірка наявності PyJWT[crypto])

    header = jwt.get_unverified_header(id_token)
    key_data = next((k for k in _jwks() if k["kid"] == header["kid"]), None)
    if key_data is None:
        raise ValueError("Ключ підпису токена не знайдено в каталозі")

    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
    tenant = current_app.config["ENTRA_TENANT_ID"]
    return jwt.decode(
        id_token, key=key, algorithms=[header.get("alg", "RS256")],
        audience=current_app.config["ENTRA_CLIENT_ID"],
        issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        options={"require": ["exp", "iat", "aud", "iss"]},
    )


def role_codes_for(claims):
    """Глобальні ролі застосунку за членством у групах Entra ID.

    Зіставлення задається змінними середовища: доступ менеджера визначається
    членством у групі каталогу, а не ручним призначенням у застосунку.
    """
    cfg = current_app.config
    mapping = {
        "admin": {g.strip() for g in (cfg.get("ENTRA_ADMIN_GROUPS") or "").split(",")
                  if g.strip()},
        "skill_manager": {g.strip() for g in
                          (cfg.get("ENTRA_MANAGER_GROUPS") or "").split(",")
                          if g.strip()},
    }
    member_of = set(claims.get("groups") or []) | set(claims.get("roles") or [])
    return [code for code, groups in mapping.items() if groups & member_of]


def profile_from_claims(claims):
    """Дані користувача з токена каталогу."""
    email = (claims.get("preferred_username") or claims.get("email") or "").strip()
    return {
        "external_id": claims.get("oid") or claims.get("sub"),
        "email": email or None,
        "username": (email or claims.get("sub") or "").lower(),
        "full_name": claims.get("name") or email,
        "department": claims.get("department") or None,
    }
