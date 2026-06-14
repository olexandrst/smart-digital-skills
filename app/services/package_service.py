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
    description = (meta.get("description") or "").strip()
    if not name:
        raise ApiError("У skill.md не вказано name", 400, "invalid_skill_md")
    if not description:
        raise ApiError("У skill.md не вказано description", 400, "invalid_skill_md")

    runtime = (meta.get("runtime") or "python").strip().lower()
    if runtime != "python":
        raise ApiError("Підтримується лише runtime: python", 400, "unsupported_runtime")

    entrypoint = _resolve_entrypoint(zf, root, meta.get("entrypoint"))

    inputs = _normalize_inputs(meta.get("inputs", []))

    return {
        "name": name,
        "description": description,
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


def run_package(skill, inputs, user=None):
    """Розпаковує пакет у тимчасову теку та виконує entrypoint у підпроцесі.

    Файли, створені кодом під час виконання, зберігаються у сховище користувача
    (якщо передано user) і повертаються у полі 'files'.
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

    # Робоча тека — усередині застосунку (instance/run_tmp), не у /tmp.
    run_dir = cfg.get("SKILL_RUN_DIR") or os.path.join(os.getcwd(), "instance", "run_tmp")
    os.makedirs(run_dir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="skillrun_", dir=run_dir)
    try:
        root = _safe_extract(file_bytes, workdir)
        rel_entry = skill.entrypoint or "main.py"
        entry = os.path.join(root, rel_entry)
        if not os.path.isfile(entry):
            # Резервний пошук за базовою назвою у розпакованому дереві.
            base = os.path.basename(rel_entry)
            found = None
            for r, _d, files in os.walk(root):
                if base in files:
                    found = os.path.join(r, base)
                    break
            if found is None:
                raise ApiError("Entrypoint не знайдено у пакеті", 400, "missing_entrypoint")
            entry = found
        rel_entry = os.path.relpath(entry, root)

        # Знімок файлів у всій робочій теці ДО виконання (для виявлення нових).
        before = _snapshot(workdir)

        payload = json.dumps({"inputs": inputs or {}}, ensure_ascii=False)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "HOME": workdir,
            "TMPDIR": workdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        # Запускаємо через runpy, додаючи у sys.path і корінь пакета, і теку
        # скрипта — щоб працювали імпорти як від кореня, так і сусідніх модулів
        # (навіть для вкладеного entrypoint на кшталт scripts/build_estimate.py).
        entry_abs = os.path.abspath(entry)
        root_abs = os.path.abspath(root)
        bootstrap = (
            "import sys, os, io, builtins, runpy\n"
            f"_ROOT = {root_abs!r}\n"
            # Перенаправляємо записи у /tmp та /var/tmp у робочу теку додатка,
            # щоб файли, які скіл пише в абсолютний /tmp, зберігались у застосунку
            # і потрапляли у сховище користувача.
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
            f"sys.path.insert(0, {os.path.dirname(entry_abs)!r})\n"
            f"sys.path.insert(0, _ROOT)\n"
            f"runpy.run_path({entry_abs!r}, run_name='__main__')\n"
        )
        timeout = int(cfg.get("SKILL_EXEC_TIMEOUT", 30))
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", bootstrap],
                input=payload,
                capture_output=True,
                text=True,
                cwd=root,
                env=env,
                timeout=timeout,
                preexec_fn=_resource_limits(),
            )
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
