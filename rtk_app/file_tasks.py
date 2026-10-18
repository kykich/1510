# -*- coding: utf-8 -*-
"""Работа с файлами проекта (docs/Final_task.md, задача 4).

Модуль даёт ассистенту инструменты «читать / искать / анализировать /
создавать / изменять» файлы. Пользователь задаёт ЦЕЛЬ, а модуль сам выбирает
файлы и формирует diff. Запись по умолчанию не выполняется: сначала план и
diff, запись — только при ``apply=True``.

Сценарии:
  * find    — поиск использований компонента/API по нескольким файлам;
  * docs    — обновление документации по изменениям кода (git diff);
  * new     — генерация нового файла (ADR / CHANGELOG / README).

Все пути ограничены корнем проекта; служебные папки, bare-репозитории
(``*.git``) и файлы с секретами не читаются и не изменяются.
"""
import difflib
import json
import os
import re
import tempfile
from datetime import date

from . import config, rag

__all__ = [
    "FileTaskError", "resolve_root", "resolve_path", "list_files", "read_file",
    "search_files", "collect_context", "make_unified_diff", "parse_plan",
    "preview_plan", "apply_plan", "run_find", "run_update_docs",
    "run_generate_file", "run_edit_file", "default_caller", "text_extensions",
]


class FileTaskError(ValueError):
    """Ошибка работы с файлами проекта."""


_EXTRA_TEXT_EXTS = (
    ".ini", ".cfg", ".bat", ".ps1", ".sh", ".sql", ".xml", ".csv",
)
_SKIP_DIRS = frozenset(str(x).lower() for x in
                       (getattr(config, "RAG_DOC_SKIP_DIRS", ()) or ())) | {
    ".git", ".idea", ".vscode", ".venv", "venv", "env", "node_modules",
    "__pycache__", "session", "rag_images", "support_data",
}
_SECRET_FILES = frozenset(str(x).lower() for x in
                          (getattr(config, "RAG_DOC_SKIP_FILES", ()) or ())) | {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
}
MAX_FILE_MB = int(getattr(config, "FILES_MAX_FILE_MB", 2) or 2)
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_READ_CHARS = int(getattr(config, "FILES_MAX_READ_CHARS", 200000) or 200000)
MAX_WRITE_CHARS = int(getattr(config, "FILES_MAX_WRITE_CHARS", 200000) or 200000)
MAX_RESULTS = int(getattr(config, "FILES_MAX_RESULTS", 200) or 200)
MAX_CONTEXT_CHARS = int(getattr(config, "FILES_MAX_CONTEXT_CHARS", 16000) or
                        16000)
MAX_DIFF_OUTPUT = int(getattr(config, "FILES_MAX_DIFF_OUTPUT", 24000) or
                      24000)
MAX_PROMPT_CHARS = max(8000, MAX_CONTEXT_CHARS * 2)


def text_extensions():
    """Разрешённые текстовые расширения (PDF не редактируем)."""
    out = []
    for ext in rag.doc_extensions():
        ext = str(ext or "").lower()
        if ext and ext != ".pdf" and ext not in out:
            out.append(ext)
    for ext in _EXTRA_TEXT_EXTS:
        if ext not in out:
            out.append(ext)
    return tuple(out)


def resolve_root(root=None):
    """Абсолютный корень проекта; ошибка, если папки нет."""
    path = os.path.abspath(str(root or "."))
    if not os.path.isdir(path):
        raise FileTaskError("корень проекта не найден: %s" % path)
    return os.path.realpath(path)


def _is_skip_dir(name):
    low = str(name or "").lower()
    if not low:
        return False
    # versions.git и другие bare-репозитории: имя заканчивается на .git.
    return low in _SKIP_DIRS or low.endswith(".git") or low.startswith(".")


def _is_secret_file(name):
    low = str(name or "").lower()
    return (low in _SECRET_FILES or low == ".git"
            or low.startswith(".env"))


def _parts(rel):
    """Относительный путь -> части; запрещает абсолютные пути и '..'."""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        raise FileTaskError("пустой путь")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise FileTaskError("путь вне проекта: %s" % rel)
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise FileTaskError("путь вне проекта: %s" % rel)
    return parts


def _resolve_existing_file(candidate, root_real):
    """Подбирает файл с тем же именем, но с расширением.

    Модель часто пишет «rtk_console» вместо «rtk_console.py». Если файла без
    расширения нет, ищем ровно одно текстовое совпадение по stem в той же
    папке (README -> README.md). Неоднозначность и чужие/секретные файлы
    игнорируются.
    """
    if os.path.isfile(candidate):
        return candidate
    folder, name = os.path.split(candidate)
    if not name or os.path.splitext(name)[1] or not os.path.isdir(folder):
        return candidate
    pattern = re.compile(r"^%s\.([a-z0-9]+)$" % re.escape(name), re.IGNORECASE)
    allowed = text_extensions()
    matches = []
    try:
        names = os.listdir(folder)
    except OSError:
        return candidate
    for entry in names:
        if _is_secret_file(entry):
            continue
        match = pattern.match(entry)
        if not match or ("." + match.group(1).lower()) not in allowed:
            continue
        full = os.path.join(folder, entry)
        if not os.path.isfile(full):
            continue
        real = os.path.realpath(full)
        if real != root_real and not real.startswith(root_real + os.sep):
            continue
        matches.append(real)
    return matches[0] if len(matches) == 1 else candidate


