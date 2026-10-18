# Handoff: AI-ассистент разработчика (репозиторий `G:\9potok\1310`)

**Дата:** 18.10.2026  
**Тема:** задачи 1–3 из `docs/Final_task.md` (RAG + `/help` + MCP «Проект»;
AI-ревью PR: diff + RAG → баги/архитектура/рекомендации, GitHub Action и
команда `/review` в чате; поддержка пользователей: JSON-тикеты через MCP,
контекст пользователя и FAQ/RAG, команда `/support`).

Документ описывает состояние на момент передачи: что сделано, что проверено,
что не закоммичено, какие есть риски и с чего продолжать в новой сессии.

---

## 1. Что за проект

Локальный веб-ассистент разработчика:

- HTTP-сервер `rtk_web.py` → `web/server.py` (порт по умолчанию 8123),
  UI в `index.html` + `js/app.js` + `css/style.css`;
- агент `rtk_app/agent.py`: выбор модели (DeepSeek / GigaChat / Local),
  персоны, память, композиция, MCP;
- RAG `rtk_app/rag.py`: индексация PDF/`docs`/кода в SQLite
  (`session/rag_index.db`), гибридный поиск (вектор + BM25), query rewrite;
- MCP-серверы в корне: `project_server.py` («Проект»: git branch/status/diff,
  список файлов, только чтение), `support_server.py` («Поддержка»: JSON-тикеты
  и пользователи, только чтение), `doska_server.py`, `zavhoz_server.py`,
  `zampolit_server.py`, `weather_server.py`, `currency_server.py`,
  `yandex_calendar_server.py`;
- локальная модель: `rtk_app/local_server.py` (запуск `llama-server` из UI,
  OpenAI-совместимый `/v1`).

---

## 2. Статус задач

| Задача | Статус | Где смотреть |
|---|---|---|
| 1. RAG по README/`docs`/коду + MCP git + `/help` | **Готово**, закоммичено (`e8c8308`) | `docs/REPORT_task6_traceability.md`, README |
| 2. AI-ревью PR (diff + RAG → 3 раздела, GitHub Action, `/review`) | **Готово**: код и документация на GitHub; workflow — локальный коммит над `origin/master`, ждёт `workflow`-scope PAT | `docs/REPORT_task7_traceability.md`, `docs/VERIFY_task2.md` |
| 3. Мини-сервис поддержки (FAQ + тикеты + CRM/MCP) | **Готово**: код, данные, тесты (106 OK), документация и `/support` в чате | `docs/REPORT_task8_traceability.md`, `docs/VERIFY_task3.md` |
| 4. Ассистент по файлам проекта (чтение/поиск/изменение) | **Не начато** | `docs/Final_task.md`, задача 4 |

### Задача 1 — что уже работает

- RAG индексирует `README.md`, `docs/`, код (`.py`, `.js`, `.css`, `.html`,
  `.json`, `.yml/.yaml`, `.toml`) и PDF; текстовые файлы — `page=0`,
  id вида `README.md#chunk.3`.
- Секреты **не индексируются**: `RAG_DOC_SKIP_FILES` (`apidpsk.txt`,
  `gigakey.txt`, `ya.txt`, `exch.txt`, `w.txt`, `weathe.txt`).
- **Область поиска — `docs_dir`**: индекс общий, но `search_multi(...,
  docs_dir=...)` отфильтровывает чужие чанки; git-инструменты MCP получают
  `repo=<docs_dir>`; если git нет — заглушка «Нет данных о GIT».
- **Linked worktree**: MCP «Проект» считает репозиторием и папку с
  `.git`-файлом (worktree), а не только с `.git`-папкой — `/help` видит ветку
  `v1009`/`v1210`/… в тестовом стенде версий.
- `/help` без модели возвращает детерминированную справку (ветка, HEAD,
  структура каталогов, статус индекса, до 5 фрагментов) — работает без ключей.
- `/help` с моделью отвечает по RAG + git; в карточке — «Источники».
- Локальная модель: если сервер запущен, источник `Local` автоматически
  появляется в селекторе и используется по умолчанию (в т.ч. для `/help`);
  при остановке исчезает.
- UI: авто-выбор Local, `refreshModels()` при старте/остановке локального
  сервера, исправлен рендер подчёркиваний в inline-code.

### Задача 2 — что уже работает

