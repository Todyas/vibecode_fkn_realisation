"""Guards: агент физически не может склеить конфликт и потерять чьё-то предложение."""
from .models import Canvas, ConsolidateResult, MediatorResult


def check_mediator(cv: Canvas, kind: str):
    def check(res: MediatorResult) -> list[str]:
        problems = []
        ops = {o.op for o in res.ops}
        open_nodes = cv.open_conflict_nodes()
        if kind != "RESOLVE":
            if not res.jointly_satisfiable and ({"merge_nodes", "add_node"} & ops):
                problems.append("jointly_satisfiable=false: merge_nodes и add_node запрещены, "
                                "разрешён только add_conflict")
            if res.verdict == "conflict" and "add_conflict" not in ops:
                problems.append("verdict=conflict требует операцию add_conflict")
            if res.verdict == "same_goal" and "add_conflict" in ops:
                problems.append("same_goal не может создавать конфликт")
            if "resolve_conflict" in ops:
                problems.append("resolve_conflict разрешён только при trigger=RESOLVE")
        else:
            if "resolve_conflict" not in ops:
                problems.append("trigger=RESOLVE требует операцию resolve_conflict")
        for o in res.ops:
            if o.op == "merge_nodes":
                if set(o.node_ids) & open_nodes:
                    problems.append(f"нельзя сливать узлы из открытого конфликта: {o.node_ids}")
                missing = [i for i in o.node_ids if cv.get(i) is None]
                if missing:
                    problems.append(f"несуществующие id в merge_nodes: {missing}")
        return problems
    return check


def check_consolidate(cv: Canvas):
    def check(res: ConsolidateResult) -> list[str]:
        problems = []
        if len(res.ops) > 6:
            problems.append("не больше 6 операций")
        open_nodes = cv.open_conflict_nodes()
        for o in res.ops:
            if o.op not in ("merge_nodes", "move_node", "update_node", "set_essence"):
                problems.append(f"операция {o.op} Consolidator'у запрещена")
            if o.op == "merge_nodes" and set(o.node_ids) & open_nodes:
                problems.append(f"нельзя сливать узлы из открытого конфликта: {o.node_ids}")
        return problems
    return check
