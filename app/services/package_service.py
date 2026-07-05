"""PackageService — скіли-пакети (архів зі skill.md + код) та їх виконання.

Формат пакета (архів .zip або .skill):
    skill.md            # YAML-фронтматер (name, description, version, runtime,
                        # entrypoint, inputs) + інструкції у тілі
    <entrypoint>.py     # код, що виконується (читає JSON зі stdin, пише у stdout)
    ...                 # будь-які інші файли та папки

Безпека виконання: окремий підпроцес, таймаут, ліміти ресурсів (CPU/пам'ять/
розмір файлів), чисте середовище, тимчасова робоча тека, захист від zip-slip.
Виконувати код можуть лише авторизовані ролі (Admin/Skill Manager створюють
скіли), а сам рушій вмикається прапором SKILL_EXEC_ENABLED.
"""
import io
import os
import re
import json
import shutil
import zipfile
import tempfile
import subprocess
import sys
from flask import current_app

from app.core.errors import ApiError

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    import resource  # POSIX-only
except ImportError:  # pragma: no cover
    resource = None

SKILL_MD_NAMES = ("skill.md", "SKILL.md", "skil.md")
ALLOWED_EXTENSIONS = (".zip", ".skill")


# ----------------------------- Розбір архіву -----------------------------

def _read_zip(file_bytes):
    try:
        return zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise ApiError("Файл не є коректним zip/skill-архівом", 400,
                       "invalid_archive") from exc


def _find_skill_md(zf):
    """Знаходить skill.md та повертає (шлях_у_архіві, корінь_пакета)."""
    candidates = []
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1]
        if base in SKILL_MD_NAMES:
            depth = name.count("/")
            candidates.append((depth, name))
    if not candidates:
        raise ApiError("В архіві відсутній skill.md", 400, "missing_skill_md")
    candidates.sort()  # найменша глибина першою
    md_path = candidates[0][1]
    root = md_path.rsplit("/", 1)[0] if "/" in md_path else ""
    return md_path, root


def _norm(p):
    return (p or "").replace("\\", "/").lstrip("./")


def _files_under_root(zf, root):
    """Список (оригінальна_назва, шлях_відносно_кореня) для файлів (без тек)."""
    out = []
    prefix = _norm(root + "/") if root else ""
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        nn = _norm(n)
        if prefix:
            if not nn.startswith(prefix):
                continue
            rel = nn[len(prefix):]
        else:
            rel = nn
        if rel:
            out.append((n, rel))
    return out


def _resolve_entrypoint(zf, root, declared):
    """Знаходить файл запуску якомога гнучкіше; повертає шлях відносно кореня пакета."""
    files = _files_under_root(zf, root)
    rels = {rel for _o, rel in files}
    declared_clean = _norm(declared).lstrip("/") if declared else ""

    # 1) Точний збіг шляху (відносно кореня) або повного шляху в архіві.
    if declared_clean and declared_clean in rels:
        return declared_clean
    if declared:
        dn = _norm(declared)
        for orig, rel in files:
            if _norm(orig) == dn:
                return rel

    # 2) Збіг за базовою назвою (declared або main.py) будь-де у пакеті.
    target_base = os.path.basename(declared_clean) if declared_clean else "main.py"
    for base in [target_base, "main.py"]:
        matches = [rel for _o, rel in files if os.path.basename(rel) == base]
        if matches:
            matches.sort(key=lambda r: r.count("/"))
            return matches[0]

    # 3) Якщо у пакеті лише один .py — використовуємо його.
    py_files = [rel for _o, rel in files if rel.endswith(".py")]
    if len(py_files) == 1:
        return py_files[0]

    listing = ", ".join(sorted(py_files)) or "(немає .py файлів)"
    raise ApiError(
        f"Не вдалося визначити entrypoint. Вкажіть 'entrypoint' у skill.md. "
        f"Python-файли в пакеті: {listing}",
        400, "missing_entrypoint")


def _parse_frontmatter(text):
    """Витягує YAML-фронтматер між рядками '---'. Повертає (meta, body)."""
    meta, body = {}, text
    stripped = text.lstrip()
    if stripped.startswith("---"):
        rest = stripped[3:]
        end = rest.find("\n---")
        if end != -1:
            raw = rest[:end]
            body = rest[end + 4:]
            if yaml is not None:
                try:
                    meta = yaml.safe_load(raw) or {}
                except yaml.YAMLError as exc:
                    raise ApiError(f"Помилка YAML у skill.md: {exc}", 400,
                                   "invalid_skill_md") from exc
            else:  # дуже простий fallback без PyYAML
                for line in raw.splitlines():
                    if ":" in line and not line.strip().startswith("-"):
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
    if not isinstance(meta, dict):
        raise ApiError("Фронтматер skill.md має бути словником", 400,
                       "invalid_skill_md")
    return meta, body


