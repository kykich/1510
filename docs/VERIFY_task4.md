# Как проверить задачу 4 (работа с файлами проекта)

Задача 4 из `docs/Final_task.md`: ассистент читает файлы, ищет информацию по
нескольким файлам, анализирует содержимое, создаёт и изменяет файлы.
Пользователь задаёт цель, а изменения сохраняются или выводятся как diff.

Все команды выполняются из корня проекта (`G:\9potok\1610`).

## 1. Автономные тесты

```powershell
python -m tests.check_files
```

Ожидаемо: `Итог: 160 OK, 0 FAIL`. Проверяются sandbox, чтение/поиск,
JSON/маркерный формат изменений, preview/apply, docs, new, edit,
MCP-сервер, `/files` и HTTP-интеграция.

## 2. Поиск использований

```powershell
python files_cli.py find RAG_MIN_SCORE --root C:\work\project --ext .py
```

Ассистент обходит проект, пропускает bare-репозитории (`*.git`), секреты и
бинарники, группирует совпадения по файлам. Модель не нужна.

Можно проверить regex:

```powershell
python files_cli.py find "def\s+search" --root C:\work\project --ext .py --regex
```

## 3. Обновление документации по diff

`docs_dir` должен указывать на git-репозиторий проекта:

```powershell
python files_cli.py docs `
  --root C:\work\project `
  --base HEAD~1 --head HEAD --model DeepSeek-flash --no-rag
```

Ожидаемо: Markdown с summary, списком файлов и unified diff; файлы **не
изменены** (preview). Для записи:

```powershell
python files_cli.py docs `
  --root C:\work\project `
  --base HEAD~1 --head HEAD --model DeepSeek-flash --no-rag --apply
```

После `--apply` проверьте `git -C C:\work\project diff`.
Для безопасной проверки записи лучше использовать временную копию проекта.

Если индекс RAG доступен, уберите `--no-rag` — в промпт добавятся фрагменты
документации/кода.

## 4. Генерация нового файла

```powershell
# preview: файл не создаётся, показывается diff
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root C:\work\project --model DeepSeek-flash --no-rag

# запись
python files_cli.py new adr "Выбор SQLite для RAG" `
  --root C:\work\project --model DeepSeek-flash --no-rag --apply

# CHANGELOG по последним коммитам
python files_cli.py new changelog "последние изменения" `
  --root C:\work\project --model DeepSeek-flash --no-rag --apply
```

Ожидаемо: ADR в `docs/ADR-YYYYMMDD-...md` или обновлённый `CHANGELOG.md`;
в preview файла на диске нет, в apply — есть.

## 5. MCP-сервер «Файлы проекта»

```powershell
python -c "from rtk_app import mcp_client; print(mcp_client.mcp_status(server_id='files'))"
python -c "from rtk_app import mcp_client; print(mcp_client.mcp_call_tool('search_files', {'query': 'RAG_MIN_SCORE', 'ext': '.py', 'max_results': 5, 'root': r'C:\work\project'}, server_id='files'))"
```

Ожидаемо: 8 инструментов (`ping`, `list_files`, `read_file`,
`read_many_files`, `search_files`, `file_info`, `preview_file_change`,
`apply_file_change`) и JSON с совпадениями. `apply_file_change` без
`confirm=True` возвращает ошибку.

## 6. Чат `/files`

Запустите сервер:

```powershell
python rtk_web.py 8123
```

В блоке RAG укажите `docs:` — корень проекта, например `C:\work\project`.

```text
/files найди все места, где используется RAG_MIN_SCORE
/files где вызывается mcp_call_tool
/files обнови документацию по изменениям HEAD~1..HEAD
/files обнови документацию --apply
/files создай ADR про выбор SQLite для RAG
/files создай CHANGELOG по последним коммитам --apply
/files edit замени MODEL на deepseek-reasoner
/files edit замени MODEL на deepseek-reasoner --apply
```

Поиск работает без модели. Для `docs`/`new`/`edit` выберите модель (или
запустите локальную). По умолчанию показывается diff; запись — только с
`--apply`. Ошибки (нет модели, путь вне root, пустой файл) показываются
карточкой в чате.

## 7. Что считать успехом

- `check_files` — 0 FAIL;
- `find` возвращает совпадения из 2+ файлов, без bare-репозиториев и секретов;
- в ответе есть `input_files` и блок «Автоматически выбраны файлы»
  (минимум 2–3 файла для `find`/`docs`/`new`);
- `docs`/`new`/`edit` без `--apply` не меняют файлы, но показывают unified diff;
- `--apply` создаёт/обновляет файл, остальные файлы не меняются;
- MCP `files` отвечает и содержит 8 инструментов;
- `/files` не требует модели для поиска и требует её для генерации/правки;
- запись вне корня, в секрет или в существующий файл с `action=create`
  отклоняется.

## Типовые проблемы

| Симптом | Причина / решение |
|---|---|
| `корень проекта не найден` | проверьте `--root` или поле `docs:` |
| `fatal: not a git repository` | для `docs` нужен git-репозиторий, а не родительская папка |
| `модель недоступна` | выберите модель/запустите локальную; для проверки используйте `--no-rag` |
| `модель вернула пустой файл` | повторите запрос; файлы не изменены |
| `файл уже существует` | для существующего файла нужен `update`, а не `create` |
| `путь вне проекта` | путь должен быть относительным и внутри `--root` |
| `apply_file_change` не пишет | передайте `confirm=True` (в CLI — `--apply`) |