- `rtk_app/pr_review.py`:
  - `collect_changes` — `git diff base...head` (3-dot, как в PR),
    `--name-status` (включая переименования), `--stat`, лимит
    `MAX_DIFF_CHARS = 120000` с маркером обрезки;
  - `build_rag_query` — пути и идентификаторы из diff → запрос в RAG;
  - `rag_context` — `RagStore.build_context(..., docs_dir=<repo>)`, при
    отсутствии индекса/Ollama ревью не падает, причина в шапке;
  - `build_messages` — системный промпт с обязательными разделами
    `## Потенциальные баги`, `## Архитектурные проблемы`, `## Рекомендации`,
    требование ссылок `файл:строка` и уровня `[critical|high|medium|low]`;
  - `_short_rev` — короткий SHA для сообщений об ошибках;
  - пустой диапазон (`base` и `head` — один коммит) → понятная ошибка без
    вызова модели (см. §2b тестов), а не пустое ревью на 0 файлов;
  - `format_review` — шапка (модель, диапазон `base...head`, число файлов,
    статус RAG), Markdown;
  - `resolve_review_source` — явное разрешение модели; неизвестная метка —
    ошибка, **без тихого отката на GigaChat**;
  - **reasoning-модели** (Qwen3.5): для ревью thinking отключается
    (`disable_thinking` → `chat_template_kwargs.enable_thinking=false`),
    иначе весь `max_tokens` уходит в `reasoning_content`, а `content` пуст;
  - для Local diff автоматически ограничивается под ctx
    (`local_review_diff_limit`), иначе llama-server с ctx 8192 отвечает 400;
  - `local_model_endpoint`, `probe_local_model`, `discover_local_model` —
    автоопределение запущенного `llama-server` по `session/local_model.json`
    и `/v1/models`;
  - `review_changes(...)` — полный цикл, возвращает `ok`, `review`, `files`,
    `rag_used`, `usage`, `error`.
- `review_pr.py` — CLI:
  `--repo/--base/--head/--model/--no-rag/--build-index/--out/
  --max-diff-chars/--max-tokens/--top-k/--local-url/--local-model/
  --local-label`; env `REVIEW_LOCAL_URL/MODEL/LABEL`.
- `.github/workflows/ai-review.yml` — GitHub Action на
  `pull_request` (`opened`, `synchronize`, `reopened`): checkout с полной
  историей, секреты `DEEPSEEK_API_KEY`/`GIGACHAT_KEY` → `apidpsk.txt`/
  `gigakey.txt`, Ollama-шаг `continue-on-error`, запуск `review_pr.py` по
  base/head SHA, артефакт `ai-review` + комментарий в PR
  (`permissions: pull-requests: write`).
- **Команда `/review` в чате** (`web/server.py`):
  - `parse_review` — `/review [base] [head] [--no-rag]`, по умолчанию
    `HEAD~1..HEAD`, RAG включён;
  - `_review_payload` — репозиторий = `docs_dir`, модель = выбранная /
    персона / запущенная локальная; при отсутствии модели — понятная ошибка;
  - команда не перехватывается MCP, «Доской» и композицией (`via_mcp=False`);
  - клиент `js/app.js` пропускает `/review` без выбранной модели (как `/help`),
    чтобы сервер вернул осмысленный ответ/ошибку;
  - результат — Markdown-карточка в чате + список изменённых файлов.
- Тесты `tests/check_pr_review.py`: **86 OK, 0 FAIL** (§1 diff/файлы, §2
  лимиты/ошибки, §2b пустой диапазон ревизий, §3 RAG-запрос, §4 промпт, §5
  полный цикл с фейковой моделью, §6 деградация RAG, §7 локальная модель и
  reasoning, §8 автоопределение из UI, §9 команда `/review`).
- Тестовый стенд версий: `test1310\versions.git` + worktree `1009/1210/2309/
  3108`, ветки `v1009`…`v3108` (см. `docs/VERIFY_versions.md`).

### Задача 3 — что уже работает

- `rtk_app/support.py`: загрузка `users.json`/`tickets.json`, поиск по
  id/email/имени/телефону, поиск тикета (`T-1001`, `#T-1001`, `1001`), список
  и последний открытый тикет, карточки пользователя/тикета, `build_context`,
  `build_rag_query`, `SUPPORT_SYSTEM_PROMPT`, `resolve_context`.
- `support_server.py` (MCP, id `support`): `ping`, `find_user`, `get_ticket`,
  `list_tickets`, `get_support_context`, `stats`; только чтение JSON.