def parse_package(file_bytes):
    """Розбирає архів: повертає dict з метаданими скіла та коренем пакета."""
    zf = _read_zip(file_bytes)
    _validate_zip_safety(zf)
    md_path, root = _find_skill_md(zf)
    text = zf.read(md_path).decode("utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)

    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip() or name
    if not name:
        raise ApiError("У SKILL.md не вказано name", 400, "invalid_skill_md")

    runtime = (meta.get("runtime") or "python").strip().lower()

    # Стандартний формат: entrypoint опційний. Якщо вказаний — це скіл-скрипт
    # (виконуємо напряму); якщо ні — агентний скіл (інструкції + ресурси, які
    # модель виконує сама через код-пісочницю).
    entrypoint = None
    if meta.get("entrypoint"):
        if runtime != "python":
            raise ApiError("Для entrypoint підтримується лише runtime: python",
                           400, "unsupported_runtime")
        entrypoint = _resolve_entrypoint(zf, root, meta.get("entrypoint"))

    inputs = _normalize_inputs(meta.get("inputs", []))

    return {
        "name": name,
        "description": description,
        "author": (str(meta.get("author") or "").strip() or None),
        "category": (str(meta.get("category") or "").strip() or None),
        "version": str(meta.get("version", "1.0.0")),
        "runtime": runtime,
        "entrypoint": entrypoint,
        "inputs": inputs,
        "instructions": body.strip(),
    }


def _normalize_inputs(raw):
    inputs = []
    for idx, spec in enumerate(raw or []):
        if not isinstance(spec, dict):
            continue
        name = (spec.get("name") or "").strip()
        if not name:
            continue
        inputs.append({
            "name": name,
            "label": spec.get("label"),
            "data_type": spec.get("data_type", "string"),
            "is_required": bool(spec.get("required", spec.get("is_required", True))),
            "default_value": spec.get("default", spec.get("default_value")),
            "description": spec.get("description"),
            "position": idx,
        })
    return inputs


# --------------------------- Безпека архіву ---------------------------

def _validate_zip_safety(zf):
    """Захист від zip-slip та zip-bomb."""
    max_unzipped = current_app.config.get("SKILL_PACKAGE_MAX_UNZIPPED",
                                          50 * 1024 * 1024)
    total = 0
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
            raise ApiError(f"Небезпечний шлях в архіві: {name}", 400, "unsafe_archive")
        total += info.file_size
        if total > max_unzipped:
            raise ApiError("Архів перевищує дозволений розмір у розпакованому вигляді",
                           400, "archive_too_large")


def _safe_extract(file_bytes, dest_dir):
    zf = _read_zip(file_bytes)
    _validate_zip_safety(zf)
    dest_abs = os.path.abspath(dest_dir)
    for info in zf.infolist():
        target = os.path.abspath(os.path.join(dest_dir, info.filename))
        if not target.startswith(dest_abs + os.sep) and target != dest_abs:
            raise ApiError(f"Небезпечний шлях в архіві: {info.filename}",
                           400, "unsafe_archive")
    zf.extractall(dest_dir)
    # Корінь пакета = тека зі skill.md.
    md_path, root = _find_skill_md(zf)
    return os.path.join(dest_dir, root) if root else dest_dir


# --------------------------- Збереження ---------------------------

def store_package(skill_id, file_bytes, original_filename):
    packages_dir = current_app.config["SKILL_PACKAGES_DIR"]
    os.makedirs(packages_dir, exist_ok=True)
    path = os.path.join(packages_dir, f"skill_{skill_id}.zip")
    with open(path, "wb") as fh:
        fh.write(file_bytes)
    return path


def delete_package_file(skill):
    if skill.package_path and os.path.exists(skill.package_path):
        try:
            os.remove(skill.package_path)
        except OSError:
            pass


def list_package_files(skill):
    if not skill.package_path or not os.path.exists(skill.package_path):
        return []
    with open(skill.package_path, "rb") as fh:
        zf = _read_zip(fh.read())
    return [{"name": i.filename, "size": i.file_size}
            for i in zf.infolist() if not i.filename.endswith("/")]


# --------------------------- Виконання ---------------------------

def _resource_limits():
    """preexec_fn для підпроцесу: обмеження CPU/пам'яті/розміру файлів (POSIX)."""
    if resource is None:  # pragma: no cover
        return None
    cfg = current_app.config
    cpu = max(1, int(cfg.get("SKILL_EXEC_TIMEOUT", 30)))
    mem_mb = int(cfg.get("SKILL_EXEC_MEMORY_MB", 512))

    def _apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024,) * 2)
        if mem_mb > 0:
            limit = mem_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ValueError, OSError):
                pass
        os.setsid()
    return _apply