def _safe_path(root, rel, must_exist=False, for_write=False):
    """Безопасный абсолютный путь внутри root."""
    root_real = resolve_root(root)
    parts = _parts(rel)
    if any(_is_skip_dir(p) for p in parts[:-1]):
        raise FileTaskError("служебная папка в пути: %s" % rel)
    if _is_secret_file(parts[-1]):
        raise FileTaskError("запрещённый файл: %s" % rel)
    candidate = os.path.realpath(os.path.join(root_real, *parts))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise FileTaskError("путь вне проекта: %s" % rel)
    if must_exist:
        candidate = _resolve_existing_file(candidate, root_real)
        if not os.path.isfile(candidate):
            raise FileTaskError("файл не найден: %s" % rel)
    if for_write:
        ext = os.path.splitext(candidate)[1].lower()
        if ext not in text_extensions():
            raise FileTaskError("нельзя изменять файл с расширением %s"
                                % (ext or "(нет)"))
    return candidate


def resolve_path(root, rel):
    """Реальный абсолютный путь существующего файла внутри root.

    Учитывает имя без расширения (rtk_console -> rtk_console.py). Нужен
    инструментам preview/apply, чтобы понять, что это update, а не create.
    """
    return _safe_path(resolve_root(root), rel, must_exist=True)


