"""FastAPI + aiogram long polling в одном event loop."""
import asyncio
import html
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, journal, llm, render, snapshot
from .models import Canvas
from .topics import TopicRoom, registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("no_idea")
STUB_PAGE = config.APP_DIR / "static" / "canvas.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry.load()
    tasks = []
    if config.TG_BOT_TOKEN:
        from .bot import run_polling
        tasks.append(asyncio.create_task(run_polling(registry), name="tg-bot"))
    else:
        log.warning("TG_BOT_TOKEN не задан: бот не запущен, работает только сайт и replay")
    asyncio.create_task(snapshot.start())
    yield
    for t in tasks:
        t.cancel()
    await snapshot.stop()


app = FastAPI(title="no_idea", lifespan=lifespan)


def _room(token: str) -> TopicRoom:
    room = registry.by_token.get(token)
    if room is None:
        raise HTTPException(404, "topic not found")
    return room


# ---------- сайт ----------

if (config.FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")


@app.get("/t/{token}")
async def canvas_page(token: str):
    index = config.FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return FileResponse(STUB_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/stub/t/{token}")
async def stub_page(token: str):
    """Запасная страница канваса бэкенда (если фронт не собран или сломан)."""
    return FileResponse(STUB_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/api/topics/{token}")
async def get_topic(token: str):
    return _room(token).snapshot()


@app.get("/api/topics/{token}/export.md", response_class=PlainTextResponse)
async def export_md(token: str):
    room = _room(token)
    return PlainTextResponse(render.markdown(room.topic), media_type="text/markdown; charset=utf-8")


@app.websocket("/ws/topics/{token}")
async def ws_topic(ws: WebSocket, token: str):
    room = registry.by_token.get(token)
    await ws.accept()
    if room is None:
        await ws.close(code=4404)
        return
    room.sockets.add(ws)
    try:
        await ws.send_json({"type": "snapshot", **room.snapshot()})
        while True:
            await ws.receive_text()          # клиент ничего не шлёт, держим соединение
    except WebSocketDisconnect:
        pass
    finally:
        room.sockets.discard(ws)


# ---------- replay и dev ----------

class ReplayReq(BaseModel):
    scenario: str = "demo_shop"
    upto: int | None = None     # сколько реплик проиграть (по умолчанию все)
    start: int = 0
    delay: float = 3.0          # пауза между репликами, больше debounce


def load_scenario(name: str) -> dict:
    path = config.SCENARIOS_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, f"scenario {name} not found")
    return json.loads(path.read_text(encoding="utf-8"))


async def play(room: TopicRoom, sc: dict, start: int, upto: int | None, delay: float) -> None:
    msgs = sc["messages"][start:upto]
    for i, m in enumerate(msgs):
        if i:
            await asyncio.sleep(delay)
        resolves = None
        if m.get("reply_to_bot"):
            open_c = [c for c in room.canvas.conflicts if c.status == "open"]
            resolves = open_c[-1].id if open_c else None
        await room.on_message(m["author"], m["text"], resolves_conflict=resolves)


@app.post("/api/topics/{token}/replay")
async def replay(token: str, req: ReplayReq):
    room = _room(token)
    if room.topic.status != "open":
        raise HTTPException(409, "topic is closed")
    sc = load_scenario(req.scenario)
    asyncio.create_task(play(room, sc, req.start, req.upto, req.delay))
    n = len(sc["messages"][req.start:req.upto])
    return {"ok": True, "messages": n, "url": room.url}


class DevTopicReq(BaseModel):
    name: str = "Демо"
    chat_id: int = 0


@app.post("/api/dev/topic")
async def dev_topic(req: DevTopicReq):
    """Тема без Telegram (для фронта и replay). chat_id=0 — сообщения бота только на сайт."""
    cur = registry.current(req.chat_id)
    if cur is not None and cur.topic.key != req.name.casefold().strip():
        registry.detach(cur)
        await cur.set_topic_status("closed")
    room, reopened = await registry.start(req.chat_id, req.name, "dev")
    return {"token": room.topic.web_token, "id": room.topic.id, "url": room.url,
            "reopened": reopened}


@app.post("/api/dev/topics/{token}/reset")
async def dev_reset(token: str):
    room = _room(token)
    room.topic.messages, room.topic.canvas, room.topic.counters = [], Canvas(), {}
    room.topic.summary = None
    room.by_tg, room.bot_msgs, room.pending, room.done_keys = {}, {}, [], set()
    room.human_since_consolidate = 0
    journal.append(room.topic.id, "reset", {})
    journal.write_meta(room.topic)
    await room.set_topic_status("open")
    await room.broadcast({"type": "snapshot", **room.snapshot()})
    return {"ok": True}


@app.post("/api/dev/topics/{token}/stop")
async def dev_stop(token: str):
    from .bot import close_topic
    room = _room(token)
    summary = await close_topic(room, registry)
    png = await snapshot.canvas_png(token)
    if png:
        (config.DATA_DIR / f"{room.topic.id}.png").write_bytes(png)
    (config.DATA_DIR / f"{room.topic.id}.md").write_text(render.markdown(room.topic), encoding="utf-8")
    return {"summary": summary, "png": bool(png)}


@app.get("/api/dev/topics/{token}/canvas.png")
async def dev_png(token: str):
    _room(token)
    png = await snapshot.canvas_png(token)
    if not png:
        raise HTTPException(500, "snapshot failed")
    from fastapi import Response
    return Response(png, media_type="image/png")


@app.get("/api/dev/topics/{token}/compact", response_class=PlainTextResponse)
async def dev_compact(token: str):
    return render.canvas_compact(_room(token).canvas)


# ---------- служебное ----------

@app.get("/debug/llm", response_class=HTMLResponse)
async def debug_llm():
    rows = []
    for r in reversed(llm.recent):
        prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in r["messages"])
        rows.append(
            f"<details><summary><b>{html.escape(r['role'] or '?')}</b> · {html.escape(r['model'])} · "
            f"{r['latency_s']} с · попытка {r['attempt']}{' · кэш' if r['cached'] else ''}"
            f"{' · <span style=color:red>' + html.escape(r['error']) + '</span>' if r['error'] else ''}"
            f"</summary><pre>{html.escape(prompt)}</pre><b>Ответ</b><pre>"
            f"{html.escape(r['response'] or '')}</pre></details>")
    body = "".join(rows) or "<p>Пока ни одного вызова.</p>"
    return ("<!doctype html><meta charset=utf-8><title>LLM calls</title>"
            "<style>body{font:14px system-ui;margin:24px}pre{white-space:pre-wrap;background:#f4f4f4;"
            "padding:8px}details{border-bottom:1px solid #ddd;padding:6px 0}</style>"
            f"<h1>Последние LLM-вызовы</h1>{body}")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "topics": len(registry.rooms), "bot": bool(registry.notifier),
            "llm": config.llm_enabled(), "cache": config.CACHE_MODE}
