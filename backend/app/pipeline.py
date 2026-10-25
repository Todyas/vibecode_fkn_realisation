"""extract -> reduce -> trigger -> mediate / impact / consolidate; summarize; answer."""
import logging

from . import config, guards, impact, llm, reducer, render, triggers
from .models import (AnswerResult, ConsolidateResult, ExtractResult, ImpactResult, Message,
                     MediatorResult, SummaryResult)
from .topics import TopicRoom

log = logging.getLogger(__name__)
MAX_TRIGGERS_PER_CYCLE = 2


async def process_batch(room: TopicRoom, batch: list[Message]) -> None:
    if not config.llm_enabled():
        return
    topic, cv = room.topic, room.canvas
    await room.set_status("reading")
    try:
        res = await llm.structured(config.FAST_MODEL, llm.load_prompt("extractor"),
                                   render.extractor_input(cv, topic.messages, batch),
                                   ExtractResult, role="extractor")
    except llm.LLMFailed as e:
        log.warning("extractor failed: %s", e)
        await room.set_status("idle")
        return
    patch, sig = reducer.apply_extract(topic, res)
    await room.patch(patch)
    triggers.collect(room, sig, batch)
    for _ in range(MAX_TRIGGERS_PER_CYCLE):
        trig = triggers.pick(room)
        if trig is None:
            break
        await room.set_status("thinking")
        try:
            ok = await run_trigger(room, trig)
        except llm.LLMFailed as e:
            log.warning("%s failed: %s", trig.kind, e)
            ok = False
        if ok:
            triggers.mark_done(room, trig)
        elif trig.kind != "CONSOLIDATE":
            room.pending.append(trig)   # сработает ещё раз со следующей пачкой
            break
    await room.set_status("idle")


async def run_trigger(room: TopicRoom, trig: triggers.Trigger) -> bool:
    topic, cv = room.topic, room.canvas
    if trig.kind == "IMPACT":
        changed = {c[0] for c in trig.changes}
        hit = impact.affected(cv, changed)
        if not hit:
            return True
        old = "; ".join(c[1] for c in trig.changes)
        new = "; ".join(c[2] for c in trig.changes)
        await room.patch(reducer.mark_needs_review(topic, [n.id for n in hit],
                                                   f"изменилось: {new}"))
        user = ("КАНВАС:\n" + render.canvas_compact(cv) +
                "\n\nЗАТРОНУТЫЕ УЗЛЫ:\n" + "\n".join(f"{n.id} {n.text}" for n in hit) +
                "\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n" + render.recent_messages(topic.messages, 10))
        res = await llm.structured(config.STRONG_MODEL,
                                   llm.load_prompt("impact").format(old=old, new=new),
                                   user, ImpactResult, role="impact")
        await room.patch(reducer.apply_impact(topic, res, {n.id for n in hit}))
        if res.chat_message:
            await room.agent_say(res.chat_message, focus_ids=[n.id for n in hit],
                                 reply_to_seq=trig.source_seq, initiative=False)
        return True

    if trig.kind == "CONSOLIDATE":
        await consolidate(room)
        return True

    # CONFLICT / SAME_GOAL / RESOLVE
    if trig.kind != "RESOLVE" and any(
            (n := cv.get(i)) is None or n.status == "rejected" for i in trig.focus_ids):
        return True  # узлы успели исчезнуть
    system = llm.load_prompt("mediator").format(trigger=trig.kind,
                                                focus_ids=", ".join(trig.focus_ids),
                                                conflict_id=trig.conflict_id or "-")
    user = ("КАНВАС:\n" + render.canvas_compact(cv) +
            "\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n" + render.recent_messages(topic.messages, 20))
    if trig.kind == "RESOLVE" and trig.source_seq is not None:
        user += f"\n\nОТВЕТ ЛЮДЕЙ НА ВОПРОС КОНФЛИКТА:\n{render.msg_line(topic.messages[trig.source_seq])}"
    res = await llm.structured(config.STRONG_MODEL, system, user, MediatorResult, role="mediator",
                               validator=guards.check_mediator(cv, trig.kind))
    if trig.kind != "RESOLVE" and res.confidence < config.MEDIATOR_MIN_CONFIDENCE \
            and res.verdict != "misunderstanding":
        res.ops = [o for o in res.ops if o.op == "add_node" and o.section == "questions"]
        res.verdict = "misunderstanding"
    resolver = topic.messages[trig.source_seq].author if (
        trig.kind == "RESOLVE" and trig.source_seq is not None) else None
    patch, new_conflicts = reducer.apply_mediator(topic, res, source=trig.source_seq,
                                                  resolver=resolver)
    await room.patch(patch)
    if res.toast:
        await room.toast(res.toast, trig.focus_ids)
    if res.chat_message and trig.kind != "RESOLVE" and res.verdict != "same_goal":
        conflict_id = new_conflicts[0] if new_conflicts else None
        focus = trig.focus_ids + ([cv.conflict(conflict_id).question_id] if conflict_id else [])
        await room.agent_say(res.chat_message, focus_ids=[f for f in focus if f],
                             reply_to_seq=trig.source_seq, conflict_id=conflict_id,
                             initiative=res.verdict == "complementary")
    return True