def _read_text(path, max_chars=None):
    """Читает текст, возвращает (текст, truncated, размер)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise FileTaskError("не удалось прочитать файл: %s" % exc)
    if size > MAX_FILE_BYTES:
        raise FileTaskError("файл больше %d МБ — пропущен" % MAX_FILE_MB)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise FileTaskError("не удалось прочитать файл: %s" % exc)
    if b"\x00" in raw[:4096]:
        raise FileTaskError("бинарный файл — пропущен")
    text = ""
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text and raw:
        raise FileTaskError("не удалось определить кодировку")
    limit = int(max_chars if max_chars is not None else MAX_READ_CHARS)
    truncated = bool(limit and len(text) > limit)
    if truncated:
        text = text[:limit]
    return text, truncated, size


def _iter_files(root, subdir="", exts=None):
    """Итератор (относительный путь, абсолютный путь) по текстовым файлам."""
    root_real = resolve_root(root)
    target = _safe_path(root_real, subdir) if str(subdir or "").strip() \
        else root_real
    if not os.path.isdir(target):
        raise FileTaskError("папка не найдена: %s" % subdir)
    allowed = tuple(str(e).lower() if str(e).startswith(".")
                    else "." + str(e).lower() for e in (exts or ()))
    if not allowed:
        allowed = text_extensions()
    for cur, dirs, names in os.walk(target):
        dirs[:] = sorted(d for d in dirs if not _is_skip_dir(d))
        for name in sorted(names):
            if _is_secret_file(name):
                continue
            if not name.lower().endswith(allowed):
                continue
            full = os.path.join(cur, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root_real).replace(os.sep, "/")
            yield rel, full


def list_files(root, subdir="", exts=None, limit=None):
    """Список относительных путей текстовых файлов проекта."""
    limit = max(1, min(1000, int(limit or 500)))
    out = []
    for rel, _full in _iter_files(root, subdir=subdir, exts=exts):
        out.append(rel)
        if len(out) >= limit:
            break
    return out


def read_file(root, rel, max_chars=None, with_meta=False):
    """Читает текстовый файл внутри root.

    Возвращает текст или dict {path, text, truncated, size} при with_meta=True.
    """
    root_real = resolve_root(root)
    path = _safe_path(root_real, rel, must_exist=True)
    text, truncated, size = _read_text(path, max_chars=max_chars)
    if with_meta:
        actual = os.path.relpath(path, root_real).replace(os.sep, "/")
        return {"path": actual, "text": text,
                "truncated": truncated, "size": size}
    return text


def search_files(root, query, subdir="", exts=None, regex=False,
                 case_sensitive=False, max_results=None):
    """Ищет строку/regex по нескольким файлам проекта.

    Возвращает список {path, line, text, match}. Служебные папки, bare-репо
    (*.git), секреты, бинарники и слишком большие файлы пропускаются.
    """
    query = str(query or "").strip()
    if not query:
        raise FileTaskError("пустой поисковый запрос")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise FileTaskError("некорректный regex: %s" % exc)
    limit = max(1, min(2000, int(max_results or MAX_RESULTS)))
    out = []
    for rel, full in _iter_files(root, subdir=subdir, exts=exts):
        try:
            text, _trunc, _size = _read_text(full)
        except FileTaskError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            match = pattern.search(line)
            if not match:
                continue
            out.append({"path": rel, "line": number,
                        "text": line.strip()[:500],
                        "match": match.group(0)[:200]})
            if len(out) >= limit:
                return out
    return out


def collect_context(root, query="", paths=None, max_files=5,
                    max_chars=None, exts=None):
    """Собирает текст 2–5 файлов для промпта модели.

    paths — явный список путей; иначе файлы выбираются по числу совпадений
    query (если совпадений нет — README/документация). Возвращает
    (context_text, [пути]).
    """
    root_real = resolve_root(root)
    limit = int(max_chars or MAX_CONTEXT_CHARS)
    chosen = []
    if paths:
        for path in paths:
            try:
                target = _safe_path(root_real, path, must_exist=True)
                chosen.append(os.path.relpath(target, root_real)
                              .replace(os.sep, "/"))
            except FileTaskError:
                continue
    else:
        query = str(query or "").strip()
        hits = search_files(root_real, query, exts=exts,
                            max_results=MAX_RESULTS) if query else []
        counts = {}
        for hit in hits:
            counts[hit["path"]] = counts.get(hit["path"], 0) + 1
        chosen = [p for p in sorted(counts, key=lambda x: (-counts[x], x))][:
            max(1, int(max_files))]
        if not chosen:
            docs = list_files(root_real, exts=(".md", ".mdx", ".rst", ".txt"))
            preferred = [p for p in docs if p.lower() == "readme.md"]
            preferred += [p for p in docs if p.lower().startswith("docs/")]
            chosen = (preferred or docs)[:max(1, int(max_files))]
    parts = []
    used = 0
    kept = []
    for path in chosen:
        try:
            text = read_file(root_real, path)
        except FileTaskError:
            continue
        piece = "ФАЙЛ: %s\n%s" % (path, text.strip())
        if used + len(piece) > limit and parts:
            break
        parts.append(piece[:max(0, limit - used)])
        used += len(parts[-1]) + 2
        kept.append(path)
    return "\n\n".join(parts), kept


def make_unified_diff(path, old, new):
    """Строит unified diff для файла (new/old — полные тексты)."""
    old_lines = str(old or "").splitlines(True)
    new_lines = str(new or "").splitlines(True)
    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile="a/%s" % path, tofile="b/%s" % path,
        lineterm="")
    return "\n".join(diff)


def _json_object(text):
    """Достаёт JSON-объект из ответа модели (в т.ч. из ```json-блока)."""
    if isinstance(text, dict):
        return text
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise FileTaskError("модель не вернула JSON-план")
    try:
        return json.loads(raw[start:end + 1], strict=False)
    except ValueError as exc:
        raise FileTaskError("невалидный JSON-план: %s" % exc)


def parse_plan(text):
    """Разбирает JSON-план модели: {summary, files:[{path,action,content}]}."""
    data = _json_object(text)
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise FileTaskError("в JSON-плане нет списка files")
    out = []
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise FileTaskError("элемент files должен быть объектом")
        path = str(item.get("path") or "").strip().replace("\\", "/")
        action = str(item.get("action") or "").strip().lower()
        content = item.get("content")
        if not path:
            raise FileTaskError("в плане не указан path")
        if action in ("replace", "edit"):
            action = "update"
        if action not in ("create", "update"):
            raise FileTaskError("недопустимое действие %r" % action)
        if not isinstance(content, str):
            raise FileTaskError("content должен быть строкой: %s" % path)
        if not content.strip():
            raise FileTaskError("пустое содержимое файла: %s" % path)
        if len(content) > MAX_WRITE_CHARS:
            raise FileTaskError("слишком большой файл в плане: %s" % path)
        if path in seen:
            raise FileTaskError("файл указан дважды: %s" % path)
        seen.add(path)
        out.append({"path": path, "action": action, "content": content})
    return {"summary": str(data.get("summary") or "").strip(),
            "notes": str(data.get("notes") or "").strip(), "files": out}


def _extract_file_content(text):
    """Достаёт содержимое файла из ответа модели.

    Основной протокол — маркеры <<<FILE>>> … <<<END>>> (не требует JSON-
    экранирования). Для совместимости поддерживается и старый JSON-план,
    и обычный текст/markdown-блок.
    """
    raw = str(text or "")
    start = raw.find("<<<FILE>>>")
    end = raw.find("<<<END>>>", start + 1 if start >= 0 else 0)
    if start >= 0:
        body = raw[start + len("<<<FILE>>>"):]
        if end >= 0:
            body = raw[start + len("<<<FILE>>>"):end]
        return body.strip("\r\n")
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if "\n" in stripped:
            first, rest = stripped.split("\n", 1)
            if first.strip().lower() in ("json", "markdown", "md", "css",
                                         "html", "text", ""):
                stripped = rest
        stripped = stripped.strip()
    if "{" in stripped and '"files"' in stripped:
        try:
            plan = parse_plan(stripped)
            return plan["files"][0]["content"]
        except FileTaskError:
            pass
    return stripped


def _prepare_plan(root, plan):
    """Внутренняя проверка плана: безопасные пути, старая/новая версия, diff."""
    root_real = resolve_root(root)
    data = plan if isinstance(plan, dict) and plan.get("files") else \
        parse_plan(plan)
    items = []
    for item in data.get("files") or []:
        rel = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        action = item.get("action")
        if action not in ("create", "update"):
            raise FileTaskError("недопустимое действие %r: %s"
                                % (action, rel))
        target = _safe_path(root_real, rel, for_write=True,
                            must_exist=(action == "update"))
        if action == "update":
            rel = os.path.relpath(target, root_real).replace(os.sep, "/")
        exists = os.path.isfile(target)
        if action == "create" and exists:
            raise FileTaskError("файл уже существует: %s" % rel)
        if action == "update" and not exists:
            raise FileTaskError("файла нет для update: %s" % rel)
        old = _read_text(target, max_chars=MAX_WRITE_CHARS)[0] if exists else ""
        new = str(item.get("content") or "")
        # Пустой create разрешён: пользователь может попросить создать
        # пустой файл (info.md), не придумывая содержимое.
        if not new.strip() and action != "create":
            raise FileTaskError("пустое содержимое файла: %s" % rel)
        if len(new) > MAX_WRITE_CHARS:
            raise FileTaskError("слишком большой файл в плане: %s" % rel)
        diff = make_unified_diff(rel, old, new)
        items.append({"path": rel, "action": action, "target": target,
                      "old": old, "new": new, "diff": diff,
                      "changed": bool(diff) or action == "create"})
    return {"summary": data.get("summary") or "",
            "notes": data.get("notes") or ""}, items


def _public_plan(data, items):
    """Публичное представление плана без полного содержимого файлов."""
    diffs = [i["diff"] for i in items if i["diff"]]
    diff_text = "\n".join(diffs)
    truncated = len(diff_text) > MAX_DIFF_OUTPUT
    if truncated:
        diff_text = diff_text[:MAX_DIFF_OUTPUT] + "\n... diff обрезан ..."
    return {
        "ok": True,
        "summary": data.get("summary") or "",
        "notes": data.get("notes") or "",
        "files": [{"path": i["path"], "action": i["action"],
                   "changed": i["changed"]} for i in items],
        "changed": [i["path"] for i in items if i["changed"]],
        "diff": diff_text,
        "diff_truncated": truncated,
        "applied": False,
        "written": [],
        "error": None,
    }


def preview_plan(root, plan):
    """Проверяет план и возвращает diff, НЕ записывая файлы."""
    try:
        data, items = _prepare_plan(root, plan)
        return _public_plan(data, items)
    except FileTaskError as exc:
        return {"ok": False, "error": str(exc), "files": [], "changed": [],
                "diff": "", "applied": False, "written": []}


def _write_atomic(target, content):
    """Атомарная запись UTF-8 через временный файл рядом."""
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".filetmp-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, target)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def apply_plan(root, plan):
    """Проверяет план и записывает файлы (create/update), возвращает diff."""
    try:
        data, items = _prepare_plan(root, plan)
        written = []
        for item in items:
            if item["changed"]:
                _write_atomic(item["target"], item["new"])
                written.append(item["path"])
        result = _public_plan(data, items)
        result["applied"] = True
        result["written"] = written
        return result
    except FileTaskError as exc:
        return {"ok": False, "error": str(exc), "files": [], "changed": [],
                "diff": "", "applied": False, "written": []}
    except OSError as exc:
        return {"ok": False, "error": "не удалось записать файл: %s" % exc,
                "files": [], "changed": [], "diff": "", "applied": False,
                "written": []}


def _diff_block(diff):
    if not diff:
        return "Изменений нет."
    return "```diff\n%s\n```" % diff


def _result_text(title, data, public, input_files=None):
    """Markdown-ответ для docs/new."""
    lines = [title, ""]
    if data.get("summary"):
        lines += [data["summary"], ""]
    selected = [str(p) for p in (input_files or []) if str(p or "").strip()]
    if selected:
        lines.append("Автоматически выбраны файлы:")
        lines.extend("- `%s`" % path for path in selected)
        lines.append("")
    lines.append("Файлы:")
    for item in public.get("files") or []:
        mark = "изменён" if item.get("changed") else "без изменений"
        lines.append("- `%s` — %s, %s" % (item["path"], item["action"], mark))
    if not public.get("files"):
        lines.append("- (нет)")
    lines += ["", "Diff:", _diff_block(public.get("diff") or "")]
    if public.get("applied"):
        lines += ["", "Записано: %s" % (", ".join(public.get("written") or [])
                                        or "нет изменений")]
    else:
        lines += ["", "Файлы НЕ изменены (preview). Для записи: `--apply`."]
    if public.get("notes"):
        lines += ["", public["notes"]]
    return "\n".join(lines)


def _unique_paths(paths, limit=12):
    """Уникальные пути в порядке появления (для отчёта «выбраны файлы»)."""
    out = []
    for path in paths or []:
        path = str(path or "").replace("\\", "/").strip()
        if path and path not in out:
            out.append(path)
        if len(out) >= limit:
            break
    return out


def _rag_paths(root, hits, limit=8):
    """Относительные пути RAG-фрагментов для отчёта."""
    out = []
    for hit in hits or []:
        full = str((hit or {}).get("file") or "")
        if not full:
            continue
        try:
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel.startswith(".."):
                rel = os.path.basename(full)
        except (ValueError, OSError):
            rel = os.path.basename(full)
        if rel and rel not in out:
            out.append(rel)
        if len(out) >= limit:
            break
    return out


def run_find(root, query, subdir="", exts=None, regex=False,
             max_results=None):
    """Сценарий 1: поиск использований по нескольким файлам."""
    try:
        matches = search_files(root, query, subdir=subdir, exts=exts,
                               regex=regex, max_results=max_results)
    except FileTaskError as exc:
        return {"ok": False, "scenario": "find", "text": "## Поиск\n\n_%s_"
                % exc, "files": [], "matches": [], "total": 0,
                "error": str(exc), "model_used": False}
    by_file = {}
    for hit in matches:
        by_file.setdefault(hit["path"], []).append(hit)
    lines = ["## Поиск: `%s`" % query, ""]
    if matches:
        lines.append("Найдено **%d** совпадений в **%d** файлах."
                     % (len(matches), len(by_file)))
    else:
        lines.append("Ничего не найдено.")
    for path in sorted(by_file, key=lambda p: (-len(by_file[p]), p)):
        lines += ["", "### `%s` — %d" % (path, len(by_file[path]))]
        for hit in by_file[path][:10]:
            lines.append("- строка %d: `%s`" % (hit["line"], hit["text"]))
    return {"ok": True, "scenario": "find", "text": "\n".join(lines),
            "files": sorted(by_file), "input_files": sorted(by_file),
            "matches": matches, "total": len(matches), "error": None,
            "model_used": False}


def _select_docs(root, changes, max_docs=2):
    """Выбирает документы, которые нужно обновить после изменений кода."""
    from . import pr_review
    docs = list_files(root, exts=(".md", ".mdx", ".rst", ".txt"))
    if not docs:
        return []
    query = pr_review.build_rag_query(changes.get("files") or [],
                                      changes.get("diff") or "", limit=1200)
    terms = [t.lower() for t in re.findall(r"[A-Za-zА-Яа-я_][\w.-]{2,}",
                                           query)]
    for item in changes.get("files") or []:
        base = os.path.basename(str(item.get("path") or ""))
        stem = os.path.splitext(base)[0]
        if stem:
            terms.extend([stem.lower(), base.lower()])
    scores = {}
    for path in docs:
        try:
            text = read_file(root, path).lower()
        except FileTaskError:
            continue
        score = sum(text.count(term) for term in set(terms) if term)
        if path.lower() == "readme.md":
            score += 5
        if path.lower().startswith("docs/"):
            score += 2
        scores[path] = score
    ordered = sorted(scores, key=lambda p: (-scores[p], p))
    if ordered and scores.get(ordered[0], 0) == 0:
        preferred = [p for p in docs if p.lower() == "readme.md"]
        ordered = preferred + ordered
    return ordered[:max(1, int(max_docs or 2))]


def _docs_messages(changes, docs, rag_text=""):
    """Промпт для обновления документации."""
    from . import pr_review
    allowed = ", ".join(p for p, _t in docs)
    system = "\n".join([
        "Ты — инженер документации проекта.",
        "Обнови ТОЛЬКО перечисленные документы по diff и контексту.",
        "Не выдумывай факты вне diff/RAG/текущих документов.",
        "Сохрани стиль и структуру документа.",
        "Верни СТРОГО JSON-объект без пояснений:",
        '{"summary":"что изменено", "files":[{"path":"...",'
        ' "action":"update", "content":"ПОЛНЫЙ новый текст файла"}]}',
        "Допустимые пути: %s." % (allowed or "(нет)"),
    ])
    doc_text = []
    for path, text in docs:
        doc_text.append("--- %s ---\n%s" % (path, text))
    user = "\n".join([
        "Изменённые файлы:",
        "\n".join("- %s %s" % (f.get("status", "?"), f.get("path", ""))
                  for f in (changes.get("files") or [])),
        "",
        "DIFF:",
        str(changes.get("diff") or "")[:60000],
        "",
        "ТЕКУЩИЕ ДОКУМЕНТЫ:",
        "\n\n".join(doc_text),
        "",
        "RAG-КОНТЕКСТ:",
        str(rag_text or "")[:8000] or "контекст не предоставлен",
    ])
    return [{"role": "system", "content": system},
            {"role": "user", "content": user[:MAX_PROMPT_CHARS]}]


def _generate_messages(kind, topic, target, context, rag_text="", git_log="",
                       action="create"):
    """Промпт для генерации нового файла."""
    system = "\n".join([
        "Ты — технический писатель проекта.",
        "Подготовь содержимое нового/обновлённого файла по цели пользователя.",
        "Опирайся только на предоставленный контекст проекта.",
        "Не выдумывай факты, которых нет в контексте.",
        "Верни ТОЛЬКО содержимое файла между маркерами:",
        "<<<FILE>>>",
        "...",
        "<<<END>>>",
        "Без JSON, без markdown-обёртки и без пояснений.",
        "Целевой путь: %s (%s)." % (target, action),
    ])
    user = "\n".join([
        "Тип файла: %s" % kind,
        "Цель: %s" % topic,
        "Целевой путь: %s" % target,
        "",
        "КОНТЕКСТ ПРОЕКТА:",
        str(context or "")[:12000] or "контекст не найден",
        "",
        "RAG-КОНТЕКСТ:",
        str(rag_text or "")[:6000] or "контекст не предоставлен",
        "",
        "ПОСЛЕДНИЕ КОММИТЫ:",
        str(git_log or "")[:4000] or "нет данных",
    ])
    return [{"role": "system", "content": system},
            {"role": "user", "content": user[:MAX_PROMPT_CHARS]}]


def default_caller(model=None, max_tokens=4000, temperature=0.2,
                   local_url=None, local_model=None, local_label=None):
    """Возвращает функцию вызова модели (DeepSeek/GigaChat/Local)."""
    from . import pr_review
    return pr_review._default_caller(
        model, temperature=temperature, max_tokens=max_tokens,
        local_url=local_url, local_model=local_model,
        local_label=local_label)


def _call_model(caller, model, messages, max_tokens=4000):
    call = caller or default_caller(model, max_tokens=max_tokens)
    return call(messages)


def _usage(result):
    result = result or {}
    return {"input": int(result.get("prompt_tokens", 0) or 0),
            "output": int(result.get("completion_tokens", 0) or 0)}


def _fail(scenario, title, error):
    return {"ok": False, "scenario": scenario,
            "text": "%s\n\n_%s_" % (title, error), "files": [],
            "changed": [], "diff": "", "applied": False, "error": str(error),
            "rag_used": False, "usage": None}


def run_update_docs(root, base="HEAD~1", head="HEAD", model=None, caller=None,
                    apply=False, use_rag=True, top_k=8, target_paths=None):
    """Сценарий 2: обновить документацию по изменениям кода."""
    scenario, title = "docs", "## Обновление документации"
    try:
        root_real = resolve_root(root)
        from . import pr_review
        changes = pr_review.collect_changes(root_real, base, head)
        if not changes.get("ok"):
            return _fail(scenario, title, changes.get("error") or "нет diff")
        if not changes.get("files") and not str(changes.get("diff") or "").strip():
            return _fail(scenario, title, "нет изменений между %s и %s"
                         % (base, head))
        targets = list(target_paths or []) or _select_docs(root_real, changes)
        docs = []
        for path in targets:
            try:
                docs.append((path, read_file(root_real, path,
                                             max_chars=20000)))
            except FileTaskError:
                continue
        if not docs:
            return _fail(scenario, title, "не найдены документы для обновления")
        rag_text, hits, rag_note = "", [], ""
        if use_rag:
            query = pr_review.build_rag_query(changes.get("files") or [],
                                              changes.get("diff") or "")
            rag_text, hits, rag_note = pr_review.rag_context(
                root_real, query, top_k=top_k)
        messages = _docs_messages(changes, docs, rag_text)
        try:
            model_result = _call_model(caller, model, messages)
        except Exception as exc:                 # noqa: BLE001
            return _fail(scenario, title, "модель недоступна: %s" % exc)
        plan = parse_plan((model_result or {}).get("content") or "")
        allowed = set(path.replace("\\", "/") for path, _text in docs)
        for item in plan["files"]:
            path = item["path"].replace("\\", "/")
            if path not in allowed:
                return _fail(scenario, title,
                             "модель предложила файл вне разрешённых: %s" % path)
            item["action"] = "update"
        public = apply_plan(root_real, plan) if apply else preview_plan(root_real,
                                                                        plan)
        if not public.get("ok"):
            return _fail(scenario, title, public.get("error") or
                         "план не применён")
        data = {"summary": plan.get("summary") or "",
                "notes": plan.get("notes") or ""}
        input_files = _unique_paths(
            [f.get("path") for f in (changes.get("files") or [])]
            + [path for path, _text in docs]
            + _rag_paths(root_real, hits))
        text = _result_text(title, data, public, input_files=input_files)
        return {"ok": True, "scenario": scenario, "text": text,
                "summary": data["summary"], "files": public.get("files") or [],
                "input_files": input_files,
                "changed": public.get("changed") or [],
                "diff": public.get("diff") or "", "applied": bool(apply),
                "written": public.get("written") or [],
                "rag_used": bool(rag_text), "rag_hits": len(hits),
                "rag_note": rag_note, "usage": _usage(model_result),
                "model": model or "", "error": None}
    except FileTaskError as exc:
        return _fail(scenario, title, exc)


def _edit_terms(instruction):
    """Термины из инструкции для поиска файла-цели."""
    text = str(instruction or "")
    terms = re.findall(r"`([^`]+)`", text)
    terms += re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", text)
    # Русские слова тоже важны: «поле ввода запроса» -> «ввод», «запрос».
    words = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё0-9_-]{3,}", text)
    for word in words:
        terms.append(word)
        stem = re.sub(r"(ами|ями|ого|его|ому|ему|ыми|ими|ая|яя|ое|ее|"
                      r"ые|ие|ый|ий|ой|ей|ам|ям|ах|ях|ом|ем|ов|ев|"
                      r"а|я|о|е|ы|и|у|ю|ь)$", "", word)
        if len(stem) >= 4 and stem != word:
            terms.append(stem)
    out = []
    for term in terms:
        term = term.strip(" .,:;()[]{}\"'")
        if len(term) < 3:
            continue
        if term.lower() in ("что", "это", "для", "как", "все", "файл",
                            "добавь", "измени", "замени", "сделай"):
            continue
        if term not in out:
            out.append(term)
    return out[:12]


def select_edit_target(root, instruction):
    """Выбирает файл для goal-level редактирования по инструкции.

    Ищет идентификаторы/имена из инструкции по текстовым файлам проекта.
    Предпочитает код документации; при отсутствии однозначного лидера —
    возвращает ошибку, чтобы не изменить не тот файл.
    """
    root_real = resolve_root(root)
    terms = _edit_terms(instruction)
    if not terms:
        raise FileTaskError("не удалось определить файл по инструкции")
    scores = {}
    for term in terms:
        for hit in search_files(root_real, term, max_results=MAX_RESULTS):
            path = hit.get("path") or ""
            if not path:
                continue
            score = scores.get(path, 0) + 1
            ext = os.path.splitext(path)[1].lower()
            if ext in (".py", ".js", ".ts", ".go", ".java", ".rs", ".c",
                       ".cpp", ".cs", ".css"):
                score += 4
            elif ext in (".html", ".htm"):
                score += 2
            elif ext in (".md", ".txt", ".rst"):
                score += 1
            if path.startswith("answer/"):
                score -= 2
            scores[path] = score
    if not scores:
        raise FileTaskError("файл по инструкции не найден: %s"
                            % ", ".join(terms))
    low = str(instruction or "").lower()
    preferred = ()
    if any(w in low for w in ("css", "фон", "цвет", "стил", "шрифт",
                              "отступ", "рамк", "кнопк", "поле ввода",
                              "input", "textarea")):
        preferred = (".css", ".html", ".htm")
    elif any(w in low for w in ("readme", "документац", "docs", "markdown")):
        preferred = (".md", ".mdx", ".rst", ".txt")
    if preferred:
        filtered = {p: s for p, s in scores.items()
                    if os.path.splitext(p)[1].lower() in preferred}
        if filtered:
            scores = filtered
    ordered = sorted(scores, key=lambda p: (-scores[p], len(p), p))
    best = ordered[0]
    if len(ordered) > 1 and scores[ordered[1]] == scores[best]:
        raise FileTaskError("не удалось однозначно выбрать файл; "
                            "укажите путь явно")
    return best


def _edit_messages(rel, old_text, instruction):
    """Промпт для изменения существующего файла по инструкции пользователя."""
    system = "\n".join([
        "Ты — ассистент разработчика. Измени существующий файл по инструкции.",
        "Сохрани всё содержимое, которое не относится к изменению.",
        "Не сокращай файл и не выдумывай факты вне текущего содержимого.",
        "Верни ТОЛЬКО полное новое содержимое файла между маркерами:",
        "<<<FILE>>>",
        "...",
        "<<<END>>>",
        "Без JSON, без markdown-обёртки и без пояснений.",
        "Целевой файл: %s." % rel,
    ])
    user = "\n".join([
        "Файл: %s" % rel,
        "Инструкция: %s" % instruction,
        "",
        "ТЕКУЩЕЕ СОДЕРЖИМОЕ:",
        str(old_text or ""),
    ])
    return [{"role": "system", "content": system},
            {"role": "user", "content": user[:MAX_PROMPT_CHARS]}]


def run_edit_file(root, path, instruction, model=None, caller=None,
                  apply=False, max_tokens=6000):
    """Сценарий 4: изменить существующий файл по инструкции в чате.

    Модель получает текущий текст и возвращает полное новое содержимое;
    по умолчанию строится diff, запись — только при apply=True.
    """
    scenario, title = "edit", "## Редактирование файла"
    try:
        root_real = resolve_root(root)
        instruction = str(instruction or "").strip()
        if not instruction:
            return _fail(scenario, title, "не указана инструкция изменения")
        raw_path = str(path or "").strip()
        if not raw_path:
            # Goal-level: файл выбирается по терминам инструкции
            # (например, «замени MODEL …» -> файл, где встречается MODEL).
            rel = select_edit_target(root_real, instruction)
        elif os.path.isabs(raw_path):
            real = os.path.realpath(raw_path)
            if real != root_real and not real.startswith(root_real + os.sep):
                return _fail(scenario, title, "путь вне проекта: %s" % path)
            rel = os.path.relpath(real, root_real).replace(os.sep, "/")
        else:
            rel = raw_path
        target = resolve_path(root_real, rel)
        rel = os.path.relpath(target, root_real).replace(os.sep, "/")
        data = read_file(root_real, rel, max_chars=MAX_PROMPT_CHARS,
                         with_meta=True)
        if data.get("truncated") or data.get("size", 0) > MAX_PROMPT_CHARS:
            return _fail(scenario, title,
                         "файл слишком большой для редактирования: %s" % rel)
        messages = _edit_messages(rel, data.get("text") or "", instruction)
        try:
            model_result = _call_model(caller, model, messages,
                                       max_tokens=max_tokens)
        except Exception as exc:                 # noqa: BLE001
            return _fail(scenario, title, "модель недоступна: %s" % exc)
        content = _extract_file_content(
            (model_result or {}).get("content") or "")
        if not content.strip():
            return _fail(scenario, title, "модель вернула пустой файл")
        plan = {"summary": "изменение по инструкции", "notes": "",
                "files": [{"path": rel, "action": "update",
                           "content": content}]}
        public = apply_plan(root_real, plan) if apply else preview_plan(
            root_real, plan)
        if not public.get("ok"):
            return _fail(scenario, title, public.get("error") or
                         "план не применён")
        preview = {"summary": plan.get("summary") or "",
                   "notes": plan.get("notes") or ""}
        text = _result_text(title, preview, public, input_files=[rel])
        return {"ok": True, "scenario": scenario, "text": text,
                "summary": preview["summary"],
                "files": public.get("files") or [],
                "input_files": [rel],
                "changed": public.get("changed") or [],
                "diff": public.get("diff") or "",
                "applied": bool(apply),
                "written": public.get("written") or [],
                "rag_used": False, "usage": _usage(model_result),
                "model": model or "", "error": None}
    except FileTaskError as exc:
        return _fail(scenario, title, exc)


def _slug(text, limit=48):
    value = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "-", str(text or "").strip())
    value = re.sub(r"-+", "-", value).strip("-_")
    return (value[:limit].strip("-_") or "file").lower()


def _unique_target(root, target):
    base, ext = os.path.splitext(target)
    candidate = target
    number = 2
    while os.path.isfile(os.path.join(root, candidate)) and number < 100:
        candidate = "%s-%d%s" % (base, number, ext)
        number += 1
    return candidate


def run_generate_file(root, kind="adr", topic="", model=None, caller=None,
                      apply=False, use_rag=True, path="", top_k=8):
    """Сценарий 3: сгенерировать новый файл (ADR/CHANGELOG/README)."""
    scenario, title = "new", "## Новый файл"
    try:
        root_real = resolve_root(root)
        kind = str(kind or "adr").strip().lower()
        topic = str(topic or "").strip() or "последние изменения проекта"
        if path:
            target = str(path).replace("\\", "/").lstrip("/")
        elif kind in ("adr", "decision"):
            target = "docs/ADR-%s-%s.md" % (date.today().strftime("%Y%m%d"),
                                            _slug(topic))
            target = _unique_target(root_real, target)
        elif kind in ("changelog", "changes"):
            target = "CHANGELOG.md"
        elif kind == "readme":
            target = "docs/README-%s.md" % _slug(topic)
        else:
            target = "docs/%s-%s.md" % (_slug(kind, 24), _slug(topic))
        _safe_path(root_real, target, for_write=True)
        action = "update" if os.path.isfile(os.path.join(root_real, target)) \
            else "create"
        context, chosen = collect_context(root_real, topic, max_files=5)
        from . import pr_review
        rag_text, hits, rag_note = "", [], ""
        if use_rag:
            rag_text, hits, rag_note = pr_review.rag_context(
                root_real, topic, top_k=top_k)
        git_log = ""
        if kind in ("changelog", "changes"):
            git_log, _err = pr_review._run_git(root_real, ["log", "--oneline",
                                                           "-20"])
            git_log = git_log or ""
        messages = _generate_messages(kind, topic, target, context, rag_text,
                                      git_log, action=action)
        try:
            model_result = _call_model(caller, model, messages)
        except Exception as exc:                 # noqa: BLE001
            return _fail(scenario, title, "модель недоступна: %s" % exc)
        content = _extract_file_content(
            (model_result or {}).get("content") or "")
        if not content.strip():
            return _fail(scenario, title, "модель вернула пустой файл")
        plan = {"summary": "файл создан по цели пользователя", "notes": "",
                "files": [{"path": target, "action": action,
                           "content": content}]}
        public = apply_plan(root_real, plan) if apply else preview_plan(
            root_real, plan)
        if not public.get("ok"):
            return _fail(scenario, title, public.get("error") or
                         "план не применён")
        data = {"summary": plan.get("summary") or "",
                "notes": plan.get("notes") or ""}
        input_files = _unique_paths(list(chosen) + _rag_paths(root_real, hits))
        text = _result_text(title, data, public, input_files=input_files)
        return {"ok": True, "scenario": scenario, "text": text,
                "summary": data["summary"], "files": public.get("files") or [],
                "input_files": input_files,
                "changed": public.get("changed") or [],
                "diff": public.get("diff") or "", "applied": bool(apply),
                "written": public.get("written") or [],
                "rag_used": bool(rag_text), "rag_hits": len(hits),
                "rag_note": rag_note, "context_files": chosen,
                "usage": _usage(model_result), "model": model or "",
                "target": target, "error": None}
    except FileTaskError as exc:
        return _fail(scenario, title, exc)