- `support_data/`: вымышленные 3 пользователя и 4 тикета (демо `T-1001` —
  401 после смены пароля, 2FA, старая вкладка). Реальные данные — через
  `SUPPORT_DATA_DIR` вне репозитория.
- `docs/support/FAQ.md`, `product.md` + `support_index.py`: FAQ индексируется
  в общий RAG, поиск `/support` ограничен `docs/support`.
- `web/server.py`: `parse_support`, `support_lookup` (MCP → fallback JSON),
  `support_rag_context`, ветка `is_support` в `_handle_ask`; контекст тикета
  и пользователя добавляется в системный промпт через `support_context`,
  FAQ — отдельным RAG-блоком; ответ помечается `support=true`,
  `support_source`, `ticket_id`, `rag_used`.
- `js/app.js`: ошибки `/support` (тикет не найден) показываются карточкой в
  чате, как у `/review`.
- Тесты `tests/check_support.py`: **106 OK, 0 FAIL** (данные, поиск, контекст,
  MCP-инструменты, индексатор, изоляция RAG, `/support`, fallback, агент,
  HTTP-обработчик).

---

## 3. Git-состояние

```
Репозиторий: G:\9potok\1310
origin  https://github.com/kykich/1310.git
r1210   https://github.com/kykich/1210.git

* <workflow> (master, ahead 1) ci(review): GitHub Action AI-ревью PR
* origin/master — код и документация (см. `git log`: feat(review), docs, handoff)
* e8c8308 feat(rag): RAG по docs/коду, команда /help и MCP-сервер «Проект»
  r1210/feature/rag-help -> e8c8308   (пуш в kykich/1210 выполнен)
```

Код (`6cc961c`), документация и handoff-коммиты отправлены в
`origin/master`. На момент передачи **не закоммичены** изменения задачи 3:
`rtk_app/support.py`, `support_server.py`, `support_index.py`, `support_data/`,
`docs/support/`, `tests/check_support.py`, `docs/REPORT_task8_traceability.md`,
`docs/VERIFY_task3.md`, а также правки `web/server.py`, `js/app.js`,
`rtk_app/agent.py`, `rtk_app/config.py`, `README.md` и этого handoff.

Workflow-коммит (`.github/workflows/ai-review.yml`) **локальный**:
HTTPS-токен без scope `workflow` не даёт GitHub создать workflow-файл.
После обновления токена — `git push origin master` (workflow-коммит будет
пересоздан при необходимости, его hash не фиксируйте в документах).

Ветка `backup/task2-full-push` (`f69dbb5`) — резервная копия исходных двух
коммитов, включавших workflow.

Тестовый стенд `G:\9potok\test1310\versions.git` и `versions\*` лежит **вне**
репозитория `1310` и в коммиты не входит.

Отдельно: рабочая копия `G:\9potok\test1310\1210` (сторонний репозиторий для
проверки) сейчас **грязная** — там свои изменения `docs/Final_task.md`,
`docs/session-handoff.md`, `web/server.py`. Это **не наши правки**, смешивать
с `1310` нельзя.

---

## 4. Файлы задачи 2

| Файл | Роль |
|---|---|
| `rtk_app/pr_review.py` | diff/файлы, RAG-запрос и контекст, промпт, вызов модели, формат ревью, локальная модель |
| `review_pr.py` | CLI для локального запуска и CI |
| `.github/workflows/ai-review.yml` | GitHub Action: ревью на PR + комментарий + артефакт |
| `web/server.py` | `parse_review`, `_review_payload`, ветка `/review` в `_handle_ask` |
| `js/app.js` | пропуск клиентской проверки модели для `/review` |
| `tests/check_pr_review.py` | автономные проверки (76) |
| `docs/REPORT_task7_traceability.md` | трассировка требований → код → тест |
| `docs/VERIFY_task2.md` | пошаговая инструкция проверки |
| `README.md` | разделы про RAG/`/help`, AI-ревью, `/review`, структуру |

---

## 5. Окружение и текущее состояние

