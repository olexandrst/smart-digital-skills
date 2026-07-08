"""ChatService — чат із обраною моделлю, застосування скілів та облік токенів.

Скіли бувають двох типів:
  - 'prompt'  — формує запит до LLM-моделі;
  - 'package' — архів зі skill.md та кодом, що виконується локально.
"""
import json
import re
import threading
from flask import current_app
from app.extensions import db
from app.core.errors import ApiError
from app.models import (
    Skill, UserSkill, Model, ChatSession, ChatMessage, TokenUsageLog,
)
from app.integrations import get_client_for_model
from app.services import package_service, quota_service, model_access_service

# Скільки останніх повідомлень передавати моделі як контекст діалогу.
HISTORY_LIMIT = 20

# Markdown-посилання [текст](ціль).
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)\s*\)")


def _sanitize_reply_links(text):
    """Знешкоджує вигадані моделлю посилання на файли.

    Модель не може знати реального URL згенерованого файлу (він відомий лише
    після збереження), тож будь-яке її посилання на локальний/відносний шлях
    (напр. `[звіт.xlsx](звіт.xlsx)` чи `sandbox:/mnt/...`) не працює. Лишаємо
    лише зовнішні http(s) та наші /files/ посилання; решту зводимо до тексту.
    Робочі посилання платформа додає окремо (persist у метаданих повідомлення).
    """
    if not text:
        return text

    def repl(m):
        label, href = m.group(1), m.group(2)
        low = href.lower()
        if low.startswith("http://") or low.startswith("https://") or href.startswith("/files/"):
            return m.group(0)  # легітимне посилання лишаємо
        return label  # мертве локальне/відносне посилання → просто текст

    return _MD_LINK_RE.sub(repl, text)


def _agent_max_steps():
    return int(current_app.config.get("SKILL_AGENT_MAX_STEPS", 6))


def _pick_agent_model(skill):
    """Модель для агентного скіла: модель скіла або перша активна LLM."""
    if skill.model_id:
        m = Model.query.get(skill.model_id)
        if m and m.is_active:
            return m
    return (Model.query.filter_by(is_active=True, model_type="llm")
            .order_by(Model.id).first())


def run_agent_skill(user, model, skill, content, history=None):
    """Агентний цикл: модель читає інструкції SKILL.md і сама виконує код
    у пісочниці (стандартний формат скілів). Повертає (reply, usage, files)."""
    if model is None:
        raise ApiError("Немає активної LLM-моделі для агентного скіла",
                       400, "no_model")
    quota_service.ensure_within_limit(user)

    system_prompt = package_service.build_agent_system_prompt(skill)
    messages = [{"role": "system", "content": system_prompt}]
    for m in (history or [])[-HISTORY_LIMIT:]:
        if m.role in ("user", "assistant"):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": content})

    client = get_client_for_model(model)
    params = _skill_params(skill)
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    sandbox = package_service.prepare_sandbox(skill)
    final = ""
    try:
        for _step in range(_agent_max_steps()):
            res = client.chat(model.deployment_name, messages, params)
            usage["prompt_tokens"] += res.prompt_tokens
            usage["completion_tokens"] += res.completion_tokens
            usage["total_tokens"] += res.total_tokens
            messages.append({"role": "assistant", "content": res.content})
            final = res.content

            code = package_service.extract_run_python(res.content)
            if not code:
                break
            ex = package_service.exec_in_sandbox(sandbox, code)
            obs = (f"[returncode={ex['returncode']}]\n"
                   f"stdout:\n{ex['stdout']}\nstderr:\n{ex['stderr']}")
            if ex["returncode"] != 0:
                obs += ("\n\n(Код завершився з ПОМИЛКОЮ. Виправ СВІЙ код виклику за "
                        "traceback вище та спробуй ще раз — не відмовляйся від виконання "
                        "і не замінюй його текстом.)")
            messages.append({"role": "user",
                             "content": "Результат виконання коду:\n" + obs[:8000]})
        files = package_service.finalize_sandbox(sandbox, user, skill.id)
    finally:
        package_service.cleanup_sandbox(sandbox)

    clean = re.sub(r"```(?:run-python|python|py|tool_code).*?```", "",
                   final, flags=re.DOTALL | re.IGNORECASE).strip()
    return (clean or final), usage, files


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
        raise ApiError("Навичку не знайдено", 404, "not_found")
    if not _user_has_skill(user.id, skill_id):
        raise ApiError("Навичку не активовано для вас", 403, "forbidden")
    if skill.status != "published":
        raise ApiError("Навичка недоступна", 400, "skill_not_published")
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

