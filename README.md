# /no_idea — второй мозг в рабочем Telegram-чате

Бот `/no_idea` слушает тему в групповом чате и ведёт живой канвас обсуждения на сайте:
тезисы по 7 блокам, сведение одной цели, конфликты с вопросом в чат, последствия смены ограничений.
На закрытии присылает PNG канваса + итог до 5 строк. Лицензия MIT.

## Запуск (одна команда после установки)

```bash
cp .env.example .env            # впиши TG_BOT_TOKEN, LLM_API_KEY, PUBLIC_BASE_URL
uv sync                         # зависимости из pyproject.toml (в корне)
uv run playwright install chromium
cd backend && uv run python -m app   # бот (long polling) + сайт + API на :8000
```

Без uv: `cd backend && pip install -r requirements.txt && playwright install chromium && python -m app`.

Публичная ссылка для телефонов: `cloudflared tunnel --url http://localhost:8000` → адрес в `PUBLIC_BASE_URL`.

Сайт канваса — SPA в `frontend/` (сборка в `frontend/dist`, её раздаёт FastAPI). Если сборки нет,
работает запасная страница бэкенда (`backend/app/static/canvas.html`, также доступна на `/stub/t/{token}`).

## Команды в чате

| Команда | Что делает |
|---|---|
| `/no_idea start <имя>` | открыть тему или переоткрыть старую, ссылка на канвас |
| `/no_idea stop [имя]` | закрыть: PNG канваса + саммари |
| `/no_idea export [имя]` | PNG + файл .md + ссылка |
| `/no_idea ask [имя] <вопрос>` | ответ по канвасу (или реплай на сообщение бота) |
| `/no_idea list`, `/no_idea help` | темы чата, справка |

Имя из нескольких слов — в кавычках. Реплай на вопрос бота по конфликту разрешает конфликт.

## Без Telegram (тесты, страховка на защите)

```bash
curl -XPOST localhost:8000/api/dev/topic -H 'content-type: application/json' -d '{"name":"Продажи"}'
# -> {"token": "..."}; открой http://localhost:8000/t/<token>
curl -XPOST localhost:8000/api/topics/<token>/replay -H 'content-type: application/json' \
     -d '{"scenario":"demo_shop","upto":4,"delay":3}'
curl localhost:8000/api/dev/topics/<token>/compact        # канвас текстом
curl -XPOST localhost:8000/api/dev/topics/<token>/stop    # закрыть, PNG и .md в backend/data/
curl -XPOST localhost:8000/api/dev/topics/<token>/reset   # очистить тему
```

`/debug/llm` — последние 20 LLM-вызовов. `/healthz` — статус бота, LLM и кэша.
`CACHE_MODE=record` на репетиции, `CACHE_MODE=replay` на защите.

## Устройство

Один Python-процесс: aiogram 3 (long polling) + FastAPI + WebSocket. LLM возвращает операции,
Reducer на Python их проверяет и применяет (`backend/app/reducer.py`), guards запрещают склеивать
конфликты (`guards.py`). Состояние в памяти + журнал JSONL на тему (`backend/data/topics/`),
при рестарте темы восстанавливаются из журнала без LLM. Контракт сайта — `docs/api-contract.md`.

Тесты: `cd backend && pytest -q`.
