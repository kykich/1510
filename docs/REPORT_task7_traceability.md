# Трассировка требований: `docs/Final_task.md`, задача 2 → реализация → тест

**Тема (задача 2 Final_task):** пайплайн, в котором ассистент анализирует
новый PR: получает diff и изменённые файлы, использует RAG (документация +
код) и выдаёт текст ревью с потенциальными багами, архитектурными проблемами
и рекомендациями.

## Требование → код → тест

| Требование | Реализация | Тест |
|---|---|---|
| Пайплайн запускается на PR (например, GitHub Action) | `.github/workflows/ai-review.yml`: `pull_request` (`opened`, `synchronize`, `reopened`), `actions/checkout` с `fetch-depth: 0` | проверяется `actionlint`/вручную; логика — в `tests/check_pr_review.py` |
| Ассистент получает diff | `rtk_app/pr_review.py::collect_changes` — `git diff base...head` (как в PR), `--name-status`, `--stat`; лимит `MAX_DIFF_CHARS` с маркером обрезки | §1–2 |
| Изменённые файлы | `_parse_name_status` → `{status, path, old_path}`, включая переименования | §1 |
| RAG по документации и коду | `build_rag_query` (пути + идентификаторы из diff) → `rag_context` (`RagStore.build_context(..., docs_dir=<repo>)`, тот же индекс, что у `/help`); индекс строится `review_pr.py --build-index` | §3, §6 |
| Ревью: потенциальные баги, архитектурные проблемы, рекомендации | `build_messages` — системный промпт с обязательными разделами `## Потенциальные баги`, `## Архитектурные проблемы`, `## Рекомендации`; `format_review` добавляет шапку (модель, число файлов, статус RAG) | §4 |
| Возврат результата в PR | workflow: артефакт `ai-review` + комментарий через `actions/github-script` (`issues.createComment`, обрезка до 65 000 символов) | ручная проверка |
| Локальный запуск/проверка | `review_pr.py` (CLI): `--repo/--base/--head/--model/--no-rag/--build-index/--out/--max-diff-chars/--max-tokens/--top-k` | §5 (модель замокана) |
| Локальная модель (OpenAI-совместимая) | `--local-url`/`--local-model`/`--local-label` → `Agent.set_local_model` + `resolve_review_source`; неизвестная метка — явная ошибка (без отката на GigaChat); для reasoning-моделей thinking отключается (`disable_thinking` → `chat_template_kwargs.enable_thinking=false`); для Local diff ограничивается под ctx (`local_review_diff_limit`) | §7 |
| Команда `/review` в чате | `web/server.py`: `parse_review` + `_review_payload` (repo=`docs_dir`, модель выбранная/персона/локальная, `--no-rag`); не перехватывается MCP | `tests/check_pr_review.py` §9 |
| Автоопределение локальной модели из UI | `discover_local_model` читает `session/local_model.json`, проверяет `/v1/models`; при пустом `--model` берётся, если блок включён, при `--model Local` — обязательно | §8 |
| Устойчивость к сбоям | нет индекса/Ollama → ревью без RAG, причина в шапке (`rag_note`); пустой diff (совпавшие ревизии) → ошибка с подсказкой, без вызова модели; ошибка модели → `ok=false` и текст ошибки | §2b, §6 |

## Как запустить

Пошаговая инструкция проверки (включая прогон на репозиториях из
`C:\work\project`) — `docs/VERIFY_task2.md`.

```bash
# автономный тест (без сети, git-репозиторий создаётся во временной папке)
python -m tests.check_pr_review          # 86 проверок

# локальное ревью последнего коммита (без RAG)
python review_pr.py --base HEAD~1 --head HEAD --no-rag --out review.md

# локальное ревью с RAG (нужен индекс: --build-index; эмбеддинги Ollama)
python review_pr.py --base origin/master --head HEAD --build-index --out review.md

# конкретная модель
python review_pr.py --base HEAD~1 --head HEAD --model GigaChat

# локальная модель (OpenAI-совместимый llama-server / Ollama / LM Studio)
python review_pr.py --base HEAD~1 --head HEAD \
  --local-url http://127.0.0.1:8080/v1 --local-model qwen2.5-coder \
  --model Local --out review.md

# локальная модель, запущенная через блок UI: URL можно не указывать
python review_pr.py --base HEAD~1 --head HEAD --model Local --out review.md
python review_pr.py --base HEAD~1 --head HEAD --out review.md   # если блок включён
```

