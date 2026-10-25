"""Сквозной прогон demo_shop с подменённой LLM: воркер, триггеры, Mediator, Impact, RESOLVE."""
import asyncio

import pytest

from app import config, llm, pipeline
from app.models import (CanvasOp, ExtractOp, ExtractResult, ImpactItem, ImpactResult,
                        MediatorResult, SummaryResult)
from app.topics import Registry

E = lambda **k: ExtractOp(**k)  # noqa: E731

EXTRACT = {
    0: [E(op="add_node", section="essence", text="+15% продаж за квартал", source_msg=0, author=""),
        E(op="add_node", section="constraints", text="Бюджет небольшой", source_msg=0, author="")],
    1: [E(op="add_node", section="problem", text="Уходят до покупки", source_msg=1, author=""),
        E(op="add_node", section="solution", text="Персональные предложения", goal="довести до покупки",
          source_msg=1, author="")],
    2: [E(op="add_node", section="solution", text="Оформление короче", goal="довести до покупки",
          source_msg=2, author="")],
    3: [E(op="add_node", section="constraints", text="2 разработчика, месяц", source_msg=3, author="")],
    4: [E(op="add_node", section="solution", text="Рассылка со скидкой 20%", goal="вернуть старых клиентов",
          possible_conflict_with=["N2"], source_msg=4, author="")],
    5: [],
    6: [E(op="change_constraint", node_id="N2", text="Бюджет есть", source_msg=6, author="")],
}


TEXTS = ["+15%", "уходят", "оформление", "двое и месяц", "скидка 20%", "маржа важнее", "бюджет дали"]


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    async def structured(model, system, user, schema, *, role="", validator=None, max_retries=1):
        calls.append(role)
        if role == "extractor":
            ops = []
            for line in user.split("НОВЫЕ СООБЩЕНИЯ:\n")[1].splitlines():
                seq, text = int(line.split()[0][1:]), line.split("] ", 1)[1]
                ops += [o.model_copy(update={"source_msg": seq}) for o in EXTRACT.get(TEXTS.index(text), [])]
            return ExtractResult(ops=ops)
        if role == "mediator":
            if "ситуацию SAME_GOAL" in system:
                r = MediatorResult(same_underlying_goal=True, jointly_satisfiable=True, verdict="same_goal",
                                   confidence=0.9, toast="Свёл 2 тезиса — цель одна",
                                   ops=[CanvasOp(op="merge_nodes", node_ids=["N4", "N5"],
                                                 text="Довести до покупки", goal="довести до покупки")])
            elif "ситуацию RESOLVE" in system:
                r = MediatorResult(same_underlying_goal=False, jointly_satisfiable=False, verdict="conflict",
                                   confidence=1, ops=[
                                       CanvasOp(op="resolve_conflict", conflict_id="K1", keep_ids=["N2"],
                                                reject_ids=["N8"], reason="маржа важнее"),
                                       CanvasOp(op="add_node", parent_id="N6", text="Скидка 5% на наборы",
                                                depends_on=["N2"])])
            else:
                r = MediatorResult(same_underlying_goal=False, jointly_satisfiable=False, verdict="conflict",
                                   confidence=0.9, chat_message="Что важнее: клиенты или маржа?",
                                   ops=[CanvasOp(op="add_conflict", a="N8", b="N2", explanation="скидка vs бюджет",
                                                 question_text="Что важнее: клиенты или маржа?")])
            assert validator is None or validator(r) == []
            return r
        if role == "impact":
            return ImpactResult(items=[ImpactItem(node_id="N10", decision="revise", reason="бюджет есть",
                                                  new_text="Скидка можно больше")],
                                chat_message="Бюджет изменился, скидку 5% можно пересмотреть.")
        if role == "summarizer":
            return SummaryResult(lines=["Поднимаем продажи на 15%", "Скидка 5% на наборы"])
        raise AssertionError(role)

    monkeypatch.setattr(llm, "structured", structured)
    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(config, "DEBOUNCE_S", 0.01)
    return calls


async def settle(room):
    await room.run_job(lambda: asyncio.sleep(0))


async def test_demo_shop_flow(fake_llm):
    reg = Registry()
    room, reopened = await reg.start(0, "Продажи", "dev")
    assert not reopened
    for i, t in enumerate(TEXTS):
        resolves = "K1" if i == 5 else None
        await room.on_message(f"U{i % 3}", t, resolves_conflict=resolves)
        await settle(room)
        cv = room.canvas
        if i == 2:   # одна цель -> общий тезис
            top = cv.get("N6")
            assert top and top.parent_id is None
            assert cv.get("N4").parent_id == "N6" and cv.get("N5").parent_id == "N6"
        if i == 4:   # конфликт, бот спросил
            assert cv.conflict("K1").status == "open"
            assert cv.get("N8").status == "disputed"
            assert room.topic.messages[-1].is_agent
        if i == 5:   # реплай -> RESOLVE
            assert cv.get("N8").status == "rejected"
            assert cv.conflict("K1").status == "resolved"
            assert cv.get("N10").depends_on == ["N2"]
        if i == 6:   # IMPACT
            assert cv.get("N10").status == "needs_review"
            assert cv.get("N10").revise_text == "Скидка можно больше"
            assert room.topic.messages[-1].is_agent
    assert fake_llm.count("mediator") == 3 and "impact" in fake_llm
    summary = await room.run_job(lambda: pipeline.summarize(room))
    assert summary.startswith("Поднимаем")


async def test_start_rules():
    reg = Registry()
    a, _ = await reg.start(5, "Онбординг", "x")
    with pytest.raises(Exception):
        await reg.start(5, "Другое", "x")
    reg.detach(a)
    await a.set_topic_status("closed")
    b, re = await reg.start(5, "  ОНБОРДИНГ ", "x")
    assert re and b is a and a.topic.status == "open"
    assert reg.find(5, "онб") is a
