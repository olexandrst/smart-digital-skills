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

    entrypoint = (meta.get("entrypoint") or "main.py").strip()
    # Перевіряємо, що entrypoint існує в архіві.
    entry_in_zip = f"{root}/{entrypoint}" if root else entrypoint
    if entry_in_zip not in zf.namelist():
        raise ApiError(f"Entrypoint '{entrypoint}' не знайдено в архіві",
                       400, "missing_entrypoint")

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


def run_package(skill, inputs):
    """Розпаковує пакет у тимчасову теку та виконує entrypoint у підпроцесі."""
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

    workdir = tempfile.mkdtemp(prefix="skillrun_")
    try:
        root = _safe_extract(file_bytes, workdir)
        entry = os.path.join(root, skill.entrypoint or "main.py")
        if not os.path.isfile(entry):
            raise ApiError("Entrypoint не знайдено у пакеті", 400, "missing_entrypoint")

        payload = json.dumps({"inputs": inputs or {}}, ensure_ascii=False)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "HOME": workdir,
            "TMPDIR": workdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        timeout = int(cfg.get("SKILL_EXEC_TIMEOUT", 30))
        try:
            proc = subprocess.run(
                [sys.executable, "-I", os.path.basename(entry)],
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

        return _parse_exec_output(stdout, stderr)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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
