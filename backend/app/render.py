"""Канвас -> текст для промптов; канвас -> markdown."""
from .models import SECTION_TITLES, SECTIONS, Canvas, Message, Node, Topic


def _node_line(cv: Canvas, n: Node) -> str:
    parts = [f"{n.id} {n.text}"]
    if n.goal:
        parts.append(f"цель: {n.goal}")
    if n.authors:
        parts.append("авторы: " + ", ".join(n.authors))
    if n.status in ("disputed", "needs_review", "rejected"):
        parts.append(n.status.upper() + (f" ({n.status_reason})" if n.status_reason else ""))
    if n.depends_on:
        parts.append("зависит от: " + ", ".join(n.depends_on))
    if n.tag:
        parts.append(f"пометка: {n.tag}")
    for c in cv.conflicts:
        if c.question_id == n.id:
            parts.append(f"конфликт {c.id}: {c.a} vs {c.b}" + (" (решён)" if c.status == "resolved" else ""))
    return " | ".join(parts)


def canvas_compact(cv: Canvas) -> str:
    lines = []
    for s in SECTIONS:
        for n in [x for x in cv.nodes if x.section == s and x.parent_id is None]:
            lines.append(f"[{s}] " + _node_line(cv, n))
            for ch in cv.children(n.id):
                lines.append("  " + _node_line(cv, ch))
    open_c = [c for c in cv.conflicts if c.status == "open"]
    for c in open_c:
        lines.append(f"(открытый конфликт {c.id}: {c.a} vs {c.b} — {c.explanation})")
    return "\n".join(lines) or "(канвас пуст)"


def msg_line(m: Message) -> str:
    who = "бот" if m.is_agent else m.author
    reply = f" (ответ на #{m.reply_to_seq})" if m.reply_to_seq is not None else ""
    return f"#{m.seq} [{who}]{reply} {m.text}"


def extractor_input(cv: Canvas, messages: list[Message], batch: list[Message]) -> str:
    first = batch[0].seq
    ctx = [m for m in messages if m.seq < first][-8:]
    return ("КАНВАС:\n" + canvas_compact(cv) +
            "\n\nКОНТЕКСТ:\n" + ("\n".join(msg_line(m) for m in ctx) or "(нет)") +
            "\n\nНОВЫЕ СООБЩЕНИЯ:\n" + "\n".join(msg_line(m) for m in batch))


def recent_messages(messages: list[Message], n: int = 20) -> str:
    return "\n".join(msg_line(m) for m in messages[-n:]) or "(нет)"


def markdown(topic: Topic) -> str:
    cv = topic.canvas
    out = [f"# {topic.name}", ""]
    ess = cv.theses("essence")
    if ess:
        out += [f"> Суть: {ess[0].text}", ""]
    if topic.summary:
        out += ["## Итог", "", topic.summary, ""]

    def bullet(n: Node, indent: str = "") -> list[str]:
        mark = ""
        if n.status == "disputed":
            mark = " ⚠️ спорно"
        elif n.status == "needs_review":
            mark = " 🟡 пересмотреть" + (f": {n.status_reason}" if n.status_reason else "")
        who = f" — {', '.join(a for a in n.authors if a != 'agent')}" if n.authors else ""
        tag = f" [{n.tag}]" if n.tag else ""
        return [f"{indent}- {n.text}{tag}{mark}{who}"]

    for s in SECTIONS[1:]:
        nodes = cv.theses(s)
        if not nodes:
            continue
        out.append(f"## {SECTION_TITLES[s]}")
        for n in nodes:
            out += bullet(n)
            for ch in cv.children(n.id):
                if ch.status != "rejected":
                    out += bullet(ch, "  ")
        out.append("")
    rejected = [n for n in cv.nodes if n.status == "rejected" and n.section != "questions"]
    if rejected:
        out.append("## Отказались")
        out += [f"- ~~{n.text}~~" + (f" — {n.status_reason}" if n.status_reason else "") for n in rejected]
        out.append("")
    out.append(f"_Тема: {topic.name} · сообщений: {len([m for m in topic.messages if not m.is_agent])}_")
    return "\n".join(out) + "\n"
