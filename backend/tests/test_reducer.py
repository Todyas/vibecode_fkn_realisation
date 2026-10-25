from app import guards, impact, reducer, render, triggers
from app.models import (CanvasOp, ExtractOp, ExtractResult, ImpactItem, ImpactResult, Message,
                        MediatorResult, Topic)


def mk_topic(*msgs: tuple[str, str]) -> Topic:
    t = Topic(id="T1", chat_id=1, name="Продажи", key="продажи", web_token="x")
    for i, (a, txt) in enumerate(msgs):
        t.messages.append(Message(seq=i, ts=0, author=a, text=txt))
    return t


def ex(*ops) -> ExtractResult:
    return ExtractResult(ops=[ExtractOp(**o) for o in ops])


def test_add_nodes_refs_and_depth():
    t = mk_topic(("Руководитель", "+15%"), ("Маркетолог", "персональные"))
    patch, sig = reducer.apply_extract(t, ex(
        dict(op="add_node", ref="r1", section="essence", text="+15% продаж за квартал", source_msg=0, author="x"),
        dict(op="add_node", ref="r2", section="solution", text="Персональные предложения",
             goal="вернуть клиентов", source_msg=1, author="x"),
        dict(op="add_node", ref="r3", parent_id="r2", text="Готовые наборы", source_msg=1, author="x"),
        dict(op="add_node", parent_id="r3", text="Глубже нельзя", source_msg=1, author="x"),
        dict(op="add_node", section="essence", text="Вторая суть", source_msg=1, author="x"),
    ))
    cv = t.canvas
    assert [n.id for n in cv.nodes] == ["N1", "N2", "N3", "N4"]
    assert cv.get("N1").authors == ["Руководитель"]          # автор берётся из сообщения
    assert cv.get("N3").parent_id == "N2" and cv.get("N3").section == "solution"
    assert cv.get("N4").parent_id == "N2"                      # глубина > 2 -> к дедушке
    assert len(cv.theses("essence")) == 1
    assert sig.new_ids == ["N1", "N2", "N3", "N4"]
    assert all(op["op"] == "upsert_node" for op in patch)


def test_attach_and_new_to_active():
    t = mk_topic(("Оля", "очереди"), ("Саша", "согласна"))
    reducer.apply_extract(t, ex(dict(op="add_node", section="problem", text="Очереди", source_msg=0, author="Оля")))
    assert t.canvas.get("N1").status == "new"
    patch, _ = reducer.apply_extract(t, ex(dict(op="attach_source", node_id="N1", source_msg=1, author="Саша")))
    n = t.canvas.get("N1")
    assert n.status == "active" and n.authors == ["Оля", "Саша"] and n.source_msgs == [0, 1]
    assert len(t.canvas.nodes) == 1


def test_bad_ids_dropped():
    t = mk_topic(("a", "x"))
    _, sig = reducer.apply_extract(t, ex(dict(op="attach_source", node_id="N99", source_msg=0, author="a"),
                                         dict(op="add_node", parent_id="N77", text="x", source_msg=0, author="a")))
    assert t.canvas.nodes == [] and len(sig.dropped) == 2


def _demo_canvas():
    t = mk_topic(("Руководитель", "бюджет"), ("Маркетолог", "персональные"), ("Дизайнер", "оформление"),
                 ("Маркетолог", "скидка 20"), ("Руководитель", "маржа важнее"))
    reducer.apply_extract(t, ex(
        dict(op="add_node", ref="b", section="constraints", text="Бюджет небольшой", source_msg=0, author=""),
        dict(op="add_node", ref="p", section="solution", text="Персональные предложения", goal="довести до покупки", source_msg=1, author=""),
        dict(op="add_node", ref="d", section="solution", text="Оформление короче", goal="Довести до покупки", source_msg=2, author=""),
    ))
    return t


def test_same_goal_groups_and_merge():
    t = _demo_canvas()
    assert triggers.same_goal_groups(t.canvas) == [["N2", "N3"]]
    res = MediatorResult(same_underlying_goal=True, jointly_satisfiable=True, verdict="same_goal",
                         confidence=0.9, ops=[CanvasOp(op="merge_nodes", node_ids=["N2", "N3"],
                                                       text="Довести до покупки", goal="довести до покупки")])
    assert guards.check_mediator(t.canvas, "SAME_GOAL")(res) == []
    patch, _ = reducer.apply_mediator(t, res)
    top = t.canvas.get("N4")
    assert top.parent_id is None and set(top.authors) == {"Маркетолог", "Дизайнер"}
    assert t.canvas.get("N2").parent_id == "N4" and t.canvas.get("N3").parent_id == "N4"
    assert triggers.same_goal_groups(t.canvas) == []           # уже сведены


