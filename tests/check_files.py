# -*- coding: utf-8 -*-
"""Проверка работы с файлами проекта (docs/Final_task.md, задача 4).

Покрывает:
  1. sandbox: пути, секреты, bare-репозитории, бинарники и лимиты;
  2. чтение и поиск по нескольким файлам;
  3. JSON-план, preview и атомарную запись create/update;
  4. сценарий поиска использований;
  5. обновление документации по git diff (фейковая модель);
  6. генерацию ADR/CHANGELOG;
  7. MCP-сервер «Файлы проекта»;
  8. команду `/files` и HTTP-интеграцию.

Запуск: python -m tests.check_files
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import file_tasks, pr_review  # noqa: E402
from rtk_app.agent import apply_tool_defaults  # noqa: E402
from rtk_app.session_store import SessionStore  # noqa: E402
import files_server  # noqa: E402
import web.server as web_server  # noqa: E402

def _write(path, text, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as fh:
            fh.write(text)
    else:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

def _make_project():
    """Временный проект: 2–3 файла с Widget + секреты и bare-репозиторий."""
    root = tempfile.mkdtemp(prefix="files_task_")
    _write(os.path.join(root, "README.md"),
           "# Demo\n\nWidget используется в приложении.\n")
    _write(os.path.join(root, "docs", "GUIDE.md"),
           "# Guide\n\n`Widget()` создаётся в app.py.\n")
    _write(os.path.join(root, "pkg", "widget.py"),
           "class Widget:\n    def render(self):\n        return 'widget'\n")
    _write(os.path.join(root, "app.py"),
           "from pkg.widget import Widget\n\n"
           "widget = Widget()\nprint(widget.render())\n")
    _write(os.path.join(root, "tests", "test_app.py"),
           "def test_widget():\n    assert 'widget'\n")
    _write(os.path.join(root, "apidpsk.txt"), "SECRET-KEY")
    _write(os.path.join(root, ".env"), "TOKEN=SECRET")
    _write(os.path.join(root, "archive.git", "objects", "secret.py"),
           "SECRET = 'inside bare repo'")
    _write(os.path.join(root, "binary.bin"), b"\x00\x01\x02", binary=True)
    return root

def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "Test", "GIT_COMMITTER_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_EMAIL": "test@example.com"})
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")

def _make_git_project():
    root = _make_project()
    _git(root, "init")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    _write(os.path.join(root, "pkg", "widget.py"),
           "class Widget:\n    def render(self):\n        return 'widget-v2'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: widget v2")
    return root

def test_sandbox():
    section("1. Sandbox: пути, секреты, bare-репозитории, бинарники")
    root = _make_project()
    files = file_tasks.list_files(root)
    check("README найден", "README.md" in files)
    check("код найден", "pkg/widget.py" in files and "app.py" in files)
    check("секрет apidpsk.txt скрыт", "apidpsk.txt" not in files)
    check(".env скрыт", ".env" not in files)
    check("bare-репозиторий archive.git скрыт",
          not any(p.startswith("archive.git/") for p in files))
    check("бинарник не попал в список", "binary.bin" not in files)
    check("PDF не входит в редактируемые расширения",
          ".pdf" not in file_tasks.text_extensions())

    text = file_tasks.read_file(root, "README.md")
    check("read_file читает текст", "Widget" in text)
    meta = file_tasks.read_file(root, "README", with_meta=True)
    check("read_file без расширения находит README.md",
          meta.get("path") == "README.md" and "Widget" in meta.get("text", ""))
    meta = file_tasks.read_file(root, "pkg/widget", with_meta=True)
    check("read_file без расширения находит pkg/widget.py",
          meta.get("path") == "pkg/widget.py")
    for bad in ("binary", "../outside.txt", "C:/Windows/win.ini",
                "apidpsk.txt", ".env", "binary.bin"):
        raised = False
        try:
            file_tasks.read_file(root, bad)
        except file_tasks.FileTaskError:
            raised = True
        check("read_file отклоняет %s" % bad, raised)

def test_search():
    section("2. Поиск по нескольким файлам")
    root = _make_project()
    hits = file_tasks.search_files(root, "Widget")
    paths = {h["path"] for h in hits}
    check("совпадения в 3+ файлах", len(paths) >= 3, "%r" % paths)
    check("app.py содержит совпадение", "app.py" in paths)
    check("pkg/widget.py содержит совпадение", "pkg/widget.py" in paths)
    check("README содержит совпадение", "README.md" in paths)
    check("секреты не найдены",
          not any("apidpsk" in p or ".env" in p for p in paths))
    check("bare-репозиторий не найден",
          not any(p.startswith("archive.git/") for p in paths))
    check("у совпадения есть номер строки",
          all(isinstance(h.get("line"), int) for h in hits))

    regex_hits = file_tasks.search_files(root, r"Widget\s*\(", regex=True)
    check("regex-поиск работает", bool(regex_hits)
          and all("widget" in h["match"].lower() for h in regex_hits))
    limited = file_tasks.search_files(root, "Widget", max_results=2)
    check("max_results ограничивает выдачу", len(limited) == 2)

def test_plan_and_apply():
    section("3. JSON-план, diff, preview и атомарная запись")
    root = _make_project()
    old = file_tasks.read_file(root, "README.md")
    new = old + "\nДобавлен раздел о Widget.\n"
    diff = file_tasks.make_unified_diff("README.md", old, new)
    check("diff содержит удаление/добавление",
          "-" in diff and "+" in diff and "Добавлен раздел" in diff)

    raw = json.dumps({
        "summary": "Обновить README",
        "files": [{"path": "README.md", "action": "update",
                   "content": new}],
    })
    plan = file_tasks.parse_plan("```json\n%s\n```" % raw)
    check("JSON из блока разобран", plan["files"][0]["path"] == "README.md")

    preview = file_tasks.preview_plan(root, plan)
    check("preview ok", preview.get("ok") is True)
    check("preview не записал файл",
          file_tasks.read_file(root, "README.md") == old)
    check("preview содержит diff", "Добавлен раздел" in preview["diff"])
    check("preview applied=false", preview.get("applied") is False)

    applied = file_tasks.apply_plan(root, plan)
    check("apply ok", applied.get("ok") is True)
    check("apply записал файл",
          "Добавлен раздел" in file_tasks.read_file(root, "README.md"))
    check("apply вернул written", applied.get("written") == ["README.md"])

    create = {"summary": "ADR", "files": [{
        "path": "docs/ADR-001.md", "action": "create",
        "content": "# ADR 001\n\nРешение.\n"}]}
    result = file_tasks.apply_plan(root, create)
    check("create файла", result.get("ok") is True
          and os.path.isfile(os.path.join(root, "docs", "ADR-001.md")))
    check("create diff непустой", "# ADR 001" in result.get("diff", ""))

    bad_create = {"files": [{"path": "README.md", "action": "create",
                             "content": "x"}]}
    check("create существующего файла запрещён",
          file_tasks.preview_plan(root, bad_create).get("ok") is False)
    bad_update = {"files": [{"path": "docs/MISSING.md", "action": "update",
                             "content": "x"}]}
    check("update отсутствующего файла запрещён",
          file_tasks.preview_plan(root, bad_update).get("ok") is False)
    outside = {"files": [{"path": "../outside.md", "action": "create",
                          "content": "x"}]}
    check("запись вне root запрещена",
          file_tasks.preview_plan(root, outside).get("ok") is False)
    secret = {"files": [{"path": "apidpsk.txt", "action": "update",
                         "content": "x"}]}
    check("запись в секрет запрещена",
          file_tasks.preview_plan(root, secret).get("ok") is False)

    raised = False
    try:
        file_tasks.parse_plan("не json")
    except file_tasks.FileTaskError:
        raised = True
    check("невалидный JSON отклонён", raised)

def test_run_find():
    section("4. Сценарий поиска использований")
    root = _make_project()
    result = file_tasks.run_find(root, "Widget")
    check("find ok", result.get("ok") is True)
    check("find нашёл 3+ файла", len(result.get("files") or []) >= 3)
    check("find не использовал модель", result.get("model_used") is False)
    check("find input_files 3+", len(result.get("input_files") or []) >= 3)
    check("в тексте есть заголовок", "## Поиск" in result.get("text", ""))
    check("в тексте есть совпадения", "строка" in result.get("text", ""))
    empty = file_tasks.run_find(root, "NO_SUCH_TOKEN_XYZ")
    check("пустой поиск ok", empty.get("ok") is True)
    check("пустой поиск сообщает об этом",
          "Ничего не найдено" in empty.get("text", ""))

def _fake_plan(path, content, summary="изменено"):
    return {"content": json.dumps({
        "summary": summary,
        "files": [{"path": path, "action": "update", "content": content}],
    }), "prompt_tokens": 10, "completion_tokens": 5}

def test_update_docs():
    section("5. Обновление документации по git diff")
    root = _make_git_project()
    captured = {}

    def caller(messages):
        captured["messages"] = messages
        return _fake_plan("docs/GUIDE.md",
                          "# Guide\n\nWidget возвращает widget-v2.\n")

    result = file_tasks.run_update_docs(
        root, "HEAD~1", "HEAD", model="Fake", caller=caller,
        apply=False, use_rag=False, target_paths=["docs/GUIDE.md"])
    check("docs ok", result.get("ok") is True)
    check("docs scenario", result.get("scenario") == "docs")
    check("docs preview не записал файл",
          "widget-v2" not in file_tasks.read_file(root, "docs/GUIDE.md"))
    check("docs diff содержит изменение", "widget-v2" in result.get("diff", ""))
    check("docs usage посчитан", (result.get("usage") or {}).get("input") == 10)
    check("в промпт попал diff", "widget-v2" in captured["messages"][1]
          ["content"])
    check("в промпт попал документ", "# Guide" in captured["messages"][1]
          ["content"])
    inputs = result.get("input_files") or []
    check("docs input_files 2+", len(inputs) >= 2)
    check("docs input_files содержат код",
          any("widget.py" in p for p in inputs))
    check("docs input_files содержат документ", "docs/GUIDE.md" in inputs)
    check("docs text сообщает выбранные файлы",
          "Автоматически выбраны файлы" in result.get("text", ""))

    result = file_tasks.run_update_docs(
        root, "HEAD~1", "HEAD", model="Fake", caller=caller,
        apply=True, use_rag=False, target_paths=["docs/GUIDE.md"])
    check("docs apply записал файл",
          "widget-v2" in file_tasks.read_file(root, "docs/GUIDE.md"))
    check("docs applied=true", result.get("applied") is True)
    check("docs written", result.get("written") == ["docs/GUIDE.md"])

    outside = lambda messages: _fake_plan("README.md", "# Changed\n")
    result = file_tasks.run_update_docs(
        root, "HEAD~1", "HEAD", model="Fake", caller=outside,
        use_rag=False, target_paths=["docs/GUIDE.md"])
    check("docs запрещает файл вне target", result.get("ok") is False)
    check("docs причина указана", "вне разрешённых" in
          (result.get("error") or ""))

    bad = lambda messages: {"content": "не json", "prompt_tokens": 1,
                            "completion_tokens": 0}
    result = file_tasks.run_update_docs(
        root, "HEAD~1", "HEAD", model="Fake", caller=bad,
        use_rag=False, target_paths=["docs/GUIDE.md"])
    check("docs отклоняет невалидный JSON", result.get("ok") is False)

    result = file_tasks.run_update_docs(
        root, "HEAD", "HEAD", model="Fake", caller=caller, use_rag=False)
    check("docs без изменений — ошибка", result.get("ok") is False)

def test_generate_file():
    section("6. Генерация ADR/CHANGELOG")
    root = _make_git_project()
    target = "docs/ADR-test.md"

    def caller(messages):
        return _fake_plan(target, "# ADR\n\nИспользуем SQLite для RAG.\n")

    result = file_tasks.run_generate_file(
        root, kind="adr", topic="SQLite для RAG", model="Fake",
        caller=caller, apply=False, use_rag=False, path=target)
    check("new preview ok", result.get("ok") is True)
    check("new preview не создал файл",
          not os.path.isfile(os.path.join(root, target)))
    check("new diff содержит содержимое", "# ADR" in result.get("diff", ""))
    check("new target указан", result.get("target") == target)
    check("new input_files 2+", len(result.get("input_files") or []) >= 2)
    check("new text сообщает выбранные файлы",
          "Автоматически выбраны файлы" in result.get("text", ""))

    result = file_tasks.run_generate_file(
        root, kind="adr", topic="SQLite для RAG", model="Fake",
        caller=caller, apply=True, use_rag=False, path=target)
    check("new apply создал файл",
          os.path.isfile(os.path.join(root, target)))
    check("new content записан",
          "SQLite" in file_tasks.read_file(root, target))

    _write(os.path.join(root, "CHANGELOG.md"), "# Changelog\n")
    changelog = lambda messages: _fake_plan(
        "CHANGELOG.md", "# Changelog\n\n## Added\n- files task\n")
    result = file_tasks.run_generate_file(
        root, kind="changelog", topic="последние изменения", model="Fake",
        caller=changelog, apply=True, use_rag=False)
    check("changelog обновлён",
          "files task" in file_tasks.read_file(root, "CHANGELOG.md"))

    outside = lambda messages: _fake_plan(target, "x")
    result = file_tasks.run_generate_file(
        root, kind="adr", topic="x", model="Fake", caller=outside,
        use_rag=False, path="../outside.md")
    check("new запрещает путь вне root", result.get("ok") is False)

def test_edit_file():
    section("6b. Редактирование файла по инструкции")
    root = _make_project()
    old = file_tasks.read_file(root, "README.md")
    new = old + "\nДобавлено по инструкции.\n"
    captured = {}

    def caller(messages):
        captured["messages"] = messages
        return _fake_plan("README.md", new)

    result = file_tasks.run_edit_file(
        root, "README", "добавь строку про инструкцию", model="Fake",
        caller=caller, apply=False)
    check("edit preview ok", result.get("ok") is True)
    check("edit нашёл README.md", result.get("input_files") == ["README.md"])
    check("edit diff содержит изменение",
          "Добавлено по инструкции" in (result.get("diff") or ""))
    check("edit preview не записал",
          file_tasks.read_file(root, "README.md") == old)
    prompt = " ".join(m.get("content", "") for m in captured["messages"])
    check("в промпт попал текущий текст",
          "Widget" in prompt and "добавь строку" in prompt)

    result = file_tasks.run_edit_file(
        root, "README", "добавь строку про инструкцию", model="Fake",
        caller=caller, apply=True)
    check("edit apply ok", result.get("ok") is True
          and result.get("written") == ["README.md"])
    check("edit apply записал изменение",
          "Добавлено по инструкции" in file_tasks.read_file(root, "README.md"))

    settings = os.path.join(root, "settings.py")
    _write(settings, 'MODEL = "deepseek-chat"\n')

    def goal_caller(messages):
        return _fake_plan("settings.py", 'MODEL = "deepseek-reasoner"\n')

    result = file_tasks.run_edit_file(
        root, "", "замени MODEL на deepseek-reasoner", model="Fake",
        caller=goal_caller, apply=False)
    check("goal-level edit сам нашёл settings.py",
          result.get("ok") is True and result.get("input_files") == ["settings.py"])
    check("goal-level edit показал diff",
          "deepseek-reasoner" in (result.get("diff") or ""))
    check("goal-level edit не записал без apply",
          "deepseek-chat" in file_tasks.read_file(root, "settings.py"))

    raw_caller = lambda messages: {
        "content": "<<<FILE>>>\nMODEL = \"deepseek-green\"\n<<<END>>>",
        "prompt_tokens": 1, "completion_tokens": 1}
    result = file_tasks.run_edit_file(
        root, "settings.py", "замени MODEL на deepseek-green", model="Fake",
        caller=raw_caller, apply=False)
    check("edit принимает raw-содержимое без JSON",
          result.get("ok") is True
          and "deepseek-green" in (result.get("diff") or ""))

def test_mcp_server():
    section("7. MCP-сервер «Файлы проекта»")
    root = _make_project()
    check("ping отвечает", "files_server" in files_server.ping())

    payload = json.loads(files_server.list_files(root=root, ext=".py"))
    check("list_files ok", payload.get("ok") is True)
    check("list_files видит код", "app.py" in (payload.get("files") or []))
    check("list_files не видит секреты",
          "apidpsk.txt" not in (payload.get("files") or []))

    payload = json.loads(files_server.read_file("README.md", root=root))
    check("read_file ok", payload.get("ok") is True)
    check("read_file текст", "Widget" in (payload.get("text") or ""))

    payload = json.loads(files_server.read_file("README", root=root))
    check("MCP read_file без расширения ok",
          payload.get("ok") is True
          and payload.get("path") == "README.md"
          and "Widget" in (payload.get("text") or ""))

    old_readme = file_tasks.read_file(root, "README.md")
    new_readme = old_readme + "\nДобавлено через MCP.\n"
    payload = json.loads(files_server.preview_file_change(
        "README", new_readme, root=root))
    check("preview без расширения — update",
          payload.get("ok") is True and payload.get("applied") is False
          and "Добавлено через MCP" in (payload.get("diff") or ""))
    check("preview не записал README",
          file_tasks.read_file(root, "README.md") == old_readme)
    payload = json.loads(files_server.apply_file_change(
        "README", new_readme, confirm=True, root=root))
    check("apply без расширения записал README.md",
          payload.get("ok") is True
          and payload.get("written") == ["README.md"])
    check("изменение видно в файле",
          "Добавлено через MCP" in file_tasks.read_file(root, "README.md"))

    payload = json.loads(files_server.read_many_files(limit=3, root=root))
    check("read_many_files ok", payload.get("ok") is True)
    check("read_many_files прочитал 3 файла",
          payload.get("count") == 3 and len(payload.get("files") or []) == 3)
    check("read_many_files вернул текст",
          all((item.get("text") or "") for item in payload.get("files") or []))

    payload = json.loads(files_server.read_many_files(
        paths="README,app", root=root))
    check("read_many_files понимает имена без расширения",
          payload.get("ok") is True
          and {f.get("path") for f in payload.get("files") or []}
          == {"README.md", "app.py"})

    payload = json.loads(files_server.search_files("Widget", root=root,
                                                   max_results=5))
    check("search_files ok", payload.get("ok") is True)
    check("search_files нашёл", (payload.get("count") or 0) > 0)

    target = "docs/MCP-NEW.md"
    payload = json.loads(files_server.preview_file_change(
        target, "# MCP\n", root=root))
    check("preview_file_change ok", payload.get("ok") is True)
    check("preview_file_change не записал",
          not os.path.isfile(os.path.join(root, target)))

    payload = json.loads(files_server.apply_file_change(
        target, "# MCP\n", confirm=False, root=root))
    check("apply без confirm запрещён", payload.get("ok") is False)
    payload = json.loads(files_server.apply_file_change(
        target, "# MCP\n", confirm=True, root=root))
    check("apply с confirm записал", payload.get("ok") is True
          and os.path.isfile(os.path.join(root, target)))

    payload = json.loads(files_server.apply_file_change(
        "info.md", "", confirm=True, root=root))
    info_path = os.path.join(root, "info.md")
    check("apply создаёт пустой файл",
          payload.get("ok") is True and os.path.isfile(info_path)
          and os.path.getsize(info_path) == 0)

    payload = json.loads(files_server.file_info("README.md", root=root))
    check("file_info ok", payload.get("ok") is True
          and payload.get("size", 0) > 0)

def test_parse_files():
    section("8. Команда /files: разбор цели")
    spec = web_server.parse_files("/files find Widget")
    check("find scenario", spec["scenario"] == "find")
    check("find query", spec["query"] == "Widget")

    spec = web_server.parse_files(
        "/files docs HEAD~1 HEAD --apply --no-rag")
    check("docs scenario", spec["scenario"] == "docs")
    check("docs base/head", spec["base"] == "HEAD~1"
          and spec["head"] == "HEAD")
    check("docs apply", spec["apply"] is True)
    check("docs no-rag", spec["use_rag"] is False)

    spec = web_server.parse_files("/files new adr SQLite для RAG --apply")
    check("new scenario", spec["scenario"] == "new")
    check("new kind", spec["kind"] == "adr")
    check("new topic", "SQLite" in spec["topic"])
    check("new apply", spec["apply"] is True)

    spec = web_server.parse_files('/files new "333.md"')
    check("new explicit path", spec["scenario"] == "new"
          and spec["path"] == "333.md")

    spec = web_server.parse_files("/files создай ADR про SQLite")
    check("естественная цель new", spec["scenario"] == "new"
          and spec["kind"] == "adr")

    spec = web_server.parse_files(
        "/files обнови документацию по последним изменениям --apply")
    check("естественная цель docs", spec["scenario"] == "docs")
    check("docs default base", spec["base"] == "HEAD~1")
    check("docs default head", spec["head"] == "HEAD")

    spec = web_server.parse_files(
        "/files обнови документацию по изменениям HEAD~1..HEAD")
    check("естественная фраза не подменяет base/head",
          spec["base"] == "HEAD~1" and spec["head"] == "HEAD")

    spec = web_server.parse_files(
        "/files edit rtk_console.py замени MODEL на deepseek-reasoner --apply")
    check("edit scenario", spec["scenario"] == "edit")
    check("edit path", spec["path"] == "rtk_console.py")
    check("edit instruction", "MODEL" in spec["instruction"])
    check("edit apply", spec["apply"] is True)

    spec = web_server.parse_files(
        "/files измени в файле README.md добавь строку про MCP")
    check("естественная цель edit", spec["scenario"] == "edit")
    check("edit path из фразы", spec["path"] == "README.md")
    check("edit instruction из фразы", "добавь строку" in spec["instruction"])

    spec = web_server.parse_files(
        "/files edit замени MODEL на deepseek-reasoner")
    check("goal-level edit без пути", spec["scenario"] == "edit"
          and spec["path"] == "")
    check("goal-level edit instruction",
          "MODEL" in spec["instruction"] and "deepseek-reasoner"
          in spec["instruction"])

    check("естественный edit-запрос распознан",
          web_server._is_chat_edit_request(
              "добавить серый фон в поле ввода запроса"))
    check("обычный вопрос не считается edit",
          not web_server._is_chat_edit_request("что такое CSS?"))
    check("edit применяется, если не просили diff",
          web_server._chat_edit_apply("добавь серый фон")
          and not web_server._chat_edit_apply("сначала покажи diff"))
    check("естественное создание файла распознано",
          web_server._is_chat_create_request("создать файл info.md в корне")
          and web_server._chat_create_path("создать файл info.md в корне")
          == "info.md")

    spec = web_server.parse_files("/files")
    check("пустая команда — help", spec["scenario"] == "help")
    check("обычный текст — не команда",
          web_server.parse_files("просто вопрос") is None)

def test_chat_files_command():
    section("9. /files в чате: HTTP-интеграция")
    original_session = web_server._ServerState.session
    original_agent = web_server._ServerState.agent
    original_mcp = web_server._ServerState.mcp_enabled
    original_server = web_server._ServerState.mcp_server
    original_comp = web_server._ServerState.composition_running
    original_local = web_server._running_local_label
    original_find = web_server.file_tasks.run_find
    original_docs = web_server.file_tasks.run_update_docs
    original_edit = web_server.file_tasks.run_edit_file

    class FakeAgent(object):
        def answer(self, *args, **kwargs):
            return {"ok": True, "text": "ok"}

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.remove(tmp.name)
    web_server._ServerState.session = SessionStore(tmp.name)
    web_server._ServerState.agent = FakeAgent()
    web_server._ServerState.mcp_enabled = False
    web_server._ServerState.mcp_server = ""
    web_server._ServerState.composition_running = False
    web_server._running_local_label = lambda: ""

    def fake_find(root, query, **kwargs):
        return {"ok": True, "scenario": "find", "text": "## Поиск\n\nok",
                "files": ["a.py"], "matches": [], "total": 1,
                "error": None, "model_used": False}

    def fake_docs(root, **kwargs):
        return {"ok": True, "scenario": "docs", "text": "## Docs\n\nok",
                "files": [{"path": "docs/GUIDE.md", "action": "update",
                           "changed": True}],
                "changed": ["docs/GUIDE.md"], "diff": "+new",
                "applied": False, "written": [], "rag_used": False,
                "usage": {"input": 1, "output": 1}, "error": None}

    def fake_edit(root, path, instruction, **kwargs):
        return {"ok": True, "scenario": "edit",
                "text": "## Редактирование\\n\\nok",
                "files": [{"path": path, "action": "update",
                           "changed": True}],
                "input_files": [path], "changed": [path], "diff": "+new",
                "applied": bool(kwargs.get("apply")), "written": [],
                "rag_used": False, "usage": {"input": 1, "output": 1},
                "error": None}

    web_server.file_tasks.run_find = fake_find
    web_server.file_tasks.run_update_docs = fake_docs
    web_server.file_tasks.run_edit_file = fake_edit

    def run(question, models=None):
        captured = {}
        handler = web_server.WebRequestHandler.__new__(
            web_server.WebRequestHandler)
        payload = {"question": question}
        if models is not None:
            payload["models"] = models
        handler._read_json_body = lambda: payload
        handler._send_json = lambda code, body: captured.update(body)
        handler._handle_ask()
        return captured

    try:
        payload = run("/files find Widget")
        check("find без модели ok", payload.get("ok") is True)
        check("find files_task", payload.get("files_task") is True)
        check("find scenario", payload.get("scenario") == "find")
        check("find text", "Поиск" in (payload.get("text") or ""))

        payload = run("/files docs")
        check("docs без модели — ошибка", payload.get("ok") is False)
        check("docs без модели files_task", payload.get("files_task") is True)
        check("docs без модели подсказка",
              "Выберите модель" in (payload.get("text") or ""))

        payload = run("/files docs", models=[{"label": "Fake"}])
        check("docs с моделью ok", payload.get("ok") is True)
        check("docs с моделью applied=false",
              payload.get("applied") is False)
        check("docs с моделью answer label",
              (payload.get("answers") or [{}])[0].get("label") == "Fake")

        payload = run("/files edit README.md добавь строку --apply",
                      models=[{"label": "Fake"}])
        check("edit с моделью ok", payload.get("ok") is True)
        check("edit scenario", payload.get("scenario") == "edit")
        check("edit applied=true", payload.get("applied") is True)
        check("edit input_files", payload.get("input_files") == ["README.md"])
    finally:
        web_server._ServerState.session = original_session
        web_server._ServerState.agent = original_agent
        web_server._ServerState.mcp_enabled = original_mcp
        web_server._ServerState.mcp_server = original_server
        web_server._ServerState.composition_running = original_comp
        web_server._running_local_label = original_local
        web_server.file_tasks.run_find = original_find
        web_server.file_tasks.run_update_docs = original_docs
        web_server.file_tasks.run_edit_file = original_edit

def test_tool_defaults():
    section("11. Подстановка root для MCP «Файлы»")
    target = r"C:\work\project"
    check("files: read_file получает root",
          apply_tool_defaults("read_file", {}, {"root": target},
                              server_id="files") == {"root": target})
    check("files: list_files получает root",
          apply_tool_defaults("list_files", {"ext": ".py"},
                              {"root": target},
                              server_id="files")
          == {"ext": ".py", "root": target})
    check("project: list_files продолжает получать repo",
          apply_tool_defaults("list_files", {"ext": ".py"},
                              {"repo": target},
                              server_id="project")
          == {"ext": ".py", "repo": target})
    check("явный root имеет приоритет",
          apply_tool_defaults("read_file", {"root": r"G:\other"},
                              {"root": target}, server_id="files")["root"]
          == r"G:\other")

def main():
    ensure_utf8()
    test_sandbox()
    test_search()
    test_plan_and_apply()
    test_run_find()
    test_update_docs()
    test_generate_file()
    test_edit_file()
    test_mcp_server()
    test_parse_files()
    test_chat_files_command()
    test_tool_defaults()
    sys.exit(finish())

if __name__ == "__main__":
    main()

