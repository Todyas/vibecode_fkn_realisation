# Деплой на Railway (постоянный https-адрес)

Один контейнер: бот (long polling) + сайт + API. Журналы тем на volume `/data`.

1. railway.app → войти через GitHub → **New Project → Deploy from GitHub repo** → `Todyas/vibecode_fkn_realisation`.
   Railway сам найдёт `Dockerfile` (сборка ~3–4 мин, ставит Chromium для PNG).
2. Сервис → **Variables** → добавить:
   `TG_BOT_TOKEN`, `LLM_BASE_URL`, `LLM_API_KEY`, `FAST_MODEL`, `STRONG_MODEL`, `FALLBACK_MODEL`,
   `CACHE_MODE=off`, `ROLEPLAY_NAMES=1`. `PORT` и `PUBLIC_BASE_URL` не нужны.
3. Сервис → **Settings → Networking → Generate Domain** (порт 8000). Получишь `https://….up.railway.app` —
   `PUBLIC_BASE_URL` подставится из него автоматически, в боте появится кнопка «Показать на канвасе».
4. Сервис → правый клик → **Attach Volume**, mount path `/data` (иначе темы пропадут при передеплое).
5. **Важно:** останови сервер на ноутбуке. Один токен бота может слушать только один процесс,
   иначе Telegram отдаёт `Conflict: terminated by other getUpdates request`.
6. Проверка: `https://<домен>/healthz` → `"bot": true, "llm": true`; в группе `/no_idea start Тест`.

Каждый пуш в main передеплоит автоматически (~1 мин с кэшем). Логи: вкладка **Deployments → View logs**.

План Б без облака — туннель с ноутбука: `cloudflared tunnel --url http://localhost:8000`
(временный https-адрес от Cloudflare, без регистрации) → адрес в `PUBLIC_BASE_URL`.