def _resolve_chat_model(user, model_id):
    """Перевіряє/визначає модель для чату. `model_id=None` → типова для користувача."""
    if model_id is None:
        model = model_access_service.default_model(user)
        if model is None:
            raise ApiError("Немає доступних моделей. Зверніться до адміністратора.",
                           400, "no_models")
        return model
    model = Model.query.get(model_id)
    if model is None:
        raise ApiError("Модель не знайдено", 404, "not_found")
    if not model.is_active:
        raise ApiError("Модель неактивна та недоступна для чату", 400, "model_inactive")
    if not model_access_service.is_accessible(user, model.id):
        raise ApiError("Модель недоступна для вашого облікового запису",
                       403, "model_forbidden")
    return model


def create_session(user, model_id=None, title=None):
    """Створює чат. Модель обирається автоматично (типова), якщо не задано."""
    model = _resolve_chat_model(user, model_id)
    # Назва без моделі (у списку чатів модель не показуємо); після першого
    # обміну назву автоматично замінить короткий підсумок (_maybe_autoname).
    session = ChatSession(user_id=user.id, model_id=model.id,
                          title=title or "Новий чат")
    db.session.add(session)
    db.session.commit()
    return session


def set_session_model(user, session_id, model_id):
    """Змінює модель існуючого чату (перевіряє доступність для користувача)."""
    session = _get_owned_session(user, session_id)
    model = _resolve_chat_model(user, model_id)
    session.model_id = model.id
    db.session.commit()
    return session


def send_message(user, session_id, content, skill_id=None):
    """Надсилає повідомлення; опційно застосовує скіл (LLM-шаблон або код-пакет)."""
    if not content or not content.strip():
        raise ApiError("Порожнє повідомлення", 400, "validation_error")

    session = _get_owned_session(user, session_id)
    is_first_exchange = len(session.messages) == 0
    applied_skill = None
    if skill_id:
        applied_skill = _validate_runnable_skill(user, skill_id)

    files = []
    if applied_skill is not None and package_service.is_agent_skill(applied_skill):
        # Агентний скіл (стандартний формат): модель оркеструє виконання.
        model = session.model
        if model is None or not model.is_active:
            raise ApiError("Для агентного скіла потрібна активна модель сесії",
                           400, "model_inactive")
        reply, usage, files = run_agent_skill(
            user, model, applied_skill, content, history=session.messages)
        model_id = model.id
    elif applied_skill is not None and applied_skill.skill_kind == "package":
        # Скіл-скрипт (явний entrypoint): виконуємо код, модель не викликаємо.
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

    reply = _sanitize_reply_links(reply)
    _store_exchange(session, user, model_id, skill_id, content, reply, usage, files=files)

    if is_first_exchange:
        _maybe_autoname(session, content)

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
    """Запуск скіла як одноразової дії (вкладка «Мої скіли»). Усі типи."""
    skill = _validate_runnable_skill(user, skill_id)
    agent = package_service.is_agent_skill(skill)
    model = _pick_agent_model(skill) if agent else None

    session = None
    if session_id:
        session = ChatSession.query.filter_by(id=session_id, user_id=user.id).first()
    if session is None:
        session = ChatSession(user_id=user.id, skill_id=skill_id,
                              model_id=(model.id if model else skill.model_id),
                              title=skill.name)
        db.session.add(session)
        db.session.flush()

    if agent:
        text = (inputs or {}).get("text") or json.dumps(inputs or {}, ensure_ascii=False)
        content, usage, files = run_agent_skill(user, model, skill, text)
        user_content = text
        log_model_id = model.id if model else None
    elif skill.skill_kind == "package":
        content, usage, files = _execute_skill(skill, inputs or {}, user)
        user_content = json.dumps(inputs or {}, ensure_ascii=False)
        log_model_id = skill.model_id
    else:
        content, usage, files = _execute_skill(skill, inputs or {}, user)
        user_content = _build_prompt(skill, inputs or {})
        log_model_id = skill.model_id

    content = _sanitize_reply_links(content)
    _store_exchange(session, user, log_model_id, skill_id, user_content, content, usage,
                    files=files)

    return {
        "session_id": session.id,
        "content": content,
        "usage": usage,
        "files": files,
    }


