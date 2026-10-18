# Трассировка требований: `docs/Final_task.md` → реализация → тест

**Тема (задача 1 Final_task):** RAG для документации проекта (`README`,
`project/docs`, схемы данных/API), доступ к проекту через MCP (минимум —
текущая git-ветка) и команда **`/help`**, отвечающая на вопросы о структуре
проекта.

На момент составления отчёта реализована **задача 1**. Задачи 2–4 (AI-ревью PR,
поддержка пользователей, ассистент по файлам) в этом объёме не
рассматривались; задачи 2–4 описаны в `docs/REPORT_task7_traceability.md`,
`docs/REPORT_task8_traceability.md` и `docs/REPORT_task9_traceability.md`.

## Требование → код → тест

| Требование | Реализация | Тест |
|---|---|---|
| RAG по `README` и папке `docs` | `rtk_app/rag.py`: `_doc_files` (обход с пропуском служебных папок), `extract_text_file` (UTF-8/cp1251), `_file_chunks(first_page=0)`; настройки `RAG_DOC_EXTS`, `RAG_DOC_SKIP_DIRS`, `RAG_DOC_MAX_MB` в `rtk_app/config.py` | `tests/check_project_help.py` §1–2 |
| Схемы данных / API-описания | те же механизмы: `.json`, `.yml`, `.yaml`, `.toml` в `RAG_DOC_EXTS` | §1 (расширения) |
| Идентификаторы источников | `rag.format_citation_id`: для текста `README.md#chunk.3` (без «стр.N»), для PDF — `doc.pdf#стр.54/chunk.3` | §2 |
| Ссылки «Источники» на вложенные файлы | `rag.format_sources(..., docs_dir=...)` добавляет `path` (относительный); `Agent._render_sources_html` строит `/docs/<path>`; `web/server.py::_serve_rag_doc` отдаёт текстовые файлы как `text/plain; charset=utf-8` | §3 |
| MCP: текущая git-ветка | `project_server.py::git_branch` (ветка, HEAD, upstream, число изменённых файлов), запуск через `config.MCP_SERVERS` (`id="project"`) | §4 |
| MCP (по желанию): список файлов / diff | `project_server.py::git_status`, `git_diff`, `list_files` — только чтение, таймаут и лимит вывода | §4 |
| Команда `/help` | `web/server.py`: `parse_help`, ветка `is_help` в `_handle_ask` (RAG-поиск по проекту + git-сведения из MCP), `_help_payload` — детерминированная справка без LLM; `js/app.js::ask` не требует выбранной модели для `/help` | §5–6 |
| Ответ без модели | `_help_payload`: git-ветка, структура каталогов, состояние индекса RAG, до 5 найденных фрагментов | §6 |
| Ответ с моделью | `_handle_ask` передаёт агенту `rag_context` (git + найденные фрагменты), `rag_sources`, `rag_fragments`; в карточке видны «Источники» | ручная проверка (см. ниже) |

## Как проверить

```bash
# 1. Автономные тесты (без сети, Ollama и LLM)
python -m tests.check_project_help     # 79 проверок
python -m tests.check_rag              # 54
python -m tests.check_rag_rank         # 55
python -m tests.check_rag_citations    # 38

# 2. Запуск проекта
python rtk_web.py 8123

# 3. Индексация документации и кода (корень проекта)
#    POST /api/rag {"action":"reindex","docs_dir":"G:\\9potok\\1310"}
#    (в UI — блок RAG: «папка с документами» = корень проекта, кнопка
#     «Переиндексировать»)
#    Ожидаемо: в отчёте files/chunks > 0, errors пуст.

# 4. Команда /help (в чате)
/help как устроен RAG в проекте?
/help где описаны тесты?
/help

# 5. Без выбранной модели — детерминированная справка;
#    с моделью/персоной — ответ модели с блоком «Источники (RAG)».
```

## Результаты проверки (фактические)