async def consolidate(room: TopicRoom) -> None:
    if not config.llm_enabled():
        return
    cv = room.canvas
    if not cv.nodes:
        return
    user = ("КАНВАС:\n" + render.canvas_compact(cv) +
            "\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n" + render.recent_messages(room.topic.messages, 20))
    try:
        res = await llm.structured(config.STRONG_MODEL, llm.load_prompt("consolidator"), user,
                                   ConsolidateResult, role="consolidator",
                                   validator=guards.check_consolidate(cv))
    except llm.LLMFailed as e:
        log.warning("consolidator failed: %s", e)
        return
    await room.patch(reducer.apply_consolidate(room.topic, res))
    room.human_since_consolidate = 0


def fallback_summary(room: TopicRoom) -> str:
    cv = room.canvas
    lines = []
    ess = cv.theses("essence")
    if ess:
        lines.append(ess[0].text)
    sol = cv.theses("solution")
    if sol:
        lines.append("Решение: " + "; ".join(n.text for n in sol[:3]))
    rej = [n for n in cv.nodes if n.status == "rejected"]
    if rej:
        lines.append("Отказались: " + "; ".join(n.text for n in rej[:2]))
    q = [n for n in cv.theses("questions") if n.tag != reducer.RESOLVED_TAG]
    if q:
        lines.append(f"Открытых вопросов: {len(q)}. Главный: {q[0].text}")
    return "\n".join(lines) or f"Тема «{room.topic.name}»: на канвасе пока пусто."


async def summarize(room: TopicRoom) -> str:
    summary = None
    if config.llm_enabled() and room.canvas.nodes:
        user = ("ТЕМА: " + room.topic.name + "\n\nКАНВАС:\n" + render.canvas_compact(room.canvas) +
                "\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n" + render.recent_messages(room.topic.messages, 15))
        try:
            res = await llm.structured(config.STRONG_MODEL, llm.load_prompt("summarizer"), user,
                                       SummaryResult, role="summarizer")
            summary = "\n".join(l.strip() for l in res.lines if l.strip())[:900]
        except llm.LLMFailed as e:
            log.warning("summarizer failed: %s", e)
    summary = summary or fallback_summary(room)
    room.topic.summary = summary
    return summary


async def answer(room: TopicRoom, question: str) -> AnswerResult:
    if not config.llm_enabled():
        return AnswerResult(answer="LLM не настроен, ответить не могу. Канвас: " + room.url)
    user = ("ТЕМА: " + room.topic.name + "\n\nКАНВАС:\n" + render.canvas_compact(room.canvas) +
            "\n\nСООБЩЕНИЯ:\n" + render.recent_messages(room.topic.messages, 40) +
            "\n\nВОПРОС: " + question)
    try:
        res = await llm.structured(config.FAST_MODEL, llm.load_prompt("answerer"), user,
                                   AnswerResult, role="answerer")
    except llm.LLMFailed:
        return AnswerResult(answer="Не получилось ответить, попробуй ещё раз.")
    res.node_ids = [i for i in res.node_ids if room.canvas.get(i)]
    return res
