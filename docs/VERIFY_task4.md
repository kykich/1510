# Как проверить задачу 4 (работа с файлами проекта)

Задача 4 из `docs/Final_task.md`: ассистент читает файлы, ищет информацию по
нескольким файлам, анализирует, создаёт и изменяет файлы. Пользователь задаёт
цель, а изменения сохраняются или выводятся как diff.

Все команды выполняются из корня проекта (`G:\9potok\1410`).

## 1. Автономные тесты

```powershell
python -m tests.check_files
```

Ожидаемо: `Итог: 170 OK, 0 FAIL`. Проверяются sandbox, чтение/поиск, JSON-план,
preview/apply, docs, new, MCP-сервер, `/files`, HTTP-интеграция и read-only
smoke-проверка на `G:\9potok\test1310`.

## 2. Поиск использований на тестовом стенде

```powershell
python files_cli.py find RAG_MIN_SCORE --root G:\9potok\test1310 --ext .py
```

Ожидаемо: 6 совпадений в 4 файлах (`1210\rtk_app\rag.py`,
`versions\1210\rtk_app\rag.py`, `1210\rtk_app\config.py`,
`versions\1210\rtk_app\config.py`); `versions.git`, секреты и бинарники
отсутствуют.

Можно проверить regex:

```powershell
python files_cli.py find "def\s+search" --root G:\9potok\test1310 --ext .py --regex
```

## 3. Обновление документации по diff

Корень `G:\9potok\test1310` не является git-репозиторием, поэтому для docs
используется worktree:

```powershell
python files_cli.py docs `
  --root G:\9potok\test1310\versions\1210 `
  --base HEAD~1 --head HEAD --model DeepSeek-flash --no-rag
```

Ожидаемо: Markdown с summary, списком файлов и unified diff; файлы **не
изменены** (preview). Для записи:

```powershell
python files_cli.py docs `
  --root G:\9potok\test1310\versions\1210 `
  --base HEAD~1 --head HEAD --model DeepSeek-flash --no-rag --apply
```

После `--apply` проверьте `git -C G:\9potok\test1310\versions\1210 diff`.
Для безопасной проверки записи лучше использовать временную копию worktree.

Если индекс RAG доступен, уберите `--no-rag` — в промпт добавятся фрагменты
документации/кода.

## 4. Генерация нового файла

```powershell
# preview: файл не создаётся, показывается diff
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root G:\9potok\test1310\versions\1210 --model DeepSeek-flash --no-rag

# запись
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root G:\9potok\test1310\versions\1210 --model DeepSeek-flash --no-rag --apply

# CHANGELOG по последним коммитам
python files_cli.py new changelog "последние изменения" `
  --root G:\9potok\test1310\versions\1210 --model DeepSeek-flash --no-rag --apply
```

Ожидаемо: ADR в `docs/ADR-YYYYMMDD-...md` или обновлённый `CHANGELOG.md`;
в preview файла на диске нет, в apply — есть.

## 5. MCP-сервер «Файлы проекта»

```powershell
python -c "from rtk_app import mcp_client; print(mcp_client.mcp_status(server_id='files'))"
python -c "from rtk_app import mcp_client; print(mcp_client.mcp_call_tool('search_files', {'query': 'RAG_MIN_SCORE', 'ext': '.py', 'max_results': 5, 'root': r'G:\9potok\test1310'}, server_id='files'))"
```

Ожидаемо: 7 инструментов (`ping`, `list_files`, `read_file`, `search_files`,
`file_info`, `preview_file_change`, `apply_file_change`) и JSON с совпадениями.
`apply_file_change` без `confirm=True` возвращает ошибку.

## 6. Чат `/files`

Запустите сервер:

```powershell
python rtk_web.py 8123
```

В блоке RAG укажите `docs:`:
`G:\9potok\test1310\versions\1210` (для docs/new) или
`G:\9potok\test1310` (для поиска по всем версиям).

```text
/files найди все места, где используется RAG_MIN_SCORE
/files где вызывается mcp_call_tool
/files обнови документацию по изменениям HEAD~1..HEAD
/files обнови документацию --apply
/files создай ADR про выбор SQLite для RAG
/files создай CHANGELOG по последним коммитам --apply
```

Поиск работает без модели. Для `docs`/`new` выберите модель (или запустите
локальную). По умолчанию показывается diff; запись — только с `--apply`.
Ошибки (нет модели, невалидный JSON, путь вне root) показываются карточкой в
чате.

## 7. Что считать успехом

- `check_files` — 0 FAIL;
- `find` возвращает совпадения из 2+ файлов, без `versions.git` и секретов;
- в ответе есть `input_files` и блок «Автоматически выбраны файлы»
  (минимум 2–3 файла для `find`/`docs`/`new`);
- `docs`/`new` без `--apply` не меняют файлы, но показывают unified diff;
- `--apply` создаёт/обновляет файл, остальные файлы не меняются;
- MCP `files` отвечает и содержит 7 инструментов;
- `/files` не требует модели для поиска и требует её для генерации;
- запись вне корня, в секрет или в существующий файл с `action=create`
  отклоняется.

## Типовые проблемы

| Симптом | Причина / решение |
|---|---|
| `корень проекта не найден` | проверьте `--root` или поле `docs:` |
| `fatal: not a git repository` | для `docs` нужен git-worktree (`versions\1210`), а не корень `test1310` |
| `модель недоступна` | выберите модель/запустите локальную; для проверки используйте `--no-rag` |
| `модель вернула невалидный JSON` | повторите запрос; файлы не изменены |
| `файл уже существует` | для существующего файла нужен `update`, а не `create` |
| `путь вне проекта` | путь должен быть относительным и внутри `--root` |
| `apply_file_change` не пишет | передайте `confirm=True` (в CLI — `--apply`) |
