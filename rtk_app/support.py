# -*- coding: utf-8 -*-
"""Поддержка пользователей (docs/Final_task.md, задача 3).

Слой данных и форматирования контекста для MCP-сервера `support_server.py`
и команды `/support`:

  * JSON-пользователи и тикеты (`SUPPORT_DATA_DIR`, по умолчанию support_data/);
  * поиск пользователя по id/email/имени/телефону и тикета по id;
  * единый текстовый контекст «тикет + пользователь + история» для промпта;
  * запрос к RAG по FAQ (docs/support).

Модуль не зависит от HTTP/MCP — его можно использовать в тестах напрямую.
"""
import json
import os

from . import config

__all__ = [
    "SUPPORT_SYSTEM_PROMPT", "SupportDataError", "load_data", "find_users",
    "find_user", "find_ticket", "list_tickets", "latest_ticket",
    "build_rag_query", "format_ticket", "format_user", "build_context",
    "build_support_system_context", "resolve_context",
]


SUPPORT_SYSTEM_PROMPT = """Ты — ассистент поддержки продукта DeepSeek Chat.
Отвечай пользователю по-русски, вежливо и по делу.

Правила:
- используй ТОЛЬКО данные тикета, пользователя и найденные фрагменты FAQ;
- не выдумывай факты, которых нет в контексте;
- не запрашивай и не повторяй пароли, коды 2FA и другие секреты;
- если данных недостаточно — скажи об этом и предложи эскалацию;
- учитывай, что пользователь уже мог выполнить часть шагов из тикета.

Структура ответа (Markdown):
## Краткий ответ
Одно-два предложения по существу.

## Почему так
Объяснение причины, привязанное к тикету/FAQ.

## Что сделать
Нумерованные шаги, которые пользователь может выполнить сам.

## Когда эскалировать
Признаки, при которых нужно передать тикет второй линии."""


class SupportDataError(ValueError):
    """Ошибка чтения/структуры данных поддержки."""


def data_dir(path=None):
    """Каталог данных поддержки (env SUPPORT_DATA_DIR имеет приоритет)."""
    if path:
        return os.path.abspath(str(path))
    return os.path.abspath(os.environ.get("SUPPORT_DATA_DIR")
                           or getattr(config, "SUPPORT_DATA_DIR",
                                      "support_data"))


def docs_dir(path=None):
    """Каталог FAQ/документации поддержки для RAG."""
    if path:
        return os.path.abspath(str(path))
    return os.path.abspath(os.environ.get("SUPPORT_DOCS_DIR")
                           or getattr(config, "SUPPORT_DOCS_DIR",
                                      os.path.join("docs", "support")))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SupportDataError("файл данных не найден: %s" % path)
    except Exception as exc:                     # noqa: BLE001
        raise SupportDataError("не удалось прочитать %s: %s" % (path, exc))


def load_data(path=None):
    """Читает users.json и tickets.json; возвращает {users, tickets, dir}."""
    root = data_dir(path)
    users_doc = _read_json(os.path.join(root, "users.json"))
    tickets_doc = _read_json(os.path.join(root, "tickets.json"))
    users = users_doc.get("users") if isinstance(users_doc, dict) else users_doc
    tickets = (tickets_doc.get("tickets") if isinstance(tickets_doc, dict)
               else tickets_doc)
    if not isinstance(users, list) or not isinstance(tickets, list):
        raise SupportDataError(
            "users.json и tickets.json должны содержать массивы users/tickets")
    clean_users = [u for u in users if isinstance(u, dict) and u.get("id")]
    clean_tickets = [t for t in tickets if isinstance(t, dict) and t.get("id")]
    return {"users": clean_users, "tickets": clean_tickets, "dir": root}


def _norm(value):
    return str(value or "").strip().lower()


def _find_user_in(users, query):
    q = _norm(query)
    if not q:
        return []
    exact, partial = [], []
    for user in users:
        values = [_norm(user.get(k)) for k in
                  ("id", "email", "phone", "name")]
        if q in values:
            exact.append(user)
        elif any(q in v for v in values if v):
            partial.append(user)
    return exact + [u for u in partial if u not in exact]