| Параметр | Значение (18.10.2026) |
|---|---|
| Python | 3.14.7 |
| Node | v24.20.0 |
| Ключи API | `apidpsk.txt` (DeepSeek) и `gigakey.txt` (GigaChat) есть в корне; **не коммитить и не индексировать** |
| Ollama | запущена, модель `nomic-embed-text-v2-moe:latest` (957 MB) |
| RAG | `enabled=true`, `docs_dir=g:\9potok\1210\`, `top_k=5`, `embed_model=nomic-embed-text-v2-moe`, `chunk_chars=600`, `rewrite=true`, `hybrid=true`, `hybrid_alpha=0.6`, `pool_mult=6`, `min_score=0.0`, `rerank=none` |
| Индекс | `session/rag_index.db` (~58 MB): **635 файлов, 16 231 чанк, 0 изображений**; версии стенда: `versions\1009` — 19 / 335, `1210` — 71 / 2 143, `2309` — 50 / 1 346, `3108` — 2 / 23; также `G:\9potok\1210\` — 75 / 2 212, `test1310` — 220 / 6 107, `1310` — 74 / 2 116, `SKR_PRO` — 124 / 1 949 |
| Локальная модель | `session/local_model.json`: `enabled=true`, `Qwen3.5-9B-Q6_K.gguf`, `ctx=8192`, `ngl=99`, `threads=16`, `port=8080` |
| MCP | `session/mcp.json`: `enabled=true`, `model=Local`, `server=project`, `composition=false` |
| Процессы | `rtk_web` и `llama-server` **остановлены** (18.10); порты **8000/8080/8123 свободны**; Ollama работает на `11434` |

`docs_dir` сейчас указывает на **git-репозиторий** `g:\9potok\1210\`
(ветка `master`, HEAD `648cf70`) — `/review` по умолчанию сравнивает
`HEAD~1...HEAD` (3 файла). Для тестового стенда поставьте `docs:` =
`G:\9potok\test1310\versions\<версия>` (например, `...\1210`), после чего
`/help` покажет ветку `v1210`, а `/review v1009 v1210` сравнит версии.
Если указать родительскую папку `g:\9potok\test1310\` (без `.git`), `/review`
вернёт ошибку «not a git repository», а `/help` — «Нет данных о GIT».

---

## 6. Как запускать и проверять

### Сервер и UI

```powershell
cd G:\9potok\1310
python rtk_web.py 8123
# открыть http://127.0.0.1:8123/
```

### Автономные тесты (без сети/ключей)

```powershell
python -m tests.check_pr_review     # 86 OK, 0 FAIL
python -m tests.check_project_help  # 79 OK, 0 FAIL
python -m tests.check_support       # 106 OK, 0 FAIL
python -m tests.check_composition   # 63 OK, 0 FAIL
python -m tests.check_server_context #  7 OK, 0 FAIL
node --check js\app.js
```

Все проверки — на `tests/harness.py`, комментарии/названия на русском.

### Чат

```text
/help
/help где описаны тесты?
/review                     # HEAD~1..HEAD, repo = docs_dir
/review origin/master HEAD  # явные ревизии
/review --no-rag            # без RAG
/support T-1001 Почему не работает авторизация?
/support user=anna@example.com какие мои обращения?
/support Как сбросить пароль?
```

Модель: выбранная в селекторе → модель персоны → запущенная локальная.
Если ничего нет — `/review` вернёт подсказку выбрать модель; `/help` вернёт
детерминированную справку. Если `base` и `head` совпадают (например,
`/review origin/master`, когда `origin/master == HEAD`), вернётся ошибка
«Нет изменений между …» с названием общего коммита — модель не вызывается.
`/support` требует модель; контекст тикета/пользователя и FAQ подставляются
автоматически (MCP `support`, fallback — JSON).

### CLI

```powershell
python review_pr.py --repo G:\9potok\test1310\1210 `
  --base HEAD~1 --head HEAD --no-rag --model DeepSeek-flash --out review.md

python review_pr.py --repo G:\9potok\test1310\1210 `
  --base HEAD~1 --head HEAD --build-index --model DeepSeek-flash `
  --max-tokens 8000 --out review_rag.md

# локальная модель (URL можно не указывать — берётся из session/local_model.json)
python review_pr.py --repo G:\9potok\test1310\1210 `
  --base HEAD~1 --head HEAD --no-rag --model Local --out review_local.md