def test_conflict_guard_and_resolve():
    t = _demo_canvas()
    reducer.apply_extract(t, ex(dict(op="add_node", ref="s", section="solution", text="Рассылка со скидкой 20%",
                                     goal="вернуть старых клиентов", source_msg=3, author="",
                                     possible_conflict_with=["N1"])))
    bad = MediatorResult(same_underlying_goal=False, jointly_satisfiable=False, verdict="conflict",
                         confidence=0.9, ops=[CanvasOp(op="merge_nodes", node_ids=["N4", "N1"], text="x")])
    probs = guards.check_mediator(t.canvas, "CONFLICT")(bad)
    assert any("jointly_satisfiable" in p for p in probs) and any("add_conflict" in p for p in probs)
    good = MediatorResult(same_underlying_goal=False, jointly_satisfiable=False, verdict="conflict",
                          confidence=0.9, ops=[CanvasOp(op="add_conflict", a="N4", b="N1",
                                                        explanation="скидка vs бюджет",
                                                        question_text="Что важнее: клиенты или маржа?")],
                          chat_message="?")
    assert guards.check_mediator(t.canvas, "CONFLICT")(good) == []
    _, conflicts = reducer.apply_mediator(t, good)
    assert conflicts == ["K1"]
    assert t.canvas.get("N4").status == "disputed" and t.canvas.get("N1").status == "disputed"
    q = t.canvas.get(t.canvas.conflict("K1").question_id)
    assert q.section == "questions"
    # нельзя слить узел из открытого конфликта даже через Consolidator
    p, errs, _ = reducer.apply_canvas_ops(t, [CanvasOp(op="merge_nodes", node_ids=["N4", "N2"], text="x")])
    assert errs and t.canvas.get("N4").parent_id is None
    res = MediatorResult(same_underlying_goal=False, jointly_satisfiable=False, verdict="conflict", confidence=1,
                         ops=[CanvasOp(op="resolve_conflict", conflict_id="K1", keep_ids=["N1"], reject_ids=["N4"],
                                       reason="маржа важнее"),
                              CanvasOp(op="add_node", parent_id="N2", text="Скидка 5% на наборы", depends_on=["N1"])])
    assert guards.check_mediator(t.canvas, "RESOLVE")(res) == []
    reducer.apply_mediator(t, res, source=4, resolver="Руководитель")
    assert t.canvas.get("N4").status == "rejected" and t.canvas.get("N4").status_reason == "маржа важнее"
    assert t.canvas.get("N1").status == "active"
    assert t.canvas.conflict("K1").status == "resolved"
    new = t.canvas.nodes[-1]
    assert new.text == "Скидка 5% на наборы" and new.depends_on == ["N1"] and new.authors == ["Руководитель"]


def test_impact_affected_and_apply():
    t = _demo_canvas()
    reducer.apply_extract(t, ex(dict(op="add_dependency", node_id="N2", constraint_id="N1", source_msg=1, author=""),
                                dict(op="add_node", parent_id="N2", text="Наборы", source_msg=1, author="")))
    t.canvas.get("N4").depends_on = ["N2"]                       # транзитивно
    _, sig = reducer.apply_extract(t, ex(dict(op="change_constraint", node_id="N1", text="Бюджет есть, срок 2 недели",
                                              source_msg=4, author="")))
    assert sig.changed == [("N1", "Бюджет небольшой", "Бюджет есть, срок 2 недели")]
    hit = impact.affected(t.canvas, {"N1"})
    assert {n.id for n in hit} == {"N2", "N4"}
    reducer.mark_needs_review(t, [n.id for n in hit], "срок")
    assert t.canvas.get("N2").status == "needs_review"
    reducer.apply_impact(t, ImpactResult(items=[
        ImpactItem(node_id="N2", decision="keep", reason="ок"),
        ImpactItem(node_id="N4", decision="revise", reason="срок", new_text="Наборы — этап 2"),
        ImpactItem(node_id="N3", decision="drop", reason="не в списке")]), {"N2", "N4"})
    assert t.canvas.get("N2").status == "active"
    assert t.canvas.get("N4").status == "needs_review" and t.canvas.get("N4").revise_text == "Наборы — этап 2"
    assert t.canvas.get("N3").status != "needs_review"


def test_absorb_merge_moves_sources():
    t = mk_topic(("a", "x"), ("b", "y"))
    reducer.apply_extract(t, ex(dict(op="add_node", section="problem", text="Очереди", source_msg=0, author=""),
                                dict(op="add_node", section="problem", text="Очереди 15 минут", source_msg=1, author="")))
    patch = reducer.apply_consolidate(t, __import__("app.models", fromlist=["x"]).ConsolidateResult(
        ops=[CanvasOp(op="merge_nodes", merge_mode="absorb", node_ids=["N1", "N2"], text="Очереди по 15 минут")]))
    assert [n.id for n in t.canvas.nodes] == ["N1"]
    assert t.canvas.get("N1").authors == ["a", "b"] and t.canvas.get("N1").source_msgs == [0, 1]
    assert {"op": "remove_node", "id": "N2"} in patch


def test_render_compact_and_markdown():
    t = _demo_canvas()
    txt = render.canvas_compact(t.canvas)
    assert "[constraints] N1 Бюджет небольшой" in txt and "цель: довести до покупки" in txt
    md = render.markdown(t)
    assert "## Решение" in md and "Персональные предложения" in md
