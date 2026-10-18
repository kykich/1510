# -*- coding: utf-8 -*-
"""MCP-сервер «Файлы проекта» (docs/Final_task.md, задача 4).

Тонкий адаптер над ``rtk_app.file_tasks``: чтение, поиск и безопасная запись
файлов внутри корня проекта. Все инструменты принимают необязательный
``root`` (по умолчанию — корень проекта ассистента).

Инструменты:
  * ping                 — проверка живости;
  * list_files           — список текстовых файлов;
  * read_file            — чтение одного файла;
  * read_many_files      — чтение 2–3+ файлов за один вызов;
  * search_files         — поиск по нескольким файлам;
  * file_info            — размер/признак усечения;
  * preview_file_change  — diff без записи;
  * apply_file_change    — запись только при confirm=True.

Запуск (stdio, как подпроцесс MCP-клиента):
    python files_server.py
"""
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rtk_app import file_tasks  # noqa: E402

mcp = MCPServer("files")


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _root(root=""):
    return file_tasks.resolve_root(root or BASE_DIR)


def _exts(ext=""):
    out = []
    for item in str(ext or "").split(","):
        item = item.strip().lower()
        if item:
            out.append(item if item.startswith(".") else "." + item)
    return tuple(out)


def _err(message):
    return _json({"ok": False, "error": str(message or "ошибка файлов")})


def _action(root, path):
    """update для существующего файла (в т.ч. без расширения), иначе create."""
    try:
        file_tasks.resolve_path(root, path)
        return "update"
    except file_tasks.FileTaskError:
        return "create"


@mcp.tool()
def ping() -> str:
    """Проверка живости MCP-сервера файлов."""
    return "pong: files_server (%s)" % BASE_DIR


@mcp.tool()
def list_files(subdir: str = "", ext: str = "", limit: int = 200,
               root: str = "") -> str:
    """Список текстовых файлов проекта (относительные пути)."""
    try:
        files = file_tasks.list_files(_root(root), subdir=subdir,
                                      exts=_exts(ext), limit=limit)
        return _json({"ok": True, "files": files, "count": len(files),
                      "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def read_file(path: str, max_chars: int = 100000, root: str = "") -> str:
    """Прочитать текстовый файл (path — относительно корня)."""
    try:
        data = file_tasks.read_file(_root(root), path, max_chars=max_chars,
                                    with_meta=True)
        return _json({"ok": True, **data, "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def read_many_files(paths: str = "", subdir: str = "", ext: str = "",
                    limit: int = 3, max_chars: int = 12000,
                    root: str = "") -> str:
    """Прочитать содержимое нескольких файлов (2–3 и больше за один вызов).

    paths — необязательный список путей через запятую/точку с запятой;
    если пуст, берутся первые limit текстовых файлов из subdir/ext.
    """
    try:
        project_root = _root(root)
        max_files = max(1, min(10, int(limit or 3)))
        per_file = max(1000, min(50000, int(max_chars or 12000)))
        total_limit = 40000
        raw = str(paths or "").replace(";", ",").replace("\n", ",")
        selected = [p.strip() for p in raw.split(",") if p.strip()]
        if selected:
            selected = selected[:max_files]
        else:
            selected = file_tasks.list_files(
                project_root, subdir=subdir, exts=_exts(ext),
                limit=max_files)
        out, errors, used = [], [], 0
        for rel in selected:
            try:
                data = file_tasks.read_file(project_root, rel,
                                             max_chars=per_file,
                                             with_meta=True)
                room = max(0, total_limit - used)
                text = data.get("text") or ""
                truncated = bool(data.get("truncated"))
                if len(text) > room:
                    text = text[:room]
                    truncated = True
                out.append({"path": data.get("path") or rel, "text": text,
                            "size": data.get("size"), "truncated": truncated})
                used += len(text)
                if used >= total_limit:
                    break
            except Exception as exc:             # noqa: BLE001
                errors.append({"path": rel, "error": str(exc)})
        return _json({"ok": True, "files": out, "count": len(out),
                      "errors": errors, "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def search_files(query: str, ext: str = "", regex: bool = False,
                 max_results: int = 100, subdir: str = "",
                 root: str = "") -> str:
    """Найти строку/regex по нескольким файлам проекта."""
    try:
        matches = file_tasks.search_files(
            _root(root), query, subdir=subdir, exts=_exts(ext), regex=regex,
            max_results=max_results)
        return _json({"ok": True, "matches": matches,
                      "count": len(matches), "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def file_info(path: str, root: str = "") -> str:
    """Информация о файле: существование, размер, усечение."""
    try:
        data = file_tasks.read_file(_root(root), path, max_chars=1,
                                    with_meta=True)
        return _json({"ok": True, "path": data["path"],
                      "size": data["size"], "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def preview_file_change(path: str, content: str, root: str = "") -> str:
    """Проверить изменение файла и вернуть diff, НЕ записывая файл."""
    try:
        project_root = _root(root)
        action = _action(project_root, path)
        plan = {"summary": "предпросмотр изменения",
                "files": [{"path": path, "action": action,
                           "content": content}]}
        return _json(file_tasks.preview_plan(project_root, plan))
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


@mcp.tool()
def apply_file_change(path: str, content: str, confirm: bool = False,
                      root: str = "") -> str:
    """Записать файл. Без confirm=True запись запрещена."""
    if not confirm:
        return _json({"ok": False, "error": "запись требует confirm=True",
                      "applied": False})
    try:
        project_root = _root(root)
        action = _action(project_root, path)
        plan = {"summary": "запись файла",
                "files": [{"path": path, "action": action,
                           "content": content}]}
        return _json(file_tasks.apply_plan(project_root, plan))
    except Exception as exc:                     # noqa: BLE001
        return _err(exc)


if __name__ == "__main__":
    mcp.run()