def _snapshot(directory):
    """Множина відносних шляхів усіх файлів у теці."""
    found = set()
    for root, _dirs, files in os.walk(directory):
        for f in files:
            found.add(os.path.relpath(os.path.join(root, f), directory))
    return found


def _collect_new_files(directory, before):
    """Файли, створені під час виконання (не входили в пакет, не кеш Python)."""
    new = []
    for rel in _snapshot(directory) - before:
        if "__pycache__" in rel.split(os.sep) or rel.endswith(".pyc"):
            continue
        new.append(rel)
    return sorted(new)


def _exec_header(root_abs):
    """Спільний заголовок bootstrap: UTF-8 stdio, перенаправлення /tmp, sys.path."""
    return (
        "import sys, os, io, builtins\n"
        "try:\n"
        "    sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
        "    sys.stderr.reconfigure(encoding='utf-8', errors='replace')\n"
        "    sys.stdin.reconfigure(encoding='utf-8')\n"
        "except Exception: pass\n"
        f"_ROOT = {root_abs!r}\n"
        "def _remap(p):\n"
        "    try: s = os.fspath(p)\n"
        "    except Exception: return p\n"
        "    if isinstance(s, str):\n"
        "        for pref in ('/tmp/', '/var/tmp/', '/private/tmp/'):\n"
        "            if s.startswith(pref):\n"
        "                return os.path.join(_ROOT, os.path.basename(s) or 'output')\n"
        "    return p\n"
        "_ro = builtins.open\n"
        "def _open(file, *a, **k): return _ro(_remap(file), *a, **k)\n"
        "builtins.open = _open\n"
        "io.open = _open\n"
        "_oso = os.open\n"
        "def _osopen(path, *a, **k): return _oso(_remap(path), *a, **k)\n"
        "os.open = _osopen\n"
        "for _fn in ('replace', 'rename'):\n"
        "    _orig = getattr(os, _fn)\n"
        "    def _mk(o):\n"
        "        def _w(src, dst, *a, **k): return o(_remap(src), _remap(dst), *a, **k)\n"
        "        return _w\n"
        "    setattr(os, _fn, _mk(_orig))\n"
        "try:\n"
        "    for _n in ([''] + (os.listdir(_ROOT) if os.path.isdir(_ROOT) else [])):\n"
        "        _d = os.path.join(_ROOT, _n)\n"
        "        if os.path.isdir(_d) and _d not in sys.path: sys.path.insert(0, _d)\n"
        "except Exception: pass\n"
    )


def _run_python(root, workdir, body, stdin=""):
    """Запускає Python у пісочниці: UTF-8, ліміти ресурсів, перенаправлення /tmp.

    `-X utf8` примусово вмикає UTF-8-режим (важливо на Windows, де локаль cp1251);
    декодуємо вивід як UTF-8 і на боці застосунку.
    """
    cfg = current_app.config
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    prog = _exec_header(os.path.abspath(root)) + "\n" + body
    timeout = int(cfg.get("SKILL_EXEC_TIMEOUT", 120))
    preexec = _resource_limits() if os.name != "nt" else None
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-I", "-c", prog],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
        env=env,
        timeout=timeout,
        preexec_fn=preexec,
    )