def find_users(query, data=None, limit=10):
    """Ищет пользователей по id/email/телефону/имени (сначала точные)."""
    data = data or load_data()
    return _find_user_in(data.get("users") or [], query)[:int(limit or 10)]


def find_user(query, data=None):
    """Возвращает первого найденного пользователя или None."""
    found = find_users(query, data=data, limit=1)
    return found[0] if found else None


def _normalize_ticket_id(value):
    raw = str(value or "").strip().lstrip("#").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if upper.startswith("T-"):
        return upper
    if raw.isdigit():
        return "T-" + raw
    return upper


def find_ticket(ticket_id, data=None):
    """Ищет тикет по id (T-1001, #T-1001, 1001) и прикрепляет user."""
    data = data or load_data()
    wanted = _normalize_ticket_id(ticket_id)
    if not wanted:
        return None
    for ticket in data.get("tickets") or []:
        if _norm(ticket.get("id")) == _norm(wanted):
            result = dict(ticket)
            result["user"] = (find_user(result.get("user_id"), data=data)
                              if result.get("user_id") else None)
            return result
    return None


def list_tickets(user_id=None, status=None, limit=10, data=None):
    """Список тикетов пользователя/со статусом, новые сначала."""
    data = data or load_data()
    uid, st = _norm(user_id), _norm(status)
    out = []
    for ticket in data.get("tickets") or []:
        if uid and _norm(ticket.get("user_id")) != uid:
            continue
        if st and _norm(ticket.get("status")) != st:
            continue
        item = dict(ticket)
        item["user"] = find_user(ticket.get("user_id"), data=data)
        out.append(item)
    out.sort(key=lambda t: str(t.get("updated_at") or t.get("created_at")
                               or ""), reverse=True)
    return out[:int(limit or 10)]


def latest_ticket(user_id, status=None, data=None):
    """Последний обновлённый тикет пользователя (для /support user=...)."""
    items = list_tickets(user_id=user_id, status=status, limit=1, data=data)
    return items[0] if items else None


def format_user(user):
    """Короткая карточка пользователя для промпта/ответа."""
    user = user or {}
    parts = [
        "ID: %s" % user.get("id", "—"),
        "Имя: %s" % user.get("name", "—"),
        "Email: %s" % user.get("email", "—"),
    ]
    if user.get("phone"):
        parts.append("Телефон: %s" % user.get("phone"))
    if user.get("plan"):
        parts.append("Тариф: %s" % user.get("plan"))
    if user.get("status"):
        parts.append("Статус: %s" % user.get("status"))
    parts.append("2FA: %s" % ("включена" if user.get("two_factor")
                              else "выключена"))
    if user.get("last_seen"):
        parts.append("Последняя активность: %s" % user.get("last_seen"))
    return "; ".join(parts)


def format_ticket(ticket):
    """Карточка тикета для промпта/ответа (без пользователя)."""
    ticket = ticket or {}
    lines = [
        "ID: %s" % ticket.get("id", "—"),
        "Тема: %s" % ticket.get("subject", "—"),
        "Статус: %s" % ticket.get("status", "—"),
        "Приоритет: %s" % ticket.get("priority", "—"),
        "Создан: %s" % ticket.get("created_at", "—"),
        "Обновлён: %s" % ticket.get("updated_at", "—"),
        "Продукт: %s %s" % (ticket.get("product", "—"),
                            ticket.get("version", "")),
        "Канал: %s" % ticket.get("channel", "—"),
    ]
    if ticket.get("error_code"):
        lines.append("Код ошибки: %s" % ticket.get("error_code"))
    if ticket.get("tags"):
        lines.append("Теги: %s" % ", ".join(str(t) for t in
                                           ticket.get("tags") or []))
    if ticket.get("description"):
        lines.append("Описание: %s" % ticket.get("description"))
    steps = ticket.get("steps_tried") or []
    if steps:
        lines.append("Что уже пробовал пользователь:")
        lines.extend("  - %s" % str(s) for s in steps)
    messages = ticket.get("messages") or []
    if messages:
        lines.append("История обращения:")
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            lines.append("  [%s] %s: %s" % (
                msg.get("at", "—"), msg.get("role", "—"),
                msg.get("text", "")))
    return "\n".join(lines)


