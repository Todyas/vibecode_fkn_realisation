# /no_idea — сайт канваса

Только просмотр, без регистрации: `{PUBLIC_BASE_URL}/t/{web_token}`. Контракт с бэкендом — `api-contract.md` (v1).

## Запуск

```bash
cd frontend
npm ci
npm run dev      # http://localhost:5173/t/demo — мок demo_shop; /api и /ws проксируются на :8000
npm run build    # сборка в frontend/dist, её раздаёт FastAPI на /t/{token} и /assets/*
```

`frontend/dist` лежит в git, чтобы на ноутбуке-сервере не нужен был Node. После правок фронта — `npm run build` и коммит.

## Режимы страницы

| URL | Что делает |
|---|---|
| `/t/{token}` | Живой канвас: GET `/api/topics/{token}` + WS `/ws/topics/{token}` с переподключением |
| `/t/{token}#N7` | Подсветить узел N7 и прокрутить к нему |
| `/t/{token}?print=1` | Для Playwright: ширина 1600, без ленты и анимаций, только GET; ставит `window.__canvasReady = true` |
| `/t/demo` или `?mock=1` | Мок: сценарий demo_shop проигрывается в браузере без бэкенда |
| `?upto=N` | (мок) сразу состояние после реплики N; `?delay=мс` — пауза между репликами |

Скриншот для бота (snapshot.py):

```python
page = await browser.new_page(viewport={"width": 1600, "height": 900})
await page.goto(f"{base}/t/{token}?print=1")
await page.wait_for_function("window.__canvasReady === true", timeout=10_000)
png = await page.screenshot(full_page=True)
```