| Проверка | Результат |
|---|---|
| `tests/check_project_help.py` | 79 OK, 0 FAIL |
| `tests/check_rag.py` / `check_rag_rank` / `check_rag_citations` | 54 / 55 / 38 OK, 0 FAIL |
| Остальные наборы (context, memory, strategies, jobs, reminders, composition, weather, auto_compact, server_context) | все зелёные (~200 проверок) |
| Индексация корня проекта (`G:\9potok\1310`) | **68 файлов, 1919 чанков, 0 ошибок**, 183 с |
| Секреты в индексе | `apidpsk.txt` / `gigakey.txt` **отсутствуют** (исключены `RAG_DOC_SKIP_FILES`) |
| `/help` без модели | `ok=true, help=true`: ветка `master`, HEAD `c0cc82b`, upstream `origin/master`, структура, RAG 68/1919, 5 фрагментов; имена секретных файлов не показываются |
| `/help` с DeepSeek-flash | ответ 1411 символов; блок «Источники (RAG)» с 5 ссылками (`/docs/README.md`, `/docs/rtk_app/mcp_client.py`, `/docs/project_server.py`, …), цитаты `project_server.py#chunk.1`, `README.md#chunk.35` |
| Отдача источников | `/docs/README.md`, `/docs/docs/REPORT_task6_traceability.md`, `/docs/rtk_app/rag.py` → 200, `text/plain; charset=utf-8` |
| MCP-клиент | `mcp_call_tool('git_branch', server_id='project')` → `ok=true`, ветка/HEAD/upstream/10 изменённых файлов |
| Область поиска (e2e) | индекс с двумя папками: `/help` при `docs_dir=<другая папка>` вернул только её фрагмент (маркер `ZZOTHERPROJECTZZ`), без содержимого 1310; после возврата `docs_dir=G:\9potok\1310` — только фрагменты 1310, маркер другой папки не найден |
| `/help` при включённом MCP | команда не перехватывается «Доской»/MCP: `help=true`, git-ветка и структура проекта |
| Локальная модель по умолчанию | при запущенном сервере `_running_local_label()` → `Local`, источник добавляется в агент (`running` без `enabled`), `/help` и чат идут через локальную модель; при остановке — детерминированная справка |
| Нет git в целевой папке | `git_branch(repo=<папка без .git>)` → «Нет данных о GIT: папка … не является git-репозиторием»; `/help` показывает заглушку **«Нет данных о GIT»** без fallback на репозиторий ассистента |
| Linked worktree (`.git` — файл) | `project_server._resolve_repo` принимает и папку `.git`, и файл; `/help` на `test1310\versions\1009` → `ветка: v1009`, HEAD `d4d8fc6`, репозиторий — сам worktree |
| MCP-ветка (git_status/diff/files) | сервер передаёт `tool_defaults={"repo": docs_dir}` в `Agent.answer_via_mcp`; `apply_tool_defaults` подставляет `repo` только git-инструментам, явный `repo` сохраняется; `git_status({"repo": <папка без git>})` → «Нет данных о GIT» |

Дополнительно исправлен рендер: подчёркивания в inline-коде больше не
превращаются в `<em>` (`project_server.py` отображается корректно, а не
`project<em>server.py`); проверка — раздел 7 теста.

## Ограничения и решения

- Текстовые файлы попадают в индекс с `page=0` (страниц у них нет), поэтому
  id источника содержит только `chunk.N`.
- Индекс **общий** (`session/rag_index.db`), но поиск ограничен текущей папкой
  `docs_dir`: `search_multi(..., docs_dir=...)` учитывает только чанки файлов
  внутри неё. Переиндексация не удаляет из БД файлы, которых больше нет в
  папке, поэтому без такого фильтра ответы смешивались бы между проектами;
  полная очистка — действие `clear` в `/api/rag`.
- `/help` берёт git-сведения из репозитория, в котором лежит `docs_dir`
  (`project_server.git_branch(repo=...)` находит корень подъёмом по дереву).
  Если git-данных в целевой папке нет, MCP-сервер «Проект» возвращает
  заглушку **«Нет данных о GIT»**, и она же выводится в справке — без
  подмены веткой проекта ассистента. Структура каталогов в детерминированной
  справке — из `docs_dir`.
- В MCP-ветке (`git_branch`, `git_status`, `git_diff`, `list_files`) сервер
  автоматически подставляет `repo=<docs_dir>`: инструменты работают с
  проиндексированным проектом, а не с репозиторием ассистента. Явно
  переданный `repo` сохраняется (`apply_tool_defaults` в `rtk_app/agent.py`).
- `/help` — явная команда: она **не перехватывается** режимом MCP,
  «Доской» или композицией (иначе вместо справки возвращалась таблица
  MCP-сервера).
- **Локальная модель по умолчанию:** если сервер запущен через блок
  «Локальная модель», `/help` и чат используют `Local` автоматически (даже без
  выбранной модели); при остановке сервера — снова детерминированная справка
  или выбранная облачная модель. В селекторе `Local` включается сам после
  запуска и исчезает после остановки.
- В `/help` намеренно **не выполняются** query rewrite и LLM-реранкер: справка
  должна быть быстрой и воспроизводимой; для обычных вопросов 2-й этап RAG
  работает как раньше.
- Индексируются файлы до `RAG_DOC_MAX_MB = 2` МБ; папки `.git`, `session`,
  `node_modules`, `__pycache__` и скрытые каталоги пропускаются.
- **Файлы с секретами не индексируются никогда**: `RAG_DOC_SKIP_FILES`
  (`apidpsk.txt`, `gigakey.txt`, `ya.txt`, `exch.txt`, `w.txt`, `weathe.txt`).
  Иначе ключ API попал бы в базу знаний и мог бы утечь в ответ/источники.
- MCP-сервер «Проект» выполняет только read-only git-команды
  (`rev-parse`, `status`, `diff`, чтение файлов), без записи и произвольного
  shell.
- Если пакет `mcp` не установлен, `/help` продолжает работать: git-сведения
  просто не добавляются, ответ остаётся детерминированным.

## Файлы

| Файл | Роль |
|---|---|
| `rtk_app/config.py` | `RAG_DOC_EXTS`, `RAG_DOC_MAX_MB`, `RAG_DOC_SKIP_DIRS`, запись `project` в `MCP_SERVERS` |
| `rtk_app/rag.py` | индексация документов и кода, `path` источников |
| `rtk_app/agent.py` | ссылки «Источники» по относительному пути |
| `web/server.py` | `/help`, git-сведения, отдача текстовых источников |
| `project_server.py` | MCP-сервер «Проект» (git branch/status/diff, файлы) |
| `js/app.js` | `/help` работает без выбранной модели |
| `tests/check_project_help.py` | автономная проверка задачи 1 |
| `docs/Final_task.md` | исходное ТЗ |
