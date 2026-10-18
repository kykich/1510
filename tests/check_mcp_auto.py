# -*- coding: utf-8 -*-
"""Проверка автовыбора MCP-сервера моделью (режим `auto`).

Покрывает без сети и без реальных MCP-процессов:
  1. Модель получает инструменты ВСЕХ серверов и выбирает сервер по запросу;
  2. git-запрос уходит в project, файловый — в files, тикет — в support;
  3. подстановка repo/root для выбранного сервера;
  4. защита от неверного/неоднозначного выбора сервера;
  5. режим auto в состоянии /api/mcp и проверке статуса;
  6. миграция старых настроек MCP в auto.

Запуск: python -m tests.check_mcp_auto
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import mcp_client  # noqa: E402
from rtk_app.agent import Agent  # noqa: E402
from web import server as web_server  # noqa: E402


SERVERS = [
    {"id": "project", "label": "Проект (git)", "tools": [
        {"name": "git_branch", "description": "ветка", "params": []},
        {"name": "git_status", "description": "статус", "params": []},
        {"name": "list_files", "description": "файлы git", "params": ["path"]},
    ]},
    {"id": "files", "label": "Файлы проекта", "tools": [
        {"name": "read_file", "description": "прочитать файл",
         "params": ["path"]},
        {"name": "read_many_files", "description": "прочитать несколько файлов",
         "params": ["paths", "limit"]},
        {"name": "search_files", "description": "поиск по файлам",
         "params": ["query"]},
        {"name": "list_files", "description": "файлы sandbox",
         "params": ["path"]},
    ]},
    {"id": "support", "label": "Поддержка", "tools": [
        {"name": "get_ticket", "description": "тикет", "params": ["ticket_id"]},
    ]},
]


class FakeAgent(Agent):
    """Агент с заранее заданным JSON-решением модели."""

    def __init__(self, decision, summary=None):
        super().__init__("", sources=[("deepseek", "test-model",
                                        "Fake", "fake")])
        self.decision = decision
        self.summary = summary
        self.prompts = []
        self.n = 0

    def _call_one(self, provider, model, messages, label, temperature=None,
                  max_tokens=None, **kwargs):
        self.prompts.append(messages)
        self.n += 1
        content = self.decision if self.n == 1 else (
            self.summary if self.summary is not None else self.decision)
        return {"ok": True, "content": content,
                "prompt_tokens": 10, "completion_tokens": 5,
                "elapsed": 0, "tps": 0, "provider": provider,
                "model": model, "error": None}


def _run(decision, question="вопрос", defaults=None, call_text=None,
         summary=None):
    """Выполняет auto-запрос с подменой mcp_call_tool; возвращает результат."""
    agent = FakeAgent(decision, summary=summary)
    calls = []

    def fake_call(name, arguments, timeout=None, server_id=None):
        calls.append((server_id, name, dict(arguments or {})))
        return {"ok": True, "text": call_text or "MCP-РЕЗУЛЬТАТ",
                "is_error": False, "error": None}

    old = mcp_client.mcp_call_tool
    mcp_client.mcp_call_tool = fake_call
    try:
        result = agent.answer_via_mcp_auto(question, SERVERS,
                                           tool_defaults=defaults)
    finally:
        mcp_client.mcp_call_tool = old
    return agent, result, calls


def test_git_selection():
    section("1. Git -> project")
    agent, result, calls = _run(
        '{"server": "project", "tool": "git_branch", "arguments": {}}',
        "Какая сейчас git-ветка?")
    check("ответ ok", result.get("ok") is True)
    check("выбран project", result.get("mcp", {}).get("server") == "project")
    check("вызван git_branch", calls and calls[0][1] == "git_branch")
    check("вызов ушёл в project", calls and calls[0][0] == "project")
    check("результат MCP в ответе", "MCP-РЕЗУЛЬТАТ" in result.get("text", ""))
    prompt = agent.prompts[0][0]["content"]
    check("в промпте есть project.git_branch", "project.git_branch" in prompt)
    check("в промпте есть files.read_file", "files.read_file" in prompt)
    check("в промпте есть support.get_ticket",
          "support.get_ticket" in prompt)
    check("в промпте есть правило про путь/файл",
          "ОБЯЗАТЕЛЬНО вызови files.read_file" in prompt)


def test_files_selection():
    section("2. Содержимое файла -> files + root")
    _agent, result, calls = _run(
        '{"server": "files", "tool": "read_file",'
        ' "arguments": {"path": "README.md"}}',
        "Что написано в README.md?",
        defaults={"repo": "R", "root": "SANDBOX"})
    check("ответ ok", result.get("ok") is True)
    check("выбран files", result.get("mcp", {}).get("server") == "files")
    check("вызван read_file", calls and calls[0][1] == "read_file")
    check("path передан моделью", calls and calls[0][2].get("path") == "README.md")
    check("root подставлен из defaults",
          calls and calls[0][2].get("root") == "SANDBOX")
    check("repo не подставлен файловому инструменту",
          calls and "repo" not in calls[0][2])


def test_file_result_formatting():
    section("2a. read_file -> форматированный ответ модели")
    raw = json.dumps({
        "ok": True,
        "path": "rtk_console.py",
        "text": 'MODEL = "deepseek-chat"\n',
        "size": 25,
        "error": None,
    })
    _agent, result, calls = _run(
        '{"server": "files", "tool": "read_file",'
        ' "arguments": {"path": "rtk_console"}}',
        "Какая модель используется в rtk_console?",
        call_text=raw,
        summary="## Модель\n\nИспользуется `deepseek-chat`.")
    check("ответ ok", result.get("ok") is True)
    check("в ответе краткий Markdown, а не JSON",
          "`deepseek-chat`" in (result.get("text") or "")
          and '"text":' not in (result.get("text") or ""))
    check("HTML отрендерен как Markdown",
          "mcp-answer-formatted" in (result.get("html") or "")
          and "<h3>" in (result.get("html") or ""))
    check("сырой результат сохранён в mcp.result",
          "rtk_console.py" in (result.get("mcp", {}).get("result") or ""))


def test_search_to_read_redirect():
    section("2b. search_files -> read_file для вопроса о содержимом")
    _agent, result, calls = _run(
        '{"server": "files", "tool": "search_files",'
        ' "arguments": {"query": "rtk_console"}}',
        "Какая модель используется в rtk_console?", defaults={"root": "SANDBOX"})
    check("ответ ok", result.get("ok") is True)
    check("инструмент заменён на read_file",
          calls and calls[0][1] == "read_file")
    check("read_file получил имя файла",
          calls and calls[0][2].get("path") == "rtk_console")
    check("root подставлен", calls and calls[0][2].get("root") == "SANDBOX")

    _agent, result, calls = _run(
        '{"server": "files", "tool": "search_files",'
        ' "arguments": {"query": "Widget"}}',
        "Где используется Widget?")
    check("обычный поиск остаётся search_files",
          calls and calls[0][1] == "search_files")


def test_list_to_read_many_redirect():
    section("2d. list_files -> read_many_files для чтения файлов")
    _agent, result, calls = _run(
        '{"server": "files", "tool": "list_files",'
        ' "arguments": {"limit": 100}}',
        "Прочитай файлы проекта", defaults={"root": "SANDBOX"})
    check("ответ ok", result.get("ok") is True)
    check("list_files заменён на read_many_files",
          calls and calls[0][1] == "read_many_files")
    check("root подставлен", calls and calls[0][2].get("root") == "SANDBOX")

    _agent, result, calls = _run(
        '{"server": "files", "tool": "list_files",'
        ' "arguments": {"limit": 10}}',
        "Покажи список файлов проекта")
    check("список файлов остаётся list_files",
          calls and calls[0][1] == "list_files")


def test_question_root_override():
    section("2c. Абсолютная папка из вопроса важнее RAG-default")
    root = tempfile.mkdtemp(prefix="mcp_qroot_")
    with open(os.path.join(root, "rtk_console.py"), "w",
              encoding="utf-8") as fh:
        fh.write('MODEL = "deepseek-chat"\n')
    question = ("Какая модель используется в rtk_console в " + root)
    _agent, result, calls = _run(
        '{"server": "files", "tool": "read_file",'
        ' "arguments": {"path": "rtk_console", "root": "WRONG"}}',
        question, defaults={"root": "DEFAULT"})
    check("ответ ok", result.get("ok") is True)
    check("root взят из вопроса",
          calls and calls[0][2].get("root") == os.path.abspath(root))
    check("путь файла сохранён",
          calls and calls[0][2].get("path") == "rtk_console")


def test_server_fallback():
    section("3. Сервер не указан/перепутан")
    _agent, result, calls = _run('{"tool": "git_status"}', "git status")
    check("сервер найден по уникальному инструменту",
          result.get("ok") is True and result.get("mcp", {}).get("server")
          == "project")
    check("вызов ушёл в project", calls and calls[0][0] == "project")

    _agent, result, calls = _run(
        '{"server": "project", "tool": "read_file",'
        ' "arguments": {"path": "a.txt"}}', "прочитай a.txt")
    check("неверный сервер исправлен на files",
          result.get("ok") is True and result.get("mcp", {}).get("server")
          == "files")
    check("read_file вызван в files", calls and calls[0][0] == "files")


def test_ambiguous_and_direct():
    section("4. Неоднозначность и прямой ответ")
    _agent, result, calls = _run('{"tool": "list_files"}', "покажи файлы")
    check("list_files без сервера уходит в files",
          result.get("ok") is True and calls and calls[0][0] == "files")

    _agent, result, calls = _run('{"tool": "unknown_tool"}', "что-то")
    check("неизвестный инструмент не вызывается", not calls)
    check("возвращена ошибка", result.get("ok") is False)
    check("ошибка про сервер", "сервер" in (result.get("error") or ""))

    _agent, result, calls = _run(
        '{"server": "", "tool": null, "answer": "Без MCP"}', "привет")
    check("прямой ответ ok", result.get("ok") is True)
    check("инструмент не вызывался", not calls)
    check("mcp.tool = None", result.get("mcp", {}).get("tool") is None)


def test_web_auto_state():
    section("5. Режим auto в /api/mcp")
    fake_servers = [{"id": "project", "label": "Проект", "args": []},
                    {"id": "files", "label": "Файлы", "args": []}]

    class FakeMcp(object):
        @staticmethod
        def mcp_servers():
            return list(fake_servers)

        @staticmethod
        def mcp_status(server_id=None):
            return {"ok": True, "connected": True, "tools_count": 2,
                    "tools": ["one", "two"], "server": server_id,
                    "error": None}

    old_client = web_server.mcp_client
    old_server = web_server._ServerState.mcp_server
    old_status = web_server._ServerState.mcp_status
    web_server.mcp_client = FakeMcp
    web_server._ServerState.mcp_server = "auto"
    web_server._ServerState.mcp_status = None
    try:
        payload = web_server._mcp_state_payload()
        ids = [s.get("id") for s in payload.get("servers", [])]
        check("auto есть в списке серверов", "auto" in ids)
        check("auto стоит первым", ids and ids[0] == "auto")
        check("выбранный сервер = auto", payload.get("server") == "auto")
        status = web_server._probe_mcp_status()
        check("статус auto ok", status.get("ok") is True)
        check("статус auto перечисляет серверы",
              "project" in (status.get("server") or "")
              and "files" in (status.get("server") or ""))
        check("инструменты с префиксами",
              "project.one" in (status.get("tools") or [])
              and "files.two" in (status.get("tools") or []))
    finally:
        web_server.mcp_client = old_client
        web_server._ServerState.mcp_server = old_server
        web_server._ServerState.mcp_status = old_status


def test_legacy_settings_migrate():
    section("6. Старые настройки -> auto")
    fd, path = tempfile.mkstemp(prefix="mcp_settings_", suffix=".json")
    os.close(fd)
    old_file = web_server.config.MCP_SETTINGS_FILE
    old_server = web_server._ServerState.mcp_server
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "server": "files"}, fh)
        web_server.config.MCP_SETTINGS_FILE = path
        web_server._ServerState.mcp_server = "files"
        web_server._load_mcp_settings()
        check("старый файл без routing -> auto",
              web_server._ServerState.mcp_server == "auto")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "routing": "server",
                       "server": "files"}, fh)
        web_server._load_mcp_settings()
        check("routing=server сохраняет files",
              web_server._ServerState.mcp_server == "files")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "routing": "other",
                       "server": "files"}, fh)
        web_server._load_mcp_settings()
        check("неизвестный routing -> auto",
              web_server._ServerState.mcp_server == "auto")
    finally:
        web_server.config.MCP_SETTINGS_FILE = old_file
        web_server._ServerState.mcp_server = old_server
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    ensure_utf8()
    test_git_selection()
    test_files_selection()
    test_file_result_formatting()
    test_search_to_read_redirect()
    test_list_to_read_many_redirect()
    test_question_root_override()
    test_server_fallback()
    test_ambiguous_and_direct()
    test_web_auto_state()
    test_legacy_settings_migrate()
    return finish("check_mcp_auto")


if __name__ == "__main__":
    sys.exit(main())