```

### GitHub Action

1. Закоммитить `.github/workflows/ai-review.yml` в **базовую** ветку PR.
2. Settings → Secrets and variables → Actions: `DEEPSEEK_API_KEY`
   (и/или `GIGACHAT_KEY` = base64 `client_id:client_secret`).
3. Открыть PR — workflow `AI PR review` запустится на
   `opened/synchronize/reopened`, оставит комментарий и артефакт `ai-review`.
4. Ollama-шаг необязательный (`continue-on-error`): без него ревью без RAG.
5. Локальная модель в CI — только self-hosted runner.

---

## 7. Что уже проверено (evidence)

| Проверка | Результат |
|---|---|
| `tests.check_pr_review` | **86 OK, 0 FAIL** (включая §2b пустой diff, §7 reasoning и §9 `/review`) |
| `tests.check_project_help` | **79 OK, 0 FAIL** (включая linked worktree) |
| `tests.check_composition` | **63 OK, 0 FAIL** |
| `tests.check_server_context` | **7 OK, 0 FAIL** |
| `node --check js/app.js` | OK |
| `/review --no-rag` в реальном HTTP-чате на `test1310\1210` + DeepSeek-flash | `ok=true`, `review=true`, файлов 3, `html_len=4025`, все три раздела |
| `/review` без модели (локальная остановлена) | `ok=false`, текст «Выберите модель (или запустите локальную) — иначе /review не сможет выполнить ревью.» |
| `/review origin/master` при `origin/master == HEAD` | `ok=false` за 0,2 с, «Нет изменений между origin/master и HEAD (обе ревизии указывают на 648cf70)»; модель не вызывается |
| То же через CLI (`--base origin/master --head HEAD`) | код выхода 1, в `review.md` карточка «Ревью не выполнено: Нет изменений…» |
| CLI без RAG на `1210` | все три раздела, ссылки `файл:строка`, severity |
| CLI с `--build-index` на `1210` | в шапке `RAG: использован`; найден `docs/session-handoff.md` c VPS IP и путём к SSH-ключу |
| `/help` на версиях `versions\1009/1210/2309/3108` | `ok=true`, ветки `v1009`/`v1210`/`v2309`/`v3108`, корректные HEAD и репозиторий; RAG-фрагменты только из своей папки |
| `/review` offline на всех 4 версиях | diff собран (3/3/14/1 файлов), RAG использован и ограничен версией, три раздела сформированы |
| `/review --no-rag` в чате на `versions\1009` + Local | `ok=true` за 19,9 с, 3 файла, все три раздела, `html_len=5861` |
| `/review` с RAG в чате на `versions\3108` + Local | `ok=true` за 5,3 с, 1 файл, `rag_used=true`, 8 фрагментов, все три раздела |
| `/review` на `versions\2309` + Local | `ok=true` за 19,9 с, 14 файлов, diff обрезан под ctx 8192, все три раздела |
| `/review v1009 v1210` | 79 файлов, diff обрезан лимитом 120 000 символов (ожидаемо) |
| `/help` с локальной моделью | ответила `Local` (автоопределение через `/v1/models`), без `--local-url` |
| `--model Local` без запущенного сервера | понятная ошибка, без подмены GigaChat |
| GitHub Action на реальном PR | **не проверялся**: workflow в локальном коммите, ждёт push с `workflow`-scope PAT |

Для длинных диффов использовать `--max-tokens 8000`: при 4000 ответ
обрезается.

---

## 8. Известные проблемы и риски

1. **Workflow не отправлен в GitHub.** Локальный workflow-коммит: PAT без
   scope `workflow` не может создать `.github/workflows/ai-review.yml`.
   Обновить токен (classic: `workflow`; fine-grained: Workflows Read/write),
   затем `git push origin master`. После этого проверить Action на PR и
   добавить секрет `DEEPSEEK_API_KEY`/`GIGACHAT_KEY`.
2. **`docs_dir` должен указывать на git-репозиторий.** Сейчас это
   `g:\9potok\1210\` (корректно). Если поставить родительскую папку
   `g:\9potok\test1310\` (без `.git`), `/help` останется без git-данных, а
   `/review` вернёт ошибку. Для другого проекта — `test1310\2309` / `1009`
   или `G:\9potok\1310`.
3. **Общий индекс содержит чужие проекты** (`G:\SKR_PRO\...`, репозитории
   `test1310`). Поиск фильтруется по `docs_dir`, но БД занимает ~58 МБ;
   при желании можно пересобрать индекс только по нужной папке.
4. **Секреты в `1210/docs/session-handoff.md`**: RAG-ревью нашло VPS IP и путь
   к SSH-ключу. Файл стоит почистить/обезличить и проверить, не попал ли он
   в публичный репозиторий.
5. **Длинные diff и лимит токенов.** `MAX_DIFF_CHARS=120000`, ответ модели
   при `--max-tokens 4000` может обрываться — использовать 6000–8000 или
   ревьюить частями.
6. **Комментарий в PR дублируется** на каждый push (инкрементального
   обновления нет).
7. **`/review` синхронный**: HTTP-запрос ждёт модель (десятки секунд);
   для больших диффов лучше CLI/Action.
8. **Локальная модель в CI** недоступна на github-hosted runner — только
   self-hosted.
9. **Рабочая копия `1210` грязная** (не наши изменения) — не коммитить её
   вместе с `1310`.
10. **PowerShell 5.1** ломается на UTF-8-скриптах с кириллицей: для фоновых
    запусков использовался `([wmiclass]'Win32_Process').Create(...)` и
    запись результатов в файл (см. временные скрипты в `%TEMP%`).

---

## 9. Следующие шаги (по приоритету)

1. **Отправить workflow после обновления PAT** (workflow-коммит уже готов):
   ```powershell
   cd G:\9potok\1310
   git push origin master
   ```
   Если Windows Credential Manager хранит старый токен — удалить
   `git:https://github.com` в «Учётные данные Windows» и повторить push.
