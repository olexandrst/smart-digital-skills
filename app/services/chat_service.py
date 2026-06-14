"""ChatService — оркестрація запитів до моделей та облік токенів."""
import json
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, ChatSession, ChatMessage, TokenUsageLog,
)
from app.integrations import get_client_for_model


def _user_has_skill(user_id, skill_id):
    return UserSkill.query.filter_by(
        user_id=user_id, skill_id=skill_id, is_active=True).first() is not None


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
        # Якщо в шаблоні є плейсхолдери без значень — просто додаємо вхідні дані.
        return template + "\n\n" + json.dumps(values, ensure_ascii=False)


def run_skill(user, skill_id, inputs, session_id=None):
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
                              title=skill.name)
        db.session.add(session)
        db.session.flush()

    prompt = _build_prompt(skill, inputs or {})

    params = {}
    if skill.parameters:
        try:
            params = json.loads(skill.parameters)
        except (ValueError, TypeError):
            params = {}

    client = get_client_for_model(skill.model)
    result = client.complete(skill.model.deployment_name, prompt, params)

    db.session.add(ChatMessage(
        session_id=session.id, role="user", content=prompt,
        prompt_tokens=result.prompt_tokens, total_tokens=result.prompt_tokens,
    ))
    db.session.add(ChatMessage(
        session_id=session.id, role="assistant", content=result.content,
        completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
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
