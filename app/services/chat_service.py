"""ChatService — чат із обраною моделлю, застосування скілів та облік токенів."""
import json
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, Model, ChatSession, ChatMessage, TokenUsageLog,
)
from app.integrations import get_client_for_model

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


def _apply_skill_to_message(skill, content):
    """Форматує повідомлення чату через шаблон скіла: перший/`text`-параметр = текст користувача."""
    inputs = {}
    primary = None
    for spec in skill.inputs:
        if spec.name == "text":
            primary = "text"
        if spec.default_value is not None:
            inputs[spec.name] = spec.default_value
    if primary is None and skill.inputs:
        primary = skill.inputs[0].name
    if primary is None:
        # Скіл без параметрів — додаємо текст після шаблону.
        return (skill.prompt_template or "") + "\n\n" + content
    inputs[primary] = content
    return _build_prompt(skill, inputs)


def _get_owned_session(user, session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        raise ApiError("Сесію не знайдено", 404, "not_found")
    return session


def create_session(user, model_id, title=None):
    model = Model.query.get(model_id)
    if model is None:
        raise ApiError("Модель не знайдено", 404, "not_found")
    if not model.is_active:
        raise ApiError("Модель неактивна та недоступна для чату",
                       400, "model_inactive")
    session = ChatSession(user_id=user.id, model_id=model_id,
                          title=title or f"Чат · {model.name}")
    db.session.add(session)
    db.session.commit()
    return session


def send_message(user, session_id, content, skill_id=None):
    """Надсилає повідомлення у сесію чату; опційно застосовує скіл до тексту."""
    if not content or not content.strip():
        raise ApiError("Порожнє повідомлення", 400, "validation_error")

    session = _get_owned_session(user, session_id)
    model = session.model
    if model is None:
        raise ApiError("Для сесії не обрано модель", 400, "validation_error")
    if not model.is_active:
        raise ApiError("Модель неактивна та недоступна для чату",
                       400, "model_inactive")

    params = {}
    prompt_content = content
    applied_skill = None
    if skill_id:
        applied_skill = Skill.query.get(skill_id)
        if applied_skill is None:
            raise ApiError("Скіл не знайдено", 404, "not_found")
        if not _user_has_skill(user.id, skill_id):
            raise ApiError("Скіл не активовано для вас", 403, "forbidden")
        if applied_skill.status != "published":
            raise ApiError("Скіл недоступний", 400, "skill_not_published")
        prompt_content = _apply_skill_to_message(applied_skill, content)
        params = _skill_params(applied_skill)

    # Історія діалогу + поточне (вже відформатоване скілом) повідомлення.
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[-HISTORY_LIMIT:]
               if m.role in ("user", "assistant")]
    history.append({"role": "user", "content": prompt_content})

    client = get_client_for_model(model)
    result = client.chat(model.deployment_name, history, params)

    db.session.add(ChatMessage(
        session_id=session.id, role="user", content=content,
        skill_id=skill_id,
        prompt_tokens=result.prompt_tokens, total_tokens=result.prompt_tokens,
    ))
    db.session.add(ChatMessage(
        session_id=session.id, role="assistant", content=result.content,
        completion_tokens=result.completion_tokens, total_tokens=result.completion_tokens,
    ))
    db.session.add(TokenUsageLog(
        user_id=user.id, group_id=session.group_id, skill_id=skill_id,
        model_id=model.id, session_id=session.id,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
    ))
    db.session.commit()

    return {
        "session_id": session.id,
        "content": result.content,
        "skill_id": skill_id,
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        },
        "session_total_tokens": session.total_tokens_sum,
    }


def delete_session(user, session_id):
    session = _get_owned_session(user, session_id)
    db.session.delete(session)
    db.session.commit()
    return True


def run_skill(user, skill_id, inputs, session_id=None):
    """Запуск скіла як одноразового виклику (вкладка «Мої скіли»)."""
    skill = Skill.query.get(skill_id)
    if skill is None:
        raise ApiError("Скіл не знайдено", 404, "not_found")
    if not _user_has_skill(user.id, skill_id):
        raise ApiError("Скіл не активовано для вас", 403, "forbidden")
    if skill.status != "published":
        raise ApiError("Скіл недоступний", 400, "skill_not_published")

    session = None
    if session_id:
        session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        session = ChatSession(user_id=user.id, skill_id=skill_id,
                              model_id=skill.model_id, title=skill.name)
        db.session.add(session)
        db.session.flush()

    prompt = _build_prompt(skill, inputs or {})
    params = _skill_params(skill)

    client = get_client_for_model(skill.model)
    result = client.complete(skill.model.deployment_name, prompt, params)

    db.session.add(ChatMessage(
        session_id=session.id, role="user", content=prompt, skill_id=skill_id,
        prompt_tokens=result.prompt_tokens, total_tokens=result.prompt_tokens,
    ))
    db.session.add(ChatMessage(
        session_id=session.id, role="assistant", content=result.content,
        completion_tokens=result.completion_tokens, total_tokens=result.completion_tokens,
    ))
    db.session.add(TokenUsageLog(
        user_id=user.id, skill_id=skill_id, model_id=skill.model_id,
        session_id=session.id,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
    ))
    db.session.commit()

    return {
        "session_id": session.id,
        "content": result.content,
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        },
    }