2. **Проверить Action на реальном PR**: добавить секрет, создать ветку/PR,
   убедиться в комментарии и артефакте `ai-review`.
3. **Проверить тестовый стенд версий**: поставить `docs:` =
   `G:\9potok\test1310\versions\1210`, прогнать `/help` и `/review`;
   сравнение — `/review v1009 v1210` (см. `docs/VERIFY_versions.md`).
4. **Почистить секреты** в `1210/docs/session-handoff.md`, при необходимости
   пересобрать индекс.
5. **Задача 4** (работа с файлами): MCP/tooling на чтение/поиск/изменение,
   минимум 2 сценария (например, «найти использования API» и «обновить
   документацию»), воспроизводимость.
6. Задача 3 (опционально): запись/обновление тикетов через MCP, подключение
   реальной CRM вместо JSON, переиндексация FAQ из UI.
7. Опционально: асинхронное ревью в чате, обновление существующего
   комментария PR вместо нового, очистка/пересборка индекса из UI.

---

## 10. Быстрые команды

```powershell
# состояние
cd G:\9potok\1310
git status --short
git --no-pager log --oneline -3
netstat -ano | Select-String 'LISTENING' | Select-String ':8000|:8080|:8123'

# сервер
python rtk_web.py 8123

# тесты
python -m tests.check_pr_review
python -m tests.check_project_help
python -m tests.check_composition
python -m tests.check_server_context
node --check js\app.js

# ревью
python review_pr.py --repo G:\9potok\test1310\1210 --base HEAD~1 --head HEAD --no-rag --out review.md

# остановить локальную модель / сервер
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'llama-server' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'rtk_web\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## 11. Документация

- `docs/Final_task.md` — исходное ТЗ (задачи 1–4).
- `docs/REPORT_task6_traceability.md` — задача 1: RAG/`/help`/MCP, результаты.
- `docs/REPORT_task7_traceability.md` — задача 2: требования → код → тесты.
- `docs/REPORT_task8_traceability.md` — задача 3: поддержка, тикеты, MCP, FAQ.
- `docs/VERIFY_task2.md` — как проверить задачу 2 (тесты, CLI, RAG, Local, CI,
  чат).
- `docs/VERIFY_task3.md` — как проверить задачу 3 (тикеты, MCP, FAQ/RAG,
  `/support`).
- `docs/VERIFY_versions.md` — тестовый стенд из 4 версий: ветки, worktree,
  проверка RAG/`/help`/`/review`.
- `README.md` — пользовательская документация: RAG, `/help`, AI-ревью,
  `/review`, `/support`, структура проекта.
- `docs/OPTIMIZATION_REVIEW.md` — замечания по архитектуре (не все внедрены).

---

## 12. Соглашения проекта

- Комментарии, docstring и названия проверок — **на русском**.
- Тесты — автономные скрипты `tests/check_*.py` на `tests/harness.py`
  (`check/section/finish`), запуск `python -m tests.<name>`.
- Новые зависимости не добавлялись: задача 2 — только стандартная библиотека
  (`subprocess`, `sqlite3`, `urllib`, `json`, `argparse`).
- Секреты (`apidpsk.txt`, `gigakey.txt`, …) не читать в чат, не коммитить и
  не индексировать.
- Документация ведётся в формате трассировки «требование → реализация → тест».
- Перед коммитом: прогнать затронутые тесты + `node --check js/app.js`,
  проверить `git status` (не захватить `session/`, `review.md`, ключи).



