# Подключение бота @no_idea_canvas_bot (ноутбук-сервер)

1. **@BotFather:** `/mybots` → @no_idea_canvas_bot → API Token → скопировать.
2. **Privacy mode off:** `/setprivacy` → бот → `Disable`. Затем удалить бота из группы и добавить заново,
   иначе он видит только команды. `/setjoingroups` → `Enable`.
3. **Группа:** добавить бота в «no_idea dev», сделать админом.
4. **Код:** `git pull`, в корне `cp .env.example .env`, заполнить:
   `TG_BOT_TOKEN=...`, `LLM_BASE_URL`, `LLM_API_KEY`, `FAST_MODEL`, `STRONG_MODEL`.
   Ключ Anthropic напрямую: `LLM_BASE_URL=https://api.anthropic.com/v1/`, модели `claude-haiku-5-5` / `claude-sonnet-5-5`.
   По умолчанию (OpenRouter): `FAST_MODEL=qwen/qwen3-235b-a22b-2507`, `STRONG_MODEL=deepseek/deepseek-r1-0528`.
   Через OpenRouter: `https://openrouter.ai/api/v1`, модели `anthropic/claude-haiku-5.5` / `anthropic/claude-sonnet-5.5`.
5. **Запуск:** `cd backend && python3 -m venv .venv && . .venv/bin/activate &&
   pip install -r requirements.txt && playwright install chromium && python -m app`.
   В логе должно быть `bot @no_idea_canvas_bot started (long polling)`. Проверка: http://localhost:8000/healthz → `"bot": true, "llm": true`.
6. **Туннель:** во втором терминале `cloudflared tunnel --url http://localhost:8000` → https-адрес
   в `PUBLIC_BASE_URL` → перезапустить `python -m app` (темы восстановятся из журнала).
7. **Проверка в группе:** `/no_idea start Тест` → ссылка открывается с телефона → пишешь сообщение → на канвасе
   через 2–3 с появляется тезис. Дальше сценарий из `docs/test_dialog.md`.

Если бот молчит на обычные сообщения — privacy mode не применился (шаг 2: удалить и добавить заново).
Если канвас не заполняется — смотри http://localhost:8000/debug/llm (ошибка ключа/модели видна там).

## Перед защитой
- `CACHE_MODE=record` на репетиции (17:00), `CACHE_MODE=replay` на защите; реплики копипастом из заметок, паузы как на репетиции.
- `/no_idea reset Продажи` в demo-группе — очистить тему (скрытая команда, ссылка та же).
- План Б без Telegram: `POST /api/topics/<token>/replay` (см. README).
