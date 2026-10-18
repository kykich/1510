# Трассировка требований: `docs/Final_task.md`, задача 4 → реализация → тест

**Тема (задача 4 Final_task):** ассистент активно работает с файлами проекта:
читает, ищет по нескольким файлам, анализирует, создаёт и изменяет файлы.
Пользователь задаёт цель, а не путь; изменения сохраняются или выводятся как
diff; результат воспроизводим.

## Требование → код → тест

| Требование | Реализация | Тест |
|---|---|---|
| Читать файлы проекта | `rtk_app/file_tasks.py::read_file` — UTF-8/cp1251, лимиты, запрет секретов/бинарников; MCP `files_server.read_file`; содержимое файлов также читает существующий `project_server.list_files` | §1, §7 |
| Искать информацию по нескольким файлам | `file_tasks.search_files` (literal/regex, номера строк, сниппеты) + `run_find` (группировка по файлам); `files_server.search_files` | §2, §4, §7, §10 |
| Анализировать содержимое | `collect_context`, `_select_docs` (выбор документов по diff/идентификаторам), промпты `_docs_messages`/`_generate_messages`, unified diff | §3, §5, §6 |
| Создавать и изменять файлы | JSON-план/маркеры `<<<FILE>>>` → `parse_plan`/`_extract_file_content` → `preview_plan`/`apply_plan`; `run_update_docs`, `run_generate_file`, `run_edit_file`; MCP `preview_file_change`/`apply_file_change(confirm=True)` | §3, §5, §6, §7 |
| Минимум 2 сценария | Реализованы **4**: поиск использований, обновление docs по diff, генерация ADR/CHANGELOG, редактирование файла по инструкции (`/files edit`) | §4–6, §10 |
| Работа минимум с 2–3 файлами | Поиск по нескольким файлам; docs: изменённые файлы + 1–2 документа + RAG; new: README/docs/код в контексте | §5, §6, §10 |
| Изменения сохраняются или выводятся как diff | По умолчанию preview + unified diff; запись только `--apply`/`confirm=True`; `_result_text` и `_public_plan` | §3, §5, §6 |
| Ассистент сам инициирует работу | `/files <цель>` сам выбирает сценарий, файлы, документы и безопасный путь; пользователь не указывает `открой файл X` | §8, §9 |
| Воспроизводимость | CLI `files_cli.py`; детерминированные sandbox/поиск/diff; тесты на временных фикстурах | §1–10 |
| MCP | `files_server.py` (id `files` в `config.MCP_SERVERS`): `ping`, `list_files`, `read_file`, `read_many_files`, `search_files`, `file_info`, `preview_file_change`, `apply_file_change` | §7 |
| Автовыбор MCP-сервера | Режим `auto` (`config.MCP_AUTO_ID`) + `Agent.answer_via_mcp_auto()`: модель получает инструменты `project`/`files`/`support` и выбирает сервер по запросу; старые настройки мигрируют в `auto` | `tests/check_mcp_auto.py` (51 OK) |
| Переиспользование существующих инструментов | `pr_review.collect_changes`/`rag_context`/`_default_caller`, `RagStore`, `project_server`, `mcp_client`, `html_report`, `SessionStore` | §5, §6, §9 |
| Безопасность | sandbox по `realpath`, запрет `..`, symlink, `.git`/`*.git`, `session`, `support_data`, секретов, бинарников; лимиты; нет delete; atomic write | §1, §3, §7 |

## Сценарии

### 1. Поиск использований

```powershell
python files_cli.py find RAG_MIN_SCORE --root C:\work\project --ext .py
```

Ассистент сам обходит проект, пропускает bare-репозитории (`*.git`), секреты
и бинарники, группирует совпадения по файлам. Модель не нужна.

### 2. Обновление документации по изменениям

```powershell
python files_cli.py docs --root C:\work\project `
  --base HEAD~1 --head HEAD --model DeepSeek-flash

python files_cli.py docs --root C:\work\project `
  --base HEAD~1 --head HEAD --model Local --apply
```

Используется `pr_review.collect_changes` (git diff), `rag_context` и модель.
Модель возвращает JSON с полным новым текстом документа; diff строится
детерминированно. Без `--apply` файлы не меняются.