def build_context(ticket, user=None):
    """Контекст «тикет + пользователь» для системного промпта.

    Для общего вопроса о продукте (без тикета/пользователя) возвращает
    пустую строку: в промпт попадает только инструкция ассистента.
    """
    ticket = ticket or {}
    user = user or ticket.get("user") or {}
    parts = []
    if ticket:
        parts.append("ТИКЕТ ПОДДЕРЖКИ:\n%s" % format_ticket(ticket))
    if user:
        parts.append("ПОЛЬЗОВАТЕЛЬ:\n%s" % format_user(user))
    text = "\n\n".join(parts)
    limit = int(getattr(config, "SUPPORT_CONTEXT_CHARS", 6000) or 6000)
    return text[:limit]


def build_rag_query(question, ticket=None, user=None, limit=800):
    """Поисковый запрос к FAQ по вопросу и данным тикета."""
    ticket = ticket or {}
    user = user or ticket.get("user") or {}
    parts = [str(question or "").strip(),
             str(ticket.get("subject") or ""),
             str(ticket.get("description") or ""),
             str(ticket.get("error_code") or ""),
             " ".join(str(t) for t in (ticket.get("tags") or [])),
             str(ticket.get("product") or ""),
             str(user.get("plan") or ""),
             "FAQ поддержка DeepSeek Chat"]
    return " ".join(p for p in parts if p).strip()[:int(limit or 800)]


def build_support_system_context(ticket, user=None, extra=""):
    """Системный блок поддержки: инструкция + тикет/пользователь."""
    text = SUPPORT_SYSTEM_PROMPT
    context = build_context(ticket, user)
    if context:
        text += "\n\n" + context
    extra = str(extra or "").strip()
    if extra:
        text += "\n\n" + extra
    return text


def resolve_context(ticket_id="", user_id="", query="", data=None,
                    allow_empty=False):
    """Собирает контекст обращения по тикету и/или пользователю.

    Возвращает dict {ok, ticket, user, text, rag_query, error}. Если задан
    ticket_id — он имеет приоритет; при одном user_id берётся последний
    ОТКРЫТЫЙ тикет пользователя (если открытых нет — последний любой).
    allow_empty=True разрешает общий вопрос о продукте без тикета и
    пользователя (тогда text пустой, но rag_query заполнен).
    """
    data = data or load_data()
    ticket = None
    user = None
    tid = str(ticket_id or "").strip()
    uid = str(user_id or "").strip()
    if tid:
        ticket = find_ticket(tid, data=data)
        if not ticket:
            return {"ok": False, "ticket": None, "user": None, "text": "",
                    "rag_query": build_rag_query(query),
                    "error": "тикет %r не найден" % tid}
        user = ticket.get("user")
        if not user and uid:
            user = find_user(uid, data=data)
    elif uid:
        user = find_user(uid, data=data)
        if not user:
            return {"ok": False, "ticket": None, "user": None, "text": "",
                    "rag_query": build_rag_query(query),
                    "error": "пользователь %r не найден" % uid}
        ticket = latest_ticket(user.get("id"), status="open", data=data)
        if ticket is None:
            ticket = latest_ticket(user.get("id"), data=data)
    elif not allow_empty:
        return {"ok": False, "ticket": None, "user": None, "text": "",
                "rag_query": build_rag_query(query),
                "error": "укажите ticket_id или user_id"}
    text = build_context(ticket, user)
    rag_query = build_rag_query(
        query or (ticket or {}).get("subject", ""), ticket, user)
    return {"ok": True, "ticket": ticket, "user": user, "text": text,
            "rag_query": rag_query, "error": None}

