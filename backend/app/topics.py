"""TopicRoom: состояние темы, очередь, воркер, broadcast. Реестр тем по chat_id."""
import asyncio
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Protocol

from fastapi import WebSocket

from . import config, journal
from .commands import find_key, norm_key
from .models import AgentStatus, ChatState, Message, Topic

log = logging.getLogger(__name__)


class Notifier(Protocol):
    """Отправка сообщений бота в Telegram (реализует bot.py)."""

    async def agent_say(self, room: "TopicRoom", text: str, *, reply_to_tg: int | None,
                        focus_ids: list[str], conflict_id: str | None,
                        initiative: bool) -> int | None: ...


class TopicRoom:
    def __init__(self, topic: Topic, registry: "Registry"):
        self.topic = topic
        self.registry = registry
        self.sockets: set[WebSocket] = set()
        self.agent_status: AgentStatus = "idle"
        self.bot_msgs: dict[int, str | None] = {}   # tg_message_id бота -> conflict_id
        self.by_tg: dict[int, int] = {m.tg_message_id: m.seq for m in topic.messages
                                      if m.tg_message_id is not None}
        # состояние trigger policy
        self.pending: list = []                     # отложенные триггеры
        self.done_keys: set[str] = set()            # отработанные наборы узлов
        self.human_since_consolidate = 0
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.worker: asyncio.Task | None = None

    # ---------- жизненный цикл ----------

    def ensure_worker(self) -> None:
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run(), name=f"room-{self.topic.id}")

    @property
    def canvas(self):
        return self.topic.canvas

    @property
    def url(self) -> str:
        return config.canvas_url(self.topic.web_token)

    # ---------- приём сообщений ----------

    async def on_message(self, author: str, text: str, *, tg_user_id: int | None = None,
                         tg_message_id: int | None = None, reply_to_tg: int | None = None,
                         resolves_conflict: str | None = None, is_agent: bool = False,
                         process: bool = True) -> Message:
        msg = Message(seq=len(self.topic.messages), ts=time.time(), text=text, author=author,
                      tg_user_id=tg_user_id, tg_message_id=tg_message_id,
                      reply_to_seq=self.by_tg.get(reply_to_tg) if reply_to_tg else None,
                      is_agent=is_agent, resolves_conflict=resolves_conflict)
        self.topic.messages.append(msg)
        if tg_message_id is not None:
            self.by_tg[tg_message_id] = msg.seq
        journal.append(self.topic.id, "message", msg)
        await self.broadcast({"type": "message", "message": msg.model_dump()})
        if process and not is_agent:
            self.human_since_consolidate += 1
            self.ensure_worker()
            await self.inbox.put(("msg", msg))
        return msg

    async def run_job(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Выполнить fn в воркере темы после уже поставленных сообщений (без гонок)."""
        self.ensure_worker()
        fut = asyncio.get_running_loop().create_future()
        await self.inbox.put(("job", (fn, fut)))
        return await fut

    async def _run(self) -> None:
        from .pipeline import process_batch  # поздний импорт: pipeline зависит от topics
        carry = None
        while True:
            kind, item = carry or await self.inbox.get()
            carry = None
            if kind == "job":
                fn, fut = item
                try:
                    fut.set_result(await fn())
                except Exception as e:  # noqa: BLE001
                    log.exception("job failed in %s", self.topic.id)
                    if not fut.done():
                        fut.set_exception(e)
                continue
            batch = [item]
            await asyncio.sleep(config.DEBOUNCE_S)
            while not self.inbox.empty():
                k, m = self.inbox.get_nowait()
                if k == "msg":
                    batch.append(m)
                else:
                    carry = (k, m)
                    break
            try:
                await process_batch(self, batch)
            except Exception:
                log.exception("process_batch failed in %s", self.topic.id)
                await self.set_status("idle")

    # ---------- рассылка на сайт ----------

    async def broadcast(self, ev: dict) -> None:
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_json(ev)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    def snapshot(self) -> dict:
        return {"topic": self.topic.info(),
                "messages": [m.model_dump() for m in self.topic.messages],
                "canvas": self.canvas.model_dump(),
                "agent_status": self.agent_status}

    async def set_status(self, status: AgentStatus) -> None:
        if status != self.agent_status:
            self.agent_status = status
            await self.broadcast({"type": "agent_status", "status": status})

    async def patch(self, ops: list[dict]) -> None:
        if not ops:
            return
        self.canvas.version += 1
        journal.append(self.topic.id, "canvas", self.canvas)
        journal.write_meta(self.topic)  # счётчики id
        await self.broadcast({"type": "canvas_patch", "version": self.canvas.version, "ops": ops})

    async def toast(self, text: str, focus_ids: list[str]) -> None:
        await self.broadcast({"type": "toast", "text": text, "focus_ids": focus_ids})

    async def agent_say(self, text: str, *, focus_ids: list[str] | None = None,
                        reply_to_seq: int | None = None, conflict_id: str | None = None,
                        initiative: bool = True) -> int | None:
        """Сообщение бота: на сайт в ленту и (если есть бот) в Telegram."""
        focus_ids = focus_ids or []
        reply_tg = None
        if reply_to_seq is not None and 0 <= reply_to_seq < len(self.topic.messages):
            reply_tg = self.topic.messages[reply_to_seq].tg_message_id
        tg_id = None
        if self.registry.notifier is not None:
            try:
                tg_id = await self.registry.notifier.agent_say(
                    self, text, reply_to_tg=reply_tg, focus_ids=focus_ids,
                    conflict_id=conflict_id, initiative=initiative)
            except Exception:
                log.exception("telegram send failed")
        if tg_id is not None:
            self.remember_bot_msg(tg_id, conflict_id)
        await self.on_message("no_idea", text, tg_message_id=tg_id, is_agent=True, process=False)
        return tg_id

    def remember_bot_msg(self, tg_id: int, conflict_id: str | None) -> None:
        self.bot_msgs[tg_id] = conflict_id
        journal.append(self.topic.id, "bot_msg", {"tg": tg_id, "conflict": conflict_id})

    async def set_topic_status(self, status: str) -> None:
        self.topic.status = status
        if status == "closed":
            self.topic.closed_at = time.time()
        else:
            self.topic.closed_at = None
        journal.write_meta(self.topic)
        await self.broadcast({"type": "topic_status", "status": status,
                              "summary": self.topic.summary})


class TopicError(Exception):
    pass


class Registry:
    def __init__(self):
        self.chats: dict[int, ChatState] = {}
        self.rooms: dict[str, TopicRoom] = {}
        self.by_token: dict[str, TopicRoom] = {}
        self.notifier: Notifier | None = None
        self._n = 0

    def load(self) -> None:
        for topic, bot_msgs in journal.load_all():
            room = self._add(topic)
            room.bot_msgs = bot_msgs
            chat = self.chat(topic.chat_id)
            chat.topics[topic.key] = topic.id
            if topic.status == "open":
                prev = self.rooms.get(chat.open_topic_id or "")
                if prev is not None and prev.topic.opened_at > topic.opened_at:
                    continue
                if prev is not None:
                    prev.topic.status = "closed"
                chat.open_topic_id = topic.id
            self._n = max(self._n, int(topic.id[1:]) if topic.id[1:].isdigit() else 0)
        log.info("restored %d topics", len(self.rooms))

    def _add(self, topic: Topic) -> TopicRoom:
        room = TopicRoom(topic, self)
        self.rooms[topic.id] = room
        self.by_token[topic.web_token] = room
        return room

    def chat(self, chat_id: int) -> ChatState:
        if chat_id not in self.chats:
            self.chats[chat_id] = ChatState(chat_id=chat_id)
        return self.chats[chat_id]

    def current(self, chat_id: int) -> TopicRoom | None:
        cs = self.chats.get(chat_id)
        return self.rooms.get(cs.open_topic_id) if cs and cs.open_topic_id else None

    def topics_of(self, chat_id: int) -> list[TopicRoom]:
        cs = self.chats.get(chat_id)
        if not cs:
            return []
        rooms = [self.rooms[t] for t in cs.topics.values() if t in self.rooms]
        return sorted(rooms, key=lambda r: r.topic.opened_at)

    def find(self, chat_id: int, name: str | None) -> TopicRoom | None:
        cs = self.chats.get(chat_id)
        if not cs or not name:
            return None
        key = find_key(list(cs.topics), name)
        return self.rooms.get(cs.topics[key]) if key else None

    async def start(self, chat_id: int, name: str, by: str) -> tuple[TopicRoom, bool]:
        """Открыть тему или переоткрыть старую. -> (room, reopened)."""
        cs = self.chat(chat_id)
        key = norm_key(name)
        if not key:
            raise TopicError("Напиши имя темы: /no_idea start Онбординг")
        cur = self.current(chat_id)
        if cur is not None:
            if cur.topic.key == key:
                return cur, True
            raise TopicError(f"Сначала /no_idea stop «{cur.topic.name}»")
        if key in cs.topics and cs.topics[key] in self.rooms:
            room = self.rooms[cs.topics[key]]
            cs.open_topic_id = room.topic.id
            await room.set_topic_status("open")
            return room, True
        self._n += 1
        topic = Topic(id=f"T{self._n}", chat_id=chat_id, name=name.strip(), key=key,
                      web_token=secrets.token_urlsafe(12), opened_by=by, opened_at=time.time())
        while topic.web_token in self.by_token:
            topic.web_token = secrets.token_urlsafe(12)
        room = self._add(topic)
        cs.topics[key] = topic.id
        cs.open_topic_id = topic.id
        journal.write_meta(topic)
        return room, False

    def detach(self, room: TopicRoom) -> None:
        """Перестать принимать сообщения чата в тему (первый шаг stop)."""
        cs = self.chats.get(room.topic.chat_id)
        if cs and cs.open_topic_id == room.topic.id:
            cs.open_topic_id = None

    def room_for_bot_reply(self, chat_id: int, tg_id: int) -> tuple[TopicRoom, str | None] | None:
        for room in self.topics_of(chat_id):
            if tg_id in room.bot_msgs:
                return room, room.bot_msgs[tg_id]
        return None


registry = Registry()