### 3. Генерация нового файла

```powershell
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root C:\work\project
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root C:\work\project --apply
python files_cli.py new changelog "последние изменения" `
  --root C:\work\project --apply
```

Путь выбирается безопасно: `docs/ADR-YYYYMMDD-<slug>.md`, `CHANGELOG.md`,
`docs/README-<slug>.md`; можно задать `--path`.

### 4. Редактирование файла по инструкции

```text
/files edit замени MODEL = "deepseek-chat" на "deepseek-reasoner"
/files edit замени MODEL = "deepseek-chat" на "deepseek-reasoner" --apply
/files edit rtk_console.py замени MODEL на deepseek-reasoner  # явное уточнение
```

`run_edit_file` без пути сам выбирает файл по терминам инструкции (например,
`MODEL`), читает его, передаёт модели вместе с инструкцией, получает полное
новое содержимое, строит diff. Без `--apply` файл не меняется; с `--apply`
запись атомарная. Понимается и естественная фраза:
`/files измени в файле README.md добавь строку про MCP`.

## Как запустить

```bash
# автономные тесты (без сети и реальной модели)
python -m tests.check_files

# MCP-сервер «Файлы проекта»
python -c "from rtk_app import mcp_client; print(mcp_client.mcp_status(server_id='files'))"
```

## Результаты проверки

| Проверка | Результат |
|---|---|
| `python -m tests.check_files` | **160 OK, 0 FAIL** |
| Автовыбор файлов | `input_files` в ответе: `find` — файлы совпадений; `docs` — изменённый код + документы + RAG; `new` — контекст проекта; текст содержит «Автоматически выбраны файлы» |
| Sandbox | `../`, абсолютный путь, `.env`, `apidpsk.txt`, бинарник, bare-репозиторий (`*.git`) — отклонены/скрыты |
| Имя без расширения | `read_file("README")` / `read_file("rtk_console")` находит единственный `README.md` / `rtk_console.py`; `preview_file_change`/`apply_file_change` с таким именем тоже работают как update. Неоднозначность и секреты отклоняются | §1, §7 |
| Поиск | 3+ файла, literal/regex, номера строк, `max_results` |
| Plan/preview/apply | diff без записи; update/create; запрет create существующего, update отсутствующего, пути вне root и секретов |
| Docs | git diff `HEAD~1..HEAD`, фейковая модель, preview/apply, запрет файла вне target, невалидный JSON |
| New | ADR/CHANGELOG, preview/apply, запрет path traversal |
| MCP | 8 инструментов, `apply_file_change` требует `confirm=True` |
| `/files` | разбор явных и естественных целей, HTTP-интеграция, `applied=false` по умолчанию |
| Фикстуры | поиск по 2–3 файлам, git diff, docs-preview и new-preview без изменения рабочей копии |

## Ограничения

- Модель возвращает **полный текст** файла (JSON-план или маркеры
  `<<<FILE>>>…<<<END>>>`), а не patch: надёжнее для применения, но крупные
  файлы ограничены `FILES_MAX_WRITE_CHARS`.
- `docs` требует git-репозиторий; укажите корень git-проекта, а не его
  родительскую папку.
- RAG-контекст опционален: без индекса/Ollama обновление docs и генерация
  работают по файлам, но без дополнительных фрагментов базы знаний.
- Удаление файлов не реализовано; только create/update.
- По умолчанию запись не выполняется — нужен `--apply`/`confirm=True`.
- Модель может вернуть пустой файл или некорректное содержимое — операция
  отклоняется без изменения файлов; для edit/new используется протокол
  `<<<FILE>>>…<<<END>>>` без JSON-экранирования.

## Файлы

| Файл | Роль |
|---|---|
| `rtk_app/file_tasks.py` | sandbox, чтение, поиск, JSON-план, diff, preview/apply, 3 сценария |
| `files_server.py` | MCP-сервер «Файлы проекта» |
| `files_cli.py` | воспроизводимый CLI |
| `tests/check_files.py` | автономные проверки (128) |
| `web/server.py` | `parse_files`, `_files_payload`, ветка `/files` |
| `js/app.js` | `/files` без обязательной модели для поиска, карточка ошибок |
| `docs/VERIFY_task4.md` | пошаговая проверка |
