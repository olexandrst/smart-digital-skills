"""ChatService — чат із обраною моделлю, застосування скілів та облік токенів.

Скіли бувають двох типів:
  - 'prompt'  — формує запит до LLM-моделі;
  - 'package' — архів зі skill.md та кодом, що виконується локально.
"""
import json
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, Model, ChatSession, ChatMessage, TokenUsageLog,
)
from app.integrations import get_client_for_model
from app.services import package_service, quota_service

# Скільки останніх повідомлень передавати моделі як контекст діалогу.
HISTORY_LIMIT = 20


def _user_has_skill(user_id, skill_id):
    return UserSkill.query.filter_by(
        user_id=user_id, skill_id=skill_id, is_active=True).first() is not None


def _skill_params(skill):
    if not skill.parameters:
        return {}
    try:
        return json.loads(skill.parameters)
    except (ValueError, TypeError):
        return {}


def _build_prompt(skill, inputs):
    """Підставляє вхідні параметри у prompt_template (плейсхолдери {name})."""
    template = skill.prompt_template or "{text}"
    values = {}
    for spec in skill.inputs:
        val = inputs.get(spec.name, spec.default_value)
        if spec.is_required and (val is None or val == ""):
            raise ApiError(f"Відсутній обов'язковий параметр: {spec.name}",
                           400, "validation_error")
        values[spec.name] = "" if val is None else val
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        return template + "\n\n" + json.dumps(values, ensure_ascii=False)


def _primary_input_name(skill):
    for spec in skill.inputs:
        if spec.name == "text":
            return "text"
    return skill.inputs[0].name if skill.inputs else "text"


def _inputs_for_message(skill, content):
    """Будує словник вхідних даних скіла з тексту повідомлення чату."""
    inputs = {s.name: s.default_value for s in skill.inputs if s.default_value is not None}
    inputs[_primary_input_name(skill)] = content
    return inputs


def _validate_runnable_skill(user, skill_id):
    skill = Skill.query.get(skill_id)
    if skill is None:
        raise ApiError("Скіл не знайдено", 404, "not_found")
    if not _user_has_skill(user.id, skill_id):
        raise ApiError("Скіл не активовано для вас", 403, "forbidden")
    if skill.status != "published":
        raise ApiError("Скіл недоступний", 400, "skill_not_published")
    return skill


def _execute_skill(skill, inputs, user):
    """Виконує скіл та повертає (content, usage, files). Працює для обох типів."""
    if skill.skill_kind == "package":
        result = package_service.run_package(skill, inputs, user=user)
        return result["output"], result["usage"], result.get("files", [])

    quota_service.ensure_within_limit(user)
    prompt = _build_prompt(skill, inputs)
    client = get_client_for_model(skill.model)
    res = client.complete(skill.model.deployment_name, prompt, _skill_params(skill))
    return res.content, {
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "total_tokens": res.total_tokens,
    }, []


def _get_owned_session(user, session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        raise ApiError("Сесію не знайдено", 404, "not_found")
    return session


# ----------------------------- Чат -----------------------------

def create_session(user, model_id, title=None):
    model = Model.query.get(model_id)
    if model is None:
        raise ApiError("Модель не знайдено", 404, "not_found")
    if not model.is_active:
        raise ApiError("Модель неактивна та недоступна для чату", 400, "model_inactive")
    session = ChatSession(user_id=user.id, model_id=model_id,
                          title=title or f"Чат · {model.name}")
    db.session.add(session)
    db.session.commit()
    return session


def send_message(user, session_id, content, skill_id=None):
    """Надсилає повідомлення; опційно застосовує скіл (LLM-шаблон або код-пакет)."""
    if not content or not content.strip():
        raise ApiError("Порожнє повідомлення", 400, "validation_error")

    session = _get_owned_session(user, session_id)
    applied_skill = None
    if skill_id:
        applied_skill = _validate_runnable_skill(user, skill_id)

    files = []
    # Скіл-пакет: виконуємо код, модель не викликаємо.
    if applied_skill is not None and applied_skill.skill_kind == "package":
        inputs = _inputs_for_message(applied_skill, content)
        reply, usage, files = _execute_skill(applied_skill, inputs, user)
        model_id = session.model_id
    else:
        model = session.model
        if model is None:
            raise ApiError("Для сесії не обрано модель", 400, "validation_error")
        if not model.is_active:
            raise ApiError("Модель неактивна та недоступна для чату", 400, "model_inactive")
        model_id = model.id
        quota_service.ensure_within_limit(user)

        if applied_skill is not None:
            prompt_content = _build_prompt(
                applied_skill, _inputs_for_message(applied_skill, content))
            params = _skill_params(applied_skill)
        else:
            prompt_content = content
            params = {}

        history = [{"role": m.role, "content": m.content}
                   for m in session.messages[-HISTORY_LIMIT:]
                   if m.role in ("user", "assistant")]
        history.append({"role": "user", "content": prompt_content})

        client = get_client_for_model(model)
        result = client.chat(model.deployment_name, history, params)
        reply = result.content
        usage = {"prompt_tokens": result.prompt_tokens,
                 "completion_tokens": result.completion_tokens,
                 "total_tokens": result.total_tokens}

    _store_exchange(session, user, model_id, skill_id, content, reply, usage)

    return {
        "session_id": session.id,
        "content": reply,
        "skill_id": skill_id,
        "usage": usage,
        "files": files,
        "session_total_tokens": session.total_tokens_sum,
    }


def delete_session(user, session_id):
    session = _get_owned_session(user, session_id)
    db.session.delete(session)
    db.session.commit()
    return True


# ----------------------- Одноразовий запуск скіла -----------------------

def run_skill(user, skill_id, inputs, session_id=None):
    """Запуск скіла як одноразової дії (вкладка «Мої скіли»). Обидва типи."""
    skill = _validate_runnable_skill(user, skill_id)

    session = None
    if session_id:
        session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        session = ChatSession(user_id=user.id, skill_id=skill_id,
                              model_id=skill.model_id, title=skill.name)
        db.session.add(session)
        db.session.flush()

    if skill.skill_kind == "package":
        content, usage, files = _execute_skill(skill, inputs or {}, user)
        user_content = json.dumps(inputs or {}, ensure_ascii=False)
    else:
        content, usage, files = _execute_skill(skill, inputs or {}, user)
        user_content = _build_prompt(skill, inputs or {})

    _store_exchange(session, user, skill.model_id, skill_id, user_content, content, usage)

    return {
        "session_id": session.id,
        "content": content,
        "usage": usage,
        "files": files,
    }


def _store_exchange(session, user, model_id, skill_id, user_content, reply, usage):
    db.session.add(ChatMessage(
        session_id=session.id, role="user", content=user_content, skill_id=skill_id,
        prompt_tokens=usage["prompt_tokens"], total_tokens=usage["prompt_tokens"],
    ))
    db.session.add(ChatMessage(
        session_id=session.id, role="assistant", content=reply,
        completion_tokens=usage["completion_tokens"], total_tokens=usage["completion_tokens"],
    ))
    db.session.add(TokenUsageLog(
        user_id=user.id, group_id=session.group_id, skill_id=skill_id,
        model_id=model_id, session_id=session.id,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
    ))
    db.session.commit()
    # Оновлюємо тижневий лічильник квоти.
    quota_service.record_usage(user.id, usage["total_tokens"])