CI:

1. Settings → Secrets and variables → Actions:
   - `DEEPSEEK_API_KEY` — ключ DeepSeek (основная модель);
   - `GIGACHAT_KEY` — base64 `client_id:client_secret` (если DeepSeek нет).
2. Workflow сам создаёт `apidpsk.txt`/`gigakey.txt` из секретов.
3. Шаг установки Ollama (`nomic-embed-text-v2-moe`) помечен `continue-on-error`:
   если он не удался, ревью всё равно выполнится — без RAG, с пометкой причины.
4. Результат: комментарий в PR + артефакт `ai-review`.

## Результаты проверки

| Проверка | Результат |
|---|---|
| `python -m tests.check_pr_review` | **86 OK, 0 FAIL** |
| Сбор diff/файлов на временном git-репозитории | M/A-статусы, diff и stat корректны |
| Обрезка diff | маркер «diff обрезан» при `max_diff_chars` |
| Промпт | три обязательных раздела, diff и RAG-контекст на месте |
| Полный цикл с фейковой моделью | `ok=true`, ревью с разделами, usage посчитан |
| Без индекса RAG | ревью не падает, причина указана в шапке |
| Локальная reasoning-модель (Qwen3.5) | для ревью thinking отключён: `content` не пустой, три раздела; без этого модель тратила все 4000 токенов на `reasoning_content` |
| Большой diff + Local (ctx 8192) | diff автоматически обрезан до безопасного размера; ревью `ok=true`, все разделы |
| Несколько git-веток/репозиториев | `/help` показывает ветку/HEAD целевого репозитория; `/review` собирает diff по указанным `base...head` |
| `/review` с RAG + Local | `ok=true`, RAG-фрагменты использованы, все три раздела |

## Ограничения

- Diff больше `MAX_DIFF_CHARS` (120 000 символов) обрезается — для гигантских
  PR стоит ревьюить по частям (`--base`/`--head` на подмножестве коммитов).
- RAG в CI требует Ollama с моделью эмбеддингов; без неё качество ниже
  (контекст документации/кода не подмешивается), но пайплайн не падает.
- Каждый push в PR создаёт новый комментарий (инкрементального обновления нет).
- Проверяются только diff и контекст: статусы CI, тесты и линтеры не
  анализируются.
- Большие diff'ы между ветками обрезаются лимитом; для полного сравнения —
  CLI с увеличенным `--max-tokens` и `--max-diff-chars`.
- Модель может ошибаться: ревью — рекомендация, а не блокирующая проверка
  (workflow не выставляет required status).
- Локальная чат-модель (llama-server/Ollama/LM Studio) подключается через
  `--local-url`/`--local-model` **или определяется автоматически** по
  `session/local_model.json` (блок «Локальная модель»): при пустом `--model`
  — если блок включён, при `--model Local` — обязательно. Ключи облачных API
  при этом не нужны, но RAG-эмбеддинги по-прежнему считает Ollama. В GitHub
  Actions локальная модель доступна только на self-hosted runner.

## Файлы

| Файл | Роль |
|---|---|
| `rtk_app/pr_review.py` | сбор diff/файлов, RAG-запрос и контекст, промпт, вызов модели, формат ревью |
| `review_pr.py` | CLI для локального запуска и CI |
| `.github/workflows/ai-review.yml` | GitHub Action: ревью на PR + комментарий |
| `tests/check_pr_review.py` | автономные проверки (86) |
| `docs/Final_task.md` | исходное ТЗ (задача 2) |