def run_package(skill, inputs, user=None):
    """Скіл зі скриптом (явний entrypoint): виконує entrypoint у підпроцесі.

    Файли, створені кодом, зберігаються у сховище користувача (якщо передано user).
    """
    cfg = current_app.config
    if not cfg.get("SKILL_EXEC_ENABLED", True):
        raise ApiError("Виконання скілів-пакетів вимкнено (SKILL_EXEC_ENABLED=0)",
                       403, "exec_disabled")
    if not skill.package_path or not os.path.exists(skill.package_path):
        raise ApiError("Архів скіла відсутній", 400, "package_missing")
    if (skill.runtime or "python") != "python":
        raise ApiError("Підтримується лише runtime: python", 400, "unsupported_runtime")

    with open(skill.package_path, "rb") as fh:
        file_bytes = fh.read()

    run_dir = cfg.get("SKILL_RUN_DIR") or os.path.join(os.getcwd(), "instance", "run_tmp")
    os.makedirs(run_dir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="skillrun_", dir=run_dir)
    try:
        root = _safe_extract(file_bytes, workdir)
        rel_entry = skill.entrypoint or "main.py"
        entry = os.path.join(root, rel_entry)
        if not os.path.isfile(entry):
            base = os.path.basename(rel_entry)
            found = None
            for r, _d, files in os.walk(root):
                if base in files:
                    found = os.path.join(r, base)
                    break
            if found is None:
                raise ApiError("Entrypoint не знайдено у пакеті", 400, "missing_entrypoint")
            entry = found

        before = _snapshot(workdir)
        payload = json.dumps({"inputs": inputs or {}}, ensure_ascii=False)
        entry_abs = os.path.abspath(entry)
        body = (
            f"sys.path.insert(0, {os.path.dirname(entry_abs)!r})\n"
            "import runpy\n"
            f"runpy.run_path({entry_abs!r}, run_name='__main__')\n"
        )
        timeout = int(cfg.get("SKILL_EXEC_TIMEOUT", 120))
        try:
            proc = _run_python(root, workdir, body, stdin=payload)
        except subprocess.TimeoutExpired:
            raise ApiError(f"Виконання перевищило ліміт {timeout}с", 400, "exec_timeout")

        max_out = int(cfg.get("SKILL_EXEC_MAX_OUTPUT", 100000))
        stdout = (proc.stdout or "")[:max_out]
        stderr = (proc.stderr or "")[:max_out]

        if proc.returncode != 0:
            raise ApiError(
                f"Код скіла завершився з помилкою (код {proc.returncode}).\n{stderr.strip()}",
                400, "exec_error")

        result = _parse_exec_output(stdout, stderr)
        result["files"] = _persist_outputs(workdir, before, user, skill.id)
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------- Агентні скіли (стандартний формат) ---------------------

def is_agent_skill(skill):
    """Стандартний скіл (інструкції + ресурси) — без явного entrypoint."""
    return skill.skill_kind == "package" and not skill.entrypoint


def read_skill_bundle(skill, max_chars=28000):
    """Повертає (instructions, bundle_text, file_list) із пакета скіла."""
    with open(skill.package_path, "rb") as fh:
        zf = _read_zip(fh.read())
    md_path, _root = _find_skill_md(zf)
    md_text = zf.read(md_path).decode("utf-8", "replace")
    _meta, body = _parse_frontmatter(md_text)

    file_list = [i.filename for i in zf.infolist() if not i.filename.endswith("/")]
    parts, total = [], len(body)
    text_ext = (".md", ".txt", ".py", ".json", ".csv", ".yaml", ".yml")
    for rel in file_list:
        if rel == md_path or not rel.lower().endswith(text_ext):
            continue
        try:
            content = zf.read(rel).decode("utf-8", "replace")
        except Exception:
            continue
        chunk = f"\n\n===== {rel} =====\n{content}"
        if total + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - total)] + "\n... [обрізано]"
            parts.append(chunk)
            break
        parts.append(chunk)
        total += len(chunk)
    return body.strip(), "".join(parts), file_list


def build_agent_system_prompt(skill):
    instructions, bundle, file_list = read_skill_bundle(skill)
    tree = "\n".join(" - " + f for f in file_list)
    return (
        "Ти — агент, що виконує навичку (skill) у форматі Anthropic Agent Skills. "
        "Точно дотримуйся інструкцій навички (SKILL.md) нижче та використовуй надані файли.\n\n"
        "ВИКОНАННЯ КОДУ. У тебе є Python-пісочниця у теці навички (поточна тека = корінь "
        "навички; усі файли навички вже на місці). Щоб виконати код, виведи РІВНО один блок:\n"
        "```run-python\n<твій Python-код>\n```\n"
        "Я виконаю його й поверну stdout/stderr наступним повідомленням. Працюй ІТЕРАТИВНО, "
        "маленькими кроками (можеш спершу надрукувати/перевірити дані, потім згенерувати результат).\n\n"
        "ОБОВ'ЯЗКОВІ ПРАВИЛА:\n"
        "1. Якщо навичка має створити файл/артефакт — ти ЗОБОВ'ЯЗАНИЙ зробити це КОДОМ. "
        "НЕ замінюй виконання коду текстом або таблицею в чаті — це вважається невдачею.\n"
        "2. Якщо код упав з помилкою — НЕ здавайся і НЕ кажи 'не можу'. Прочитай traceback, "
        "ВИПРАВ СВІЙ код виклику й спробуй знову (у тебе є кілька спроб). Бібліотечні скрипти "
        "навички вже робочі — виправляй власний код виклику, а не самі скрипти.\n"
        "3. Імпортуй скрипти навички напряму (напр. "
        "`from build_estimate import build_estimate, Performer, Activity`) і ТОЧНО дотримуйся "
        "їхніх сигнатур — перевір вихідний код нижче перед викликом.\n"
        "4. Зберігай файли ВІДНОСНИМ шляхом (напр. `wb.save('Оцінка.xlsx')`) — вони автоматично "
        "стають доступні користувачу як посилання.\n"
        "5. Лише КОЛИ файл реально створено — дай коротку фінальну відповідь користувачу "
        "БЕЗ блоку коду.\n\n"
        f"========== SKILL.md ==========\n{instructions}\n\n"
        f"========== Файли навички ==========\n{tree}\n"
        f"{bundle}\n"
    )


