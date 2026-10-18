# -*- coding: utf-8 -*-
"""MCP-сервер «Поддержка» (docs/Final_task.md, задача 3).

Читает JSON с пользователями и тикетами и отдаёт контекст для ассистента
поддержки. Все инструменты ТОЛЬКО читают данные; записи в CRM/JSON нет.

Инструменты:
  * ping()                       — проверка живости;
  * find_user(query)             — поиск по id/email/имени/телефону;
  * get_ticket(ticket_id)        — тикет + пользователь;
  * list_tickets(user_id, status, limit) — обращения пользователя;
  * get_support_context(ticket_id, user_id, query) — тикет/пользователь +
                                   готовый текстовый контекст для LLM;
  * stats()                      — количество пользователей/тикетов.

Запуск (stdio, как подпроцесс MCP-клиента):
    python support_server.py
"""
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rtk_app import support as support_mod  # noqa: E402

mcp = MCPServer("support")


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _error(message):
    return _json({"ok": False, "error": str(message or "ошибка поддержки")})


def _user_payload(user):
    return dict(user or {})


def _ticket_payload(ticket):
    return dict(ticket or {})


@mcp.tool()
def ping() -> str:
    """Проверка живости MCP-сервера поддержки."""
    return "pong: support_server (%s)" % support_mod.data_dir()


@mcp.tool()
def find_user(query: str) -> str:
    """Найти пользователя по id, email, имени или телефону.

    query — строка поиска (например, anna@example.com).
    Возвращает JSON {ok, users:[...], error}.
    """
    try:
        data = support_mod.load_data()
        users = support_mod.find_users(query, data=data, limit=10)
        return _json({"ok": True, "users": [_user_payload(u) for u in users],
                      "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _error(exc)


@mcp.tool()
def get_ticket(ticket_id: str) -> str:
    """Получить тикет по ID (T-1001, #T-1001 или 1001) вместе с пользователем.

    Возвращает JSON {ok, ticket, error}; ticket.user — карточка пользователя.
    """
    try:
        data = support_mod.load_data()
        ticket = support_mod.find_ticket(ticket_id, data=data)
        if not ticket:
            return _json({"ok": False, "ticket": None,
                          "error": "тикет %r не найден" % ticket_id})
        return _json({"ok": True, "ticket": _ticket_payload(ticket),
                      "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _error(exc)


@mcp.tool()
def list_tickets(user_id: str = "", status: str = "", limit: int = 10) -> str:
    """Список тикетов пользователя (user_id) и/или по статусу.

    Возвращает JSON {ok, tickets:[...], error}.
    """
    try:
        data = support_mod.load_data()
        uid = str(user_id or "").strip()
        if uid:
            user = support_mod.find_user(uid, data=data)
            if user:
                uid = user.get("id") or uid
        tickets = support_mod.list_tickets(user_id=uid or None,
                                           status=status or None,
                                           limit=limit, data=data)
        return _json({"ok": True,
                      "tickets": [_ticket_payload(t) for t in tickets],
                      "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _error(exc)


@mcp.tool()
def get_support_context(ticket_id: str = "", user_id: str = "",
                        query: str = "") -> str:
    """Собрать контекст поддержки для ответа модели.

    ticket_id — ID тикета (T-1001); user_id — id/email пользователя.
    Если передан только user_id, берётся последний открытый тикет.
    Возвращает JSON {ok, ticket, user, text, rag_query, error}; text — готовый
    блок «тикет + пользователь» для системного промпта.
    """
    try:
        context = support_mod.resolve_context(
            ticket_id=ticket_id, user_id=user_id, query=query)
        if not context.get("ok"):
            return _json({"ok": False, "ticket": None, "user": None,
                          "text": "",
                          "error": context.get("error") or "обращение "
                                                          "не найдено"})
        return _json({"ok": True,
                      "ticket": _ticket_payload(context.get("ticket")),
                      "user": _user_payload(context.get("user")),
                      "text": context.get("text") or "",
                      "rag_query": context.get("rag_query") or "",
                      "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _error(exc)


@mcp.tool()
def stats() -> str:
    """Количество пользователей и тикетов в JSON-хранилище."""
    try:
        data = support_mod.load_data()
        return _json({"ok": True, "users": len(data.get("users") or []),
                      "tickets": len(data.get("tickets") or []),
                      "dir": data.get("dir"), "error": None})
    except Exception as exc:                     # noqa: BLE001
        return _error(exc)


if __name__ == "__main__":
    # stdio — транспорт, который использует rtk_app.mcp_client.
    mcp.run()

