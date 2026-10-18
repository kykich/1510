# Трассировка требований: `docs/Final_task.md`, задача 3 → реализация → тест

**Тема (задача 3 Final_task):** мини-сервис поддержки пользователей. Ассистент
отвечает на вопросы о продукте, использует RAG по FAQ/документации и учитывает
контекст пользователя или тикета; CRM/JSON с тикетами подключён через MCP.

## Требование → код → тест

| Требование | Реализация | Тест |
|---|---|---|
| Отвечать на вопросы о продукте | `rtk_app/support.py::SUPPORT_SYSTEM_PROMPT` — структура ответа (`## Краткий ответ`, `## Почему так`, `## Что сделать`, `## Когда эскалировать`), запрет выдумывать факты и запрашивать пароли/2FA | §2, §9 |
| RAG по FAQ/документации | `docs/support/FAQ.md`, `docs/support/product.md`; `support_index.py` индексирует каталог `SUPPORT_DOCS_DIR` в общий `session/rag_index.db`; `web/server.py::support_rag_context` ищет **только** внутри `docs/support` | §5, §6 |
| Учитывать контекст пользователя/тикета | `rtk_app/support.py`: `find_user`/`find_ticket`/`list_tickets`/`latest_ticket`, `resolve_context`, `format_ticket`, `format_user`, `build_context`; `Agent._build_messages` добавляет `support_context` отдельным блоком системного промпта | §1–3, §9 |
| MCP: CRM или JSON с пользователями/тикетами | `support_server.py` — MCP-сервер «Поддержка»: `ping`, `find_user`, `get_ticket`, `list_tickets`, `get_support_context`, `stats`; данные — `support_data/users.json` + `tickets.json`; сервер зарегистрирован в `config.MCP_SERVERS` (`id="support"`) | §4, §8 |
| Пример «Почему не работает авторизация?» с данными тикета | демо-тикет `T-1001` (ошибка `401`, смена пароля, 2FA, старая вкладка); `/support T-1001 Почему не работает авторизация?` → MCP `get_support_context` → контекст тикета/пользователя + FAQ-фрагменты → модель | §10 |
| Команда в чате | `web/server.py::parse_support` (`ticket=`, `user=`, первый `T-1001`/`1001`, `--no-rag`), `support_lookup` (MCP → fallback JSON), ветка `is_support` в `_handle_ask`; `js/app.js` показывает ошибку карточкой | §7, §8, §10 |
| Устойчивость к сбоям | MCP недоступен/ошибка → чтение тех же JSON напрямую; нет индекса FAQ → ответ по тикету без RAG; неизвестный тикет/пользователь → понятная ошибка в чате | §3, §8 |
| Приватность | данные в репозитории **вымышленные**; реальные — через `SUPPORT_DATA_DIR` вне репозитория; `support_data/` исключён из общего RAG (`RAG_DOC_SKIP_DIRS`); инструменты MCP только читают | `support_data/README.md`, §4–5 |

## Как запустить

```bash
# автономный тест (без сети, MCP и Ollama)
python -m tests.check_support          # 106 проверок

# индекс FAQ поддержки (нужна Ollama с nomic-embed-text-v2-moe)
python support_index.py

# сервер и чат
python rtk_web.py 8123
```

Команды в чате:

```text
/support T-1001 Почему не работает авторизация?
/support ticket=T-1001 --no-rag что проверить?
/support user=anna@example.com какие мои обращения?
/support Как сбросить пароль?          # общий вопрос о продукте
```

Пошаговая инструкция проверки — `docs/VERIFY_task3.md`.

## Результаты проверки

| Проверка | Результат |
|---|---|
| `python -m tests.check_support` | **106 OK, 0 FAIL** |
| Поиск пользователя | email/id/телефон/частичное имя; точные совпадения выше частичных |
| Поиск тикета | `T-1001`, `#T-1001`, `1001`; пользователь прикрепляется |
| `resolve_context` | тикет приоритетнее; по `user_id` — последний открытый тикет; понятные ошибки |
| MCP-сервер | `support_server` импортируется, инструменты отвечают JSON, `get_support_context` отдаёт `text` и `rag_query` |
| `/support` в чате | MCP-вызов, fallback на JSON, RAG по FAQ, контекст тикета в системном промпте, `support=true` в ответе |
| RAG-изоляция | в тестовом индексе чужой файл рядом с FAQ **не попадает** в контекст (`docs_dir=docs/support`) |
| Ошибки | неизвестный тикет → `ok=false` + текст карточки; MCP-сбой → fallback без падения |
| Приватность `/docs` | `support_data/users.json` через `/docs/` → **403** (данные обращения не отдаются) |

## Ограничения

- `support_data/` — **вымышленные** данные для демонстрации; реальный контур
  подключается через `SUPPORT_DATA_DIR`/свою CRM-интеграцию.
- MCP-сервер только читает данные: создание/обновление тикетов не реализовано.
- Каждый вызов `/support` поднимает stdio-подпроцесс MCP (как и другие серверы
  проекта) — на слабой машине первый ответ может быть медленнее.
- RAG по FAQ требует индекса и Ollama; без индекса ответ строится по тикету,
  но без фрагментов FAQ (в контексте явно нет базы знаний).
- Поиск FAQ ограничен `docs/support`: общая документация проекта в ответ
  поддержки не подмешивается.
- Модель может ошибаться: ответ — рекомендация первой линии, эскалация
  описана в промпте и FAQ.

## Файлы

| Файл | Роль |
|---|---|
| `rtk_app/support.py` | данные, поиск, контекст, промпт, `resolve_context` |
| `support_server.py` | MCP-сервер «Поддержка» (JSON-тикеты/CRM) |
| `support_index.py` | индексация FAQ поддержки в RAG |
| `support_data/users.json`, `support_data/tickets.json` | вымышленные пользователи и тикеты |
| `docs/support/FAQ.md`, `docs/support/product.md` | база знаний для RAG |
| `web/server.py` | `/support`, MCP → JSON fallback, RAG по FAQ |
| `js/app.js` | показ ошибок `/support` карточкой в чате |
| `tests/check_support.py` | автономные проверки (106) |
| `docs/VERIFY_task3.md` | пошаговая инструкция проверки |