def _store_exchange(session, user, model_id, skill_id, user_content, reply, usage,
                    files=None):
    db.session.add(ChatMessage(
        session_id=session.id, role="user", content=user_content, skill_id=skill_id,
        prompt_tokens=usage["prompt_tokens"], total_tokens=usage["prompt_tokens"],
    ))
    assistant_msg = ChatMessage(
        session_id=session.id, role="assistant", content=reply,
        completion_tokens=usage["completion_tokens"], total_tokens=usage["completion_tokens"],
    )
    # Прив'язуємо створені файли до повідомлення (id), щоб посилання
    # переживали перевідкриття чату.
    file_ids = [f["id"] for f in (files or []) if isinstance(f, dict) and f.get("id") is not None]
    if file_ids:
        assistant_msg.msg_metadata = json.dumps({"file_ids": file_ids})
    db.session.add(assistant_msg)

    # Рахуємо вартість (USD) за цінами моделі та логуємо разом із токенами.
    mdl = Model.query.get(model_id) if model_id else None
    cost_in, cost_out, cost_total = quota_service.compute_cost(
        mdl, usage["prompt_tokens"], usage["completion_tokens"])
    db.session.add(TokenUsageLog(
        user_id=user.id, group_id=session.group_id, skill_id=skill_id,
        model_id=model_id, session_id=session.id,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        cost_in=cost_in, cost_out=cost_out, cost_total=cost_total,
        feature="chat", is_system=False,
    ))
    db.session.commit()
    # Тижневий лічильник квоти — у грошах (лише користувацьке використання).
    quota_service.record_usage(user.id, cost_total)


# ----------------------- Системна модель: іменування чатів -----------------------

def system_model():
    return Model.query.filter_by(is_system=True, is_active=True).first()


def _maybe_autoname(session, first_message):
    """Асинхронно генерує назву чату системною моделлю після першого обміну."""
    if not current_app.config.get("CHAT_AUTONAME", True):
        return
    sysm = system_model()
    if sysm is None:
        return
    app = current_app._get_current_object()
    th = threading.Thread(
        target=_autoname_worker,
        args=(app, session.id, session.user_id, sysm.id, first_message[:2000]),
        daemon=True,
    )
    th.start()


def _autoname_worker(app, session_id, user_id, model_id, message):
    with app.app_context():
        try:
            model = Model.query.get(model_id)
            sess = ChatSession.query.get(session_id)
            if model is None or sess is None:
                return
            prompt = ("Запропонуй коротку назву чату (3–6 слів, без лапок, "
                      "тією ж мовою) за першим повідомленням користувача. "
                      "Відповідай ЛИШЕ назвою.\n\n" + message)
            client = get_client_for_model(model)
            res = client.chat(model.deployment_name,
                              [{"role": "user", "content": prompt}], {})
            title = (res.content or "").strip().strip('"').splitlines()[0][:80]
            if title:
                sess.title = title
            # Окремий СИСТЕМНИЙ облік токенів + вартості (фіча "chat_naming").
            c_in, c_out, c_tot = quota_service.compute_cost(
                model, res.prompt_tokens, res.completion_tokens)
            db.session.add(TokenUsageLog(
                user_id=user_id, model_id=model_id, session_id=session_id,
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                total_tokens=res.total_tokens,
                cost_in=c_in, cost_out=c_out, cost_total=c_tot,
                feature="chat_naming", is_system=True,
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
