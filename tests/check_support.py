# -*- coding: utf-8 -*-
"""Проверка мини-сервиса поддержки (docs/Final_task.md, задача 3) — офлайн.

Покрывает:
  1. JSON-данные поддержки: пользователи, тикеты, поиск и сортировка;
  2. форматирование контекста «тикет + пользователь» и запрос к FAQ;
  3. resolve_context: тикет, пользователь, ошибки, общий вопрос;
  4. MCP-сервер «Поддержка» (support_server) — чтение тикетов/пользователей;
  5. команду /support: разбор аргументов, MCP -> fallback JSON, RAG по FAQ;
  6. интеграцию с агентом и HTTP-обработчиком (/support в чате).

Запуск: python -m tests.check_support
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import check, section, finish, ensure_utf8  # noqa: E402
from rtk_app import config, rag, support  # noqa: E402
from rtk_app.agent import Agent  # noqa: E402
from rtk_app.session_store import SessionStore  # noqa: E402
import support_index  # noqa: E402
import support_server  # noqa: E402
import web.server as web_server  # noqa: E402


def test_data_and_search():
    section("1. JSON-данные: пользователи и тикеты")
    data = support.load_data()
    check("пользователи загружены", len(data["users"]) == 3,
          "%r" % data["users"])
    check("тикеты загружены", len(data["tickets"]) == 4,
          "%r" % data["tickets"])
    check("каталог данных указан", os.path.isdir(data["dir"]))

    user = support.find_user("anna@example.com", data=data)
    check("поиск по email", bool(user) and user["id"] == "u-1001")
    check("поиск по id", support.find_user("u-1002", data=data)["name"]
          == "Иван Соколов")
    check("поиск по телефону",
          support.find_user("+7 999 100-10-03", data=data)["id"] == "u-1003")
    check("частичное совпадение по имени",
          support.find_user("Петров", data=data)["id"] == "u-1001")
    check("неизвестный пользователь", support.find_user("nobody") is None)

    ticket = support.find_ticket("T-1001", data=data)
    check("тикет по T-1001", bool(ticket) and ticket["id"] == "T-1001")
    check("пользователь прикреплён", ticket["user"]["name"] == "Анна Петрова")
    check("тикет по #T-1001",
          support.find_ticket("#T-1001", data=data)["id"] == "T-1001")
    check("тикет по числу 1001",
          support.find_ticket("1001", data=data)["id"] == "T-1001")
    check("неизвестный тикет", support.find_ticket("T-9999") is None)

    opened = support.list_tickets(user_id="u-1001", status="open", data=data)
    check("открытые тикеты пользователя", len(opened) == 1
          and opened[0]["id"] == "T-1001")
    all_user = support.list_tickets(user_id="u-1001", data=data)
    check("все тикеты пользователя", len(all_user) == 2)
    check("новые тикеты сверху",
          all_user[0]["updated_at"] >= all_user[1]["updated_at"])
    check("latest_ticket — последний открытый",
          support.latest_ticket("u-1001", status="open",
                                data=data)["id"] == "T-1001")


def test_format_context():
    section("2. Контекст «тикет + пользователь» и запрос к FAQ")
    ticket = support.find_ticket("T-1001")
    user = ticket["user"]

    card = support.format_ticket(ticket)
    check("тема тикета в карточке", "Не работает авторизация" in card)
    check("код ошибки в карточке", "401" in card)
    check("шаги пользователя в карточке", "вышла из аккаунта" in card)
    check("история обращения в карточке", "История обращения" in card)

    user_card = support.format_user(user)
    check("имя пользователя", "Анна Петрова" in user_card)
    check("тариф пользователя", "pro" in user_card)
    check("2FA отмечена", "2FA: включена" in user_card)

    context = support.build_context(ticket, user)
    check("в контексте есть тикет", "ТИКЕТ ПОДДЕРЖКИ" in context)
    check("в контексте есть пользователь", "ПОЛЬЗОВАТЕЛЬ" in context)
    check("без тикета и пользователя контекст пуст",
          support.build_context(None, None) == "")
    only_user = support.build_context(None, user)
    check("без тикета остаётся карточка пользователя",
          "ПОЛЬЗОВАТЕЛЬ" in only_user and "ТИКЕТ ПОДДЕРЖКИ" not in only_user)

    query = support.build_rag_query("Почему не пускает?", ticket, user)
    check("в RAG-запросе код ошибки", "401" in query)
    check("в RAG-запросе теги", "auth" in query)
    check("в RAG-запросе тариф", "pro" in query)
    check("длина RAG-запроса ограничена",
          len(support.build_rag_query("x" * 2000)) <= 800)

    system = support.build_support_system_context(ticket, user,
                                                  extra="FAQ CONTEXT")
    check("инструкция поддержки в системном блоке",
          "ассистент поддержки" in system)
    check("данные тикета в системном блоке", "Не работает авторизация" in system)
    check("дополнительный блок добавлен", "FAQ CONTEXT" in system)


def test_resolve_context():
    section("3. resolve_context: тикет, пользователь, ошибки")
    ctx = support.resolve_context(ticket_id="T-1001", query="Почему 401?")
    check("тикет найден", ctx["ok"] is True and ctx["ticket"]["id"] == "T-1001")
    check("пользователь получен из тикета", ctx["user"]["id"] == "u-1001")
    check("текст контекста собран", "ТИКЕТ ПОДДЕРЖКИ" in ctx["text"])
    check("rag_query содержит вопрос", "Почему 401?" in ctx["rag_query"])

    ctx = support.resolve_context(user_id="anna@example.com")
    check("пользователь найден", ctx["ok"] is True
          and ctx["user"]["id"] == "u-1001")
    check("взят последний открытый тикет", ctx["ticket"]["id"] == "T-1001")

    ctx = support.resolve_context(ticket_id="T-9999")
    check("неизвестный тикет — ошибка", ctx["ok"] is False)
    check("причина ошибки указана", "не найден" in ctx["error"])
    ctx = support.resolve_context(user_id="nobody@example.com")
    check("неизвестный пользователь — ошибка", ctx["ok"] is False)
    ctx = support.resolve_context()
    check("без идентификаторов — ошибка", ctx["ok"] is False)
    ctx = support.resolve_context(allow_empty=True)
    check("allow_empty разрешает общий вопрос", ctx["ok"] is True
          and ctx["text"] == "")


def test_support_server():
    section("4. MCP-сервер «Поддержка»")
    check("ping отвечает", "support_server" in support_server.ping())

    payload = json.loads(support_server.find_user("maria@example.com"))
    check("find_user: ok", payload.get("ok") is True)
    check("find_user: пользователь", (payload.get("users") or [{}])[0].get("id")
          == "u-1003")

    payload = json.loads(support_server.get_ticket("1001"))
    check("get_ticket: ok", payload.get("ok") is True)
    check("get_ticket: пользователь", (payload.get("ticket") or {})
          .get("user", {}).get("id") == "u-1001")

    payload = json.loads(support_server.list_tickets(user_id="u-1001"))
    check("list_tickets: ok", payload.get("ok") is True)
    check("list_tickets: два обращения", len(payload.get("tickets") or []) == 2)

    payload = json.loads(support_server.get_support_context(
        ticket_id="T-1001", query="Почему не работает авторизация?"))
    check("get_support_context: ok", payload.get("ok") is True)
    check("get_support_context: текст для промпта",
          "ТИКЕТ ПОДДЕРЖКИ" in (payload.get("text") or ""))
    check("get_support_context: rag_query",
          "401" in (payload.get("rag_query") or ""))

    payload = json.loads(support_server.get_support_context(ticket_id="T-9999"))
    check("get_support_context: тикет не найден", payload.get("ok") is False)
    payload = json.loads(support_server.get_support_context())
    check("get_support_context: нужен идентификатор",
          payload.get("ok") is False)

    payload = json.loads(support_server.stats())
    check("stats: пользователи", payload.get("users") == 3)
    check("stats: тикеты", payload.get("tickets") == 4)


def test_support_index():
    section("5. Индексация FAQ в RAG")
    captured = {}

    class FakeStore(object):
        def index_docs(self, docs_dir=None, verbose=True):
            captured["docs_dir"] = docs_dir
            captured["verbose"] = verbose
            return {"ok": True, "files": 2, "chunks": 9, "errors": []}

    original = support_index.RagStore
    support_index.RagStore = lambda: FakeStore()
    try:
        report = support_index.build_index(verbose=False)
    finally:
        support_index.RagStore = original
    check("найден каталог docs/support",
          os.path.normcase(captured.get("docs_dir") or "")
          == os.path.normcase(support.docs_dir()))
    check("отчёт индексатора получен", report.get("files") == 2
          and report.get("chunks") == 9)
    check("support_data исключён из общего RAG",
          "support_data" in config.RAG_DOC_SKIP_DIRS)


def test_support_rag_scoping():
    section("6. RAG ищет только по FAQ поддержки")
    root = tempfile.mkdtemp(prefix="support_rag_")
    support_dir = os.path.join(root, "docs", "support")
    other_dir = os.path.join(root, "docs", "other")
    os.makedirs(support_dir)
    os.makedirs(other_dir)
    faq = os.path.join(support_dir, "FAQ.md")
    other = os.path.join(other_dir, "notes.md")
    store = rag.RagStore(index_file=os.path.join(root, "index.db"),
                         settings_file=os.path.join(root, "rag.json"))

    def fake_embed(text):
        dim = rag.config.RAG_EMBED_DIM
        vector = [0.0] * dim
        for ch in str(text).lower():
            vector[ord(ch) % dim] += 1.0
        return vector

    text = "После смены пароля возникает ошибка 401: очистите cookies."
    original = rag.embed_text
    rag.embed_text = fake_embed
    try:
        conn = store._conn()
        rows = [
            (1, faq, 0, 1, text, rag._pack_vector(fake_embed(text))),
            (2, other, 0, 1, text, rag._pack_vector(fake_embed(text))),
        ]
        conn.executemany(
            "INSERT INTO chunks (id, file, page, chunk, text, vec) "
            "VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
        store.update_settings(hybrid=True, top_k=5, min_score=0.0)
        context, hits = web_server.support_rag_context(
            store, "ошибка 401 после смены пароля", top_k=5,
            docs_dir=support_dir)
    finally:
        rag.embed_text = original
    check("FAQ-контекст найден", bool(context) and "cookies" in context)
    check("чужой файл не попал в контекст", "notes.md" not in context)
    check("найден только FAQ", len(hits) == 1
          and os.path.basename(hits[0]["file"]) == "FAQ.md")
    sources = rag.format_sources(hits, docs_dir=support_dir)
    check("источник для карточки — FAQ.md",
          sources and sources[0]["path"] == "FAQ.md")


def test_parse_support():
    section("7. Команда /support: разбор аргументов")
    spec = web_server.parse_support(
        "/support T-1001 Почему не работает авторизация?")
    check("тикет из первого токена", spec["ticket_id"] == "T-1001")
    check("вопрос отделён от тикета",
          spec["query"] == "Почему не работает авторизация?")
    check("RAG включён по умолчанию", spec["use_rag"] is True)

    spec = web_server.parse_support(
        "/support ticket=1004 user=maria@example.com --no-rag код не подходит")
    check("ticket= разобран", spec["ticket_id"] == "1004")
    check("user= разобран", spec["user_id"] == "maria@example.com")
    check("--no-rag отключает RAG", spec["use_rag"] is False)
    check("текст вопроса сохранён", "код не подходит" in spec["query"])

    spec = web_server.parse_support("/support user=anna@example.com 401")
    check("только пользователь", spec["ticket_id"] == ""
          and spec["user_id"] == "anna@example.com")
    check("обычный текст — не команда",
          web_server.parse_support("почему 401?") is None)


def test_support_lookup():
    section("8. /support: MCP «Поддержка» и fallback на JSON")
    original = web_server.mcp_client
    calls = {}

    class FakeMcp(object):
        @staticmethod
        def mcp_call_tool(name, arguments, timeout=None, server_id=None):
            calls.update({"name": name, "arguments": arguments,
                          "server_id": server_id})
            return {"ok": True, "error": None, "text": json.dumps({
                "ok": True, "ticket": {"id": "T-1001"}, "user": {"id": "u-1001"},
                "text": "КОНТЕКСТ ИЗ MCP", "rag_query": "401",
                "error": None})}

    web_server.mcp_client = FakeMcp
    try:
        result = web_server.support_lookup("T-1001", "", "Почему 401?")
    finally:
        web_server.mcp_client = original
    check("MCP-инструмент вызван",
          calls.get("name") == "get_support_context")
    check("MCP-сервер поддержки выбран",
          calls.get("server_id") == config.SUPPORT_SERVER_ID)
    check("аргументы переданы",
          calls.get("arguments", {}).get("ticket_id") == "T-1001")
    check("источник — MCP", result.get("source") == "mcp")
    check("контекст из MCP", result.get("text") == "КОНТЕКСТ ИЗ MCP")

    class BrokenMcp(object):
        @staticmethod
        def mcp_call_tool(*args, **kwargs):
            raise RuntimeError("MCP недоступен")

    web_server.mcp_client = BrokenMcp
    try:
        result = web_server.support_lookup("T-1001")
    finally:
        web_server.mcp_client = original
    check("при сбое MCP — fallback на JSON", result.get("source") == "json")
    check("fallback нашёл тикет", result.get("ok") is True
          and (result.get("ticket") or {}).get("id") == "T-1001")

    result = web_server.support_lookup("", "", "Как сбросить пароль?")
    check("общий вопрос без обращения", result.get("ok") is True
          and result.get("source") == "none")
    check("RAG-запрос для общего вопроса",
          "сбросить пароль" in (result.get("rag_query") or ""))

    web_server.mcp_client = None
    try:
        result = web_server.support_lookup("T-9999")
    finally:
        web_server.mcp_client = original
    check("неизвестный тикет — ошибка", result.get("ok") is False)
    check("причина указана", "не найден" in (result.get("error") or ""))


def test_agent_support_prompt():
    section("9. Агент получает инструкцию и контекст поддержки")
    ticket = support.find_ticket("T-1001")
    block = support.build_support_system_context(ticket, ticket["user"])
    messages = Agent("")._build_messages([], "Почему 401?",
                                         support_context=block)
    system = messages[0]["content"]
    check("системный промпт поддержки добавлен",
          "ассистент поддержки" in system)
    check("тикет попал в системный промпт", "Не работает авторизация" in system)
    check("пользователь попал в системный промпт", "Анна Петрова" in system)
    check("вопрос остался в user-сообщении",
          messages[-1]["role"] == "user" and messages[-1]["content"]
          == "Почему 401?")


def test_chat_support_command():
    section("10. /support в чате: контекст, RAG, ответ")
    original_agent = web_server._ServerState.agent
    original_session = web_server._ServerState.session
    original_mcp = web_server.mcp_client
    original_enabled = web_server._ServerState.mcp_enabled
    original_server = web_server._ServerState.mcp_server
    original_composition = web_server._ServerState.composition_running
    original_reminders = web_server._collect_due_reminders
    original_local = web_server._running_local_label
    original_rag = web_server.support_rag_context

    class FakeAgent(object):
        last = {}

        def answer(self, question, history=None, selected=None, **kwargs):
            FakeAgent.last = {"question": question, "history": history,
                              "selected": selected, "kwargs": kwargs}
            return {"ok": True, "text": "ответ поддержки", "html": "",
                    "answers": [], "meta": "", "usage": {}}

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.remove(tmp.name)
    session = SessionStore(tmp.name)
    web_server._ServerState.agent = FakeAgent()
    web_server._ServerState.session = session
    web_server._ServerState.mcp_enabled = False
    web_server._ServerState.mcp_server = ""
    web_server._ServerState.composition_running = False
    web_server.mcp_client = None          # без запуска MCP-подпроцесса
    web_server._collect_due_reminders = lambda: []
    web_server._running_local_label = lambda: ""

    class FakeRagStore(object):
        query = ""
        docs_dir = ""

        @staticmethod
        def build_context(query, top_k=None, docs_dir=None):
            FakeRagStore.query = query
            FakeRagStore.docs_dir = docs_dir
            hit = {"file": os.path.join(support.docs_dir(), "FAQ.md"),
                   "page": 0, "chunk": 1, "score": 0.9,
                   "text": "Очистите cookies и войдите заново."}
            return "[фрагмент: FAQ.md]\nОчистите cookies и войдите заново.", [hit]

        @staticmethod
        def status():
            return {"available": True}

    web_server.support_rag_context = (
        lambda rag_obj, query, top_k=None, docs_dir=None:
        FakeRagStore.build_context(query, top_k=top_k, docs_dir=docs_dir))

    def run(question):
        captured = {}
        handler = web_server.WebRequestHandler.__new__(
            web_server.WebRequestHandler)
        handler._read_json_body = lambda: {
            "question": question, "models": [{"label": "Fake"}]}
        handler._send_json = lambda code, payload: captured.update(payload)
        handler._handle_ask()
        return captured

    try:
        payload = run("/support T-1001 Почему не работает авторизация?")
        check("ответ ok", payload.get("ok") is True)
        check("признак support", payload.get("support") is True)
        check("источник данных — JSON", payload.get("support_source") == "json")
        check("тикет в ответе", payload.get("ticket_id") == "T-1001")
        check("RAG использован", payload.get("rag_used") is True)
        check("RAG искал по каталогу FAQ",
              os.path.normcase(FakeRagStore.docs_dir or "")
              == os.path.normcase(support.docs_dir()))
        check("запрос к RAG содержит ошибку 401",
              "401" in FakeRagStore.query)
        check("RAG-контекст передан агенту",
              "cookies" in (FakeAgent.last["kwargs"].get("rag_context") or ""))
        support_block = FakeAgent.last["kwargs"].get("support_context") or ""
        check("контекст поддержки передан агенту",
              "ТИКЕТ ПОДДЕРЖКИ" in support_block)
        check("в контексте поддержки есть история",
              "История обращения" in support_block)
        check("вопрос для модели — текст после /support",
              FakeAgent.last["question"]
              == "Почему не работает авторизация?")

        payload = run("/support T-9999 что делать?")
        check("неизвестный тикет — ok=false", payload.get("ok") is False)
        check("ошибка помечена как support", payload.get("support") is True)
        check("причина в тексте карточки",
              "тикет" in (payload.get("text") or "").lower())
    finally:
        web_server._ServerState.agent = original_agent
        web_server._ServerState.session = original_session
        web_server.mcp_client = original_mcp
        web_server._ServerState.mcp_enabled = original_enabled
        web_server._ServerState.mcp_server = original_server
        web_server._ServerState.composition_running = original_composition
        web_server._collect_due_reminders = original_reminders
        web_server._running_local_label = original_local
        web_server.support_rag_context = original_rag


def test_support_data_not_served():
    section("11. Данные поддержки не отдаются через /docs/")
    root = os.path.dirname(support.data_dir())

    class FakeSession(object):
        @staticmethod
        def rag_settings():
            return {"docs_dir": root}

    class FakeHandler(object):
        session = FakeSession()
        status = None

        def _send_bytes(self, code, body, ctype):
            FakeHandler.status = code
            return code

    rel = os.path.join(os.path.basename(support.data_dir()), "users.json")
    code = web_server.WebRequestHandler._serve_rag_doc(FakeHandler(), rel)
    check("users.json закрыт (403)", code == 403 or FakeHandler.status == 403)


def main():
    ensure_utf8()
    test_data_and_search()
    test_format_context()
    test_resolve_context()
    test_support_server()
    test_support_index()
    test_support_rag_scoping()
    test_parse_support()
    test_support_lookup()
    test_agent_support_prompt()
    test_chat_support_command()
    test_support_data_not_served()
    sys.exit(finish())


if __name__ == "__main__":
    main()