_CODE_RE = re.compile(r"```(?:run-python|python|py|tool_code)\s*\n(.*?)```",
                      re.DOTALL | re.IGNORECASE)


def extract_run_python(text):
    m = _CODE_RE.search(text or "")
    return m.group(1).strip() if m else None


def prepare_sandbox(skill):
    """Розпаковує пакет у пісочницю всередині застосунку; повертає стан."""
    cfg = current_app.config
    if not cfg.get("SKILL_EXEC_ENABLED", True):
        raise ApiError("Виконання скілів вимкнено (SKILL_EXEC_ENABLED=0)", 403, "exec_disabled")
    if not skill.package_path or not os.path.exists(skill.package_path):
        raise ApiError("Архів скіла відсутній", 400, "package_missing")
    with open(skill.package_path, "rb") as fh:
        file_bytes = fh.read()
    run_dir = cfg.get("SKILL_RUN_DIR") or os.path.join(os.getcwd(), "instance", "run_tmp")
    os.makedirs(run_dir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="skillagent_", dir=run_dir)
    root = _safe_extract(file_bytes, workdir)
    return {"workdir": workdir, "root": root, "before": _snapshot(workdir)}


def exec_in_sandbox(sandbox, code):
    """Виконує код моделі у пісочниці; повертає {stdout, stderr, returncode}."""
    cfg = current_app.config
    timeout = int(cfg.get("SKILL_EXEC_TIMEOUT", 120))
    try:
        proc = _run_python(sandbox["root"], sandbox["workdir"], code, stdin="")
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Перевищено ліміт часу {timeout}с", "returncode": -1}
    max_out = int(cfg.get("SKILL_EXEC_MAX_OUTPUT", 100000))
    return {
        "stdout": (proc.stdout or "")[:max_out],
        "stderr": (proc.stderr or "")[:max_out],
        "returncode": proc.returncode,
    }


def finalize_sandbox(sandbox, user, skill_id):
    return _persist_outputs(sandbox["workdir"], sandbox["before"], user, skill_id)


def cleanup_sandbox(sandbox):
    shutil.rmtree(sandbox["workdir"], ignore_errors=True)


def _persist_outputs(workdir, before, user, skill_id):
    """Зберігає створені файли у сховище користувача; повертає їх метадані."""
    if user is None:
        return []
    from app.services import file_service  # локальний імпорт уникає циклів
    saved = []
    for rel in _collect_new_files(workdir, before):
        src = os.path.join(workdir, rel)
        try:
            uf = file_service.save_path(user, src, display_name=rel,
                                        source="skill_run", skill_id=skill_id)
            saved.append(uf.to_dict())
        except Exception:  # один зіпсований файл не має зривати виконання
            continue
    return saved
    return saved


def _parse_exec_output(stdout, stderr):
    """Очікує JSON {output, usage?} у stdout; інакше — увесь stdout як текст."""
    output = stdout.strip()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        data = json.loads(stdout)
        if isinstance(data, dict) and "output" in data:
            out = data["output"]
            output = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            u = data.get("usage") or {}
            if isinstance(u, dict):
                pt = int(u.get("prompt_tokens", 0) or 0)
                ct = int(u.get("completion_tokens", 0) or 0)
                usage = {"prompt_tokens": pt, "completion_tokens": ct,
                         "total_tokens": int(u.get("total_tokens", pt + ct) or pt + ct)}
    except (ValueError, TypeError):
        pass
    if not output:
        output = "(порожній вивід)" + (f"\n{stderr.strip()}" if stderr.strip() else "")
    return {"output": output, "usage": usage, "stderr": stderr.strip()}
