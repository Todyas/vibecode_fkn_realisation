"""Журнал JSONL на тему: data/topics/{topic_id}.jsonl. Рестарт = проигрывание журнала без LLM."""
import json
import logging

from . import config
from .models import Canvas, Message, Topic

log = logging.getLogger(__name__)


def _path(topic_id: str):
    return config.TOPICS_DIR / f"{topic_id}.jsonl"


def append(topic_id: str, kind: str, payload) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    with _path(topic_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": kind, "d": payload}, ensure_ascii=False) + "\n")


def write_meta(topic: Topic) -> None:
    append(topic.id, "meta", topic.model_dump(exclude={"messages", "canvas"}))


def load_all() -> list[tuple[Topic, dict[int, str | None]]]:
    """Возвращает темы и карту tg_message_id сообщений бота -> conflict_id."""
    out = []
    for p in sorted(config.TOPICS_DIR.glob("*.jsonl")):
        meta, msgs, canvas, bot_msgs = None, [], Canvas(), {}
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                t, d = ev["t"], ev["d"]
                if t == "meta":
                    meta = d
                elif t == "message":
                    msgs.append(Message.model_validate(d))
                elif t == "canvas":
                    canvas = Canvas.model_validate(d)
                elif t == "bot_msg":
                    bot_msgs[int(d["tg"])] = d.get("conflict")
                elif t == "reset":
                    msgs, canvas, bot_msgs = [], Canvas(), {}
        except Exception:
            log.exception("broken journal %s", p)
            continue
        if meta is None:
            continue
        topic = Topic.model_validate({**meta, "messages": [], "canvas": Canvas()})
        topic.messages, topic.canvas = msgs, canvas
        out.append((topic, bot_msgs))
    return out
