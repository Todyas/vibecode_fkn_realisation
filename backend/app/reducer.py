"""Reducer: проверяет и применяет операции LLM, выдаёт id, собирает canvas_patch."""
import logging
from dataclasses import dataclass, field

from .models import (CanvasOp, Conflict, ConsolidateResult, ExtractResult, ImpactResult,
                     MediatorResult, Node, Topic)

log = logging.getLogger(__name__)
RESOLVED_TAG = "решено"


@dataclass
class Signals:
    conflicts: list[tuple[str, str]] = field(default_factory=list)       # (новый узел, другой)
    changed: list[tuple[str, str, str]] = field(default_factory=list)    # (id, было, стало)
    resolves: list[tuple[str, int]] = field(default_factory=list)        # (conflict_id, seq ответа)
    new_ids: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


class Patch:
    """Копит изменённые узлы/конфликты; to_ops() -> ops для canvas_patch."""

    def __init__(self, topic: Topic):
        self.topic = topic
        self.nodes: dict[str, None] = {}
        self.removed: list[str] = []
        self.conflicts: dict[str, None] = {}

    def touch(self, n: Node) -> None:
        if n.id not in self.nodes:
            n.version += 1
        self.nodes[n.id] = None

    def touch_conflict(self, c: Conflict) -> None:
        self.conflicts[c.id] = None

    def remove(self, node_id: str) -> None:
        cv = self.topic.canvas
        cv.nodes = [n for n in cv.nodes if n.id != node_id]
        self.nodes.pop(node_id, None)
        self.removed.append(node_id)

    def to_ops(self) -> list[dict]:
        cv = self.topic.canvas
        ops = [{"op": "remove_node", "id": i} for i in self.removed]
        ops += [{"op": "upsert_node", "node": n.model_dump()}
                for i in self.nodes if (n := cv.get(i)) is not None]
        ops += [{"op": "upsert_conflict", "conflict": c.model_dump()}
                for i in self.conflicts if (c := cv.conflict(i)) is not None]
        return ops


# ---------- общие помощники ----------

def _clean(text: str | None, limit: int = 120) -> str:
    return " ".join((text or "").split())[:limit]


def _add_unique(lst: list, x) -> None:
    if x is not None and x not in lst:
        lst.append(x)


def _resolve_ref(refs: dict[str, str], x: str | None) -> str | None:
    if not x:
        return None
    return refs.get(x, x)


def age_new(topic: Topic, p: Patch) -> None:
    """new -> active при следующей версии канваса."""
    for n in topic.canvas.nodes:
        if n.status == "new":
            n.status = "active"
            p.touch(n)


def _create_node(topic: Topic, p: Patch, *, section: str | None, parent_id: str | None,
                 text: str | None, goal: str | None, author: str | None, source: int | None,
                 depends_on: list[str] | None = None, status: str = "new",
                 tag: str | None = None) -> tuple[Node | None, str | None]:
    cv = topic.canvas
    text = _clean(text)
    if not text:
        return None, "empty text"
    parent = None
    if parent_id:
        parent = cv.get(parent_id)
        if parent is None:
            return None, f"parent {parent_id} not found"
        if parent.parent_id is not None:          # глубина > 2 -> вешаем на дедушку
            parent = cv.get(parent.parent_id)
        section = parent.section
    if section is None:
        return None, "no section"
    if section == "essence" and parent is None:
        ess = cv.theses("essence")
        if ess:                                    # «Суть» одна, меняет её только set_essence
            return None, "essence exists"
    # точный дубль в том же месте = поддержка
    for n in cv.nodes:
        if (n.section == section and n.parent_id == (parent.id if parent else None)
                and n.text.casefold() == text.casefold() and n.status != "rejected"):
            _add_unique(n.authors, author)
            _add_unique(n.source_msgs, source)
            p.touch(n)
            return n, None
    node = Node(id=topic.next_id("N"), section=section, parent_id=parent.id if parent else None,
                text=text, goal=_clean(goal, 60) or None,
                authors=[author] if author else [],
                source_msgs=[source] if source is not None else [],
                depends_on=[d for d in (depends_on or []) if cv.get(d)],
                status=status, tag=tag, version=0)
    cv.nodes.append(node)
    p.touch(node)
    return node, None


CONSTRAINT_KEYS = ("бюджет", "денег", "деньг", "срок", "дедлайн", "дорог", "ехать", "добира")


def similar_constraint(cv, text: str | None) -> Node | None:
    """Существующее ограничение про то же самое (бюджет, срок, дорога)."""
    t = (text or "").casefold()
    keys = [k for k in CONSTRAINT_KEYS if k in t]
    if not keys:
        return None
    for n in cv.theses("constraints"):
        if any(k in n.text.casefold() for k in keys):
            return n
    return None


def ensure_essence(topic: Topic) -> list[dict]:
    """Если LLM не заполнила «Суть», берём первый тезис решения или название темы."""
    cv = topic.canvas
    if cv.theses("essence") or not any(not m.is_agent for m in topic.messages):
        return []
    sol = cv.theses("solution")
    if not sol:
        return []
    p = Patch(topic)
    _create_node(topic, p, section="essence", parent_id=None, text=sol[0].text, goal=None,
                 author="agent", source=None)
    return p.to_ops()


# ---------- Extractor ----------

def apply_extract(topic: Topic, res: ExtractResult) -> tuple[list[dict], Signals]:
    cv = topic.canvas
    p, sig, refs = Patch(topic), Signals(), {}
    age_new(topic, p)
    valid_seqs = {m.seq for m in topic.messages}
    for op in res.ops:
        src = op.source_msg if op.source_msg in valid_seqs else None
        author = topic.messages[src].author if src is not None else op.author
        try:
            if op.op == "add_node" and op.section == "constraints" and not op.parent_id:
                same = similar_constraint(cv, op.text)
                if same is not None:           # «бюджет 3000» при «бюджет 5000» = смена ограничения
                    op = op.model_copy(update={"op": "change_constraint", "node_id": same.id})
            if op.op == "add_node":
                node, err = _create_node(topic, p, section=op.section,
                                         parent_id=_resolve_ref(refs, op.parent_id), text=op.text,
                                         goal=op.goal, author=author, source=src)
                if err:
                    sig.dropped.append(f"add_node: {err}")
                    continue
                if op.ref:
                    refs[op.ref] = node.id
                sig.new_ids.append(node.id)
                for other in op.possible_conflict_with:
                    other = _resolve_ref(refs, other)
                    if cv.get(other) and other != node.id:
                        sig.conflicts.append((node.id, other))
                continue
            node = cv.get(_resolve_ref(refs, op.node_id))
            if node is None:
                sig.dropped.append(f"{op.op}: no node {op.node_id}")
                continue
            if op.op == "attach_source":
                _add_unique(node.authors, author)
                _add_unique(node.source_msgs, src)
                p.touch(node)
            elif op.op == "update_node":
                if node.status == "rejected" or not _clean(op.text):
                    sig.dropped.append(f"update_node: {node.id} rejected/empty")
                    continue
                node.text = _clean(op.text)
                _add_unique(node.source_msgs, src)
                if node.status == "needs_review":       # люди ответили по узлу
                    node.status, node.status_reason, node.revise_text = "active", None, None
                p.touch(node)
            elif op.op == "change_constraint":
                if node.text.casefold() == _clean(op.text).casefold():
                    _add_unique(node.source_msgs, src)
                    p.touch(node)
                    continue
                if node.section != "constraints" or not _clean(op.text):
                    sig.dropped.append(f"change_constraint: {node.id} not a constraint")
                    continue
                old = node.text
                node.text = _clean(op.text)
                _add_unique(node.authors, author)
                _add_unique(node.source_msgs, src)
                node.status = "new"
                p.touch(node)
                sig.changed.append((node.id, old, node.text))
            elif op.op == "resolve_question":
                if node.section != "questions":
                    sig.dropped.append(f"resolve_question: {node.id} not a question")
                    continue
                for c in cv.conflicts:
                    if c.question_id == node.id and c.status == "open" and src is not None:
                        sig.resolves.append((c.id, src))
                if not any(c.question_id == node.id and c.status == "open" for c in cv.conflicts):
                    node.tag = RESOLVED_TAG
                    node.status_reason = _clean(op.text) or None
                    _add_unique(node.source_msgs, src)
                    p.touch(node)
            elif op.op == "assign":
                who = _clean(op.text, 40)
                if not who:
                    sig.dropped.append("assign: no owner")
                    continue
                node.tag = f"делает: {who}"
                _add_unique(node.source_msgs, src)
                p.touch(node)
            elif op.op == "add_dependency":
                c = cv.get(_resolve_ref(refs, op.constraint_id))
                if c is None or c.section != "constraints" or c.id == node.id:
                    sig.dropped.append(f"add_dependency: bad constraint {op.constraint_id}")
                    continue
                _add_unique(node.depends_on, c.id)
                p.touch(node)
        except Exception as e:  # noqa: BLE001
            sig.dropped.append(f"{op.op}: {e}")
    if sig.dropped:
        log.info("extract dropped: %s", sig.dropped)
    return p.to_ops(), sig


# ---------- операции Mediator / Consolidator ----------

def _merge(topic: Topic, p: Patch, op: CanvasOp, author_default: str) -> str | None:
    cv = topic.canvas
    ids = [i for i in dict.fromkeys(op.node_ids)]
    nodes = [cv.get(i) for i in ids]
    if len(ids) < 2 or any(n is None for n in nodes):
        return "merge_nodes: need >=2 existing nodes"
    if {n.section for n in nodes} != {nodes[0].section}:
        return "merge_nodes: different sections"
    if set(ids) & cv.open_conflict_nodes():
        return "merge_nodes: node in open conflict"
    if any(n.status == "rejected" for n in nodes):
        return "merge_nodes: rejected node"
    if op.merge_mode == "absorb":
        keep, rest = nodes[0], nodes[1:]
        if any(c for c in cv.conflicts if {c.a, c.b} & set(ids[1:])):
            return "merge_nodes: absorbed node has conflict history"
        for n in rest:
            for a in n.authors:
                _add_unique(keep.authors, a)
            for s in n.source_msgs:
                _add_unique(keep.source_msgs, s)
            for d in n.depends_on:
                _add_unique(keep.depends_on, d)
            for ch in cv.children(n.id):
                ch.parent_id = keep.id if keep.parent_id is None else keep.parent_id
                p.touch(ch)
            for other in cv.nodes:
                if n.id in other.depends_on:
                    other.depends_on = [keep.id if d == n.id else d for d in other.depends_on]
                    p.touch(other)
            p.remove(n.id)
        if _clean(op.text):
            keep.text = _clean(op.text)
        if op.goal:
            keep.goal = _clean(op.goal, 60)
        p.touch(keep)
        return None
    # group: общий тезис-цель сверху, исходные узлы — его дети
    text = _clean(op.text)
    if not text:
        return "merge_nodes: empty text"
    parent = Node(id=topic.next_id("N"), section=nodes[0].section,
                  parent_id=None, text=text, goal=_clean(op.goal, 60) or nodes[0].goal,
                  status="new", version=0)
    for n in nodes:
        for a in n.authors:
            _add_unique(parent.authors, a)
        for s in n.source_msgs:
            _add_unique(parent.source_msgs, s)
        for d in n.depends_on:
            _add_unique(parent.depends_on, d)
    cv.nodes.append(parent)
    p.touch(parent)
    for n in nodes:
        for ch in cv.children(n.id):      # глубина 2: внуки поднимаются к новому тезису
            ch.parent_id = parent.id
            p.touch(ch)
        n.parent_id = parent.id
        if (n.goal or "").casefold().strip() == (parent.goal or "").casefold().strip():
            n.goal = None
        p.touch(n)
    return None


def _move(topic: Topic, p: Patch, op: CanvasOp) -> str | None:
    cv = topic.canvas
    n = cv.get(op.node_id)
    if n is None:
        return "move_node: no node"
    if op.parent_id:
        par = cv.get(op.parent_id)
        if par is None or par.parent_id is not None or par.id == n.id:
            return "move_node: bad parent"
        if cv.children(n.id):
            return "move_node: node with children can't become a detail"
        n.parent_id, n.section = par.id, par.section
    else:
        if op.section is None:
            return "move_node: no section"
        n.parent_id, n.section = None, op.section
        for ch in cv.children(n.id):
            ch.section = op.section
            p.touch(ch)
    p.touch(n)
    return None


def _set_essence(topic: Topic, p: Patch, text: str | None) -> str | None:
    text = _clean(text, 160)
    if not text:
        return "set_essence: empty"
    ess = topic.canvas.theses("essence")
    if ess:
        ess[0].text = text
        p.touch(ess[0])
    else:
        _create_node(topic, p, section="essence", parent_id=None, text=text, goal=None,
                     author="agent", source=None)
    return None


def apply_canvas_ops(topic: Topic, ops: list[CanvasOp], *, author: str = "agent",
                     source: int | None = None) -> tuple[Patch, list[str], list[str]]:
    """-> (patch, ошибки, id созданных конфликтов)."""
    cv = topic.canvas
    p, errors, new_conflicts, refs = Patch(topic), [], [], {}
    for op in ops:
        err = None
        if op.op == "add_node":
            node, err = _create_node(topic, p, section=op.section,
                                     parent_id=_resolve_ref(refs, op.parent_id), text=op.text,
                                     goal=op.goal, author=author, source=source,
                                     depends_on=[_resolve_ref(refs, d) for d in op.depends_on],
                                     tag=op.tag)
            if node and op.ref:
                refs[op.ref] = node.id
        elif op.op == "merge_nodes":
            op.node_ids = [_resolve_ref(refs, i) for i in op.node_ids]
            err = _merge(topic, p, op, author)
        elif op.op == "move_node":
            err = _move(topic, p, op)
        elif op.op == "update_node":
            n = cv.get(_resolve_ref(refs, op.node_id))
            if n is None or not _clean(op.text):
                err = "update_node: no node/text"
            else:
                n.text = _clean(op.text)
                if op.tag is not None:
                    n.tag = op.tag or None
                p.touch(n)
        elif op.op == "set_essence":
            err = _set_essence(topic, p, op.text)
        elif op.op == "add_conflict":
            a, b = cv.get(_resolve_ref(refs, op.a)), cv.get(_resolve_ref(refs, op.b))
            if a is None or b is None or a.id == b.id:
                err = "add_conflict: bad nodes"
            elif any(c.status == "open" and {c.a, c.b} == {a.id, b.id} for c in cv.conflicts):
                err = "add_conflict: already open"
            else:
                q, _ = _create_node(topic, p, section="questions", parent_id=None,
                                    text=op.question_text or f"Что важнее: {a.text} или {b.text}?",
                                    goal=None, author="agent", source=None)
                c = Conflict(id=topic.next_id("K"), a=a.id, b=b.id,
                             explanation=_clean(op.explanation, 200) or "",
                             question_id=q.id if q else None)
                cv.conflicts.append(c)
                p.touch_conflict(c)
                for n in (a, b):
                    n.status = "disputed"
                    p.touch(n)
                new_conflicts.append(c.id)
        elif op.op == "resolve_conflict":
            c = cv.conflict(op.conflict_id)
            if c is None or c.status != "open":
                err = "resolve_conflict: no open conflict"
            else:
                reason = _clean(op.reason, 200) or None
                rejects = set(op.reject_ids) & {c.a, c.b} or (set(op.reject_ids) & {n.id for n in cv.nodes})
                for i in rejects:
                    n = cv.get(i)
                    if n:
                        n.status, n.status_reason = "rejected", reason
                        p.touch(n)
                for i in {c.a, c.b} - rejects:
                    n = cv.get(i)
                    if n and n.status == "disputed":
                        n.status, n.status_reason = "active", None
                        p.touch(n)
                c.status, c.resolution = "resolved", reason
                p.touch_conflict(c)
                q = cv.get(c.question_id)
                if q:
                    q.tag, q.status_reason = RESOLVED_TAG, reason
                    p.touch(q)
        elif op.op == "set_status":
            n = cv.get(_resolve_ref(refs, op.node_id))
            if n is None or op.status is None:
                err = "set_status: bad node/status"
            elif n.id in cv.open_conflict_nodes() and op.status not in ("disputed", "rejected"):
                err = "set_status: node in open conflict"
            else:
                n.status, n.status_reason = op.status, _clean(op.reason, 200) or None
                if op.tag is not None:
                    n.tag = op.tag or None
                p.touch(n)
        elif op.op == "add_dependency":
            n = cv.get(_resolve_ref(refs, op.node_id))
            dep = cv.get(op.depends_on[0] if op.depends_on else op.parent_id)
            if n is None or dep is None or dep.section != "constraints":
                err = "add_dependency: bad nodes"
            else:
                _add_unique(n.depends_on, dep.id)
                p.touch(n)
        if err:
            errors.append(err)
    if errors:
        log.info("canvas ops dropped: %s", errors)
    return p, errors, new_conflicts


def apply_mediator(topic: Topic, res: MediatorResult, *, source: int | None = None,
                   resolver: str | None = None) -> tuple[list[dict], list[str]]:
    author = resolver or "agent"
    p, _, conflicts = apply_canvas_ops(topic, res.ops, author=author, source=source)
    return p.to_ops(), conflicts


def apply_consolidate(topic: Topic, res: ConsolidateResult) -> list[dict]:
    allowed = [o for o in res.ops if o.op in ("merge_nodes", "move_node", "update_node", "set_essence")]
    p, _, _ = apply_canvas_ops(topic, allowed[:6])
    return p.to_ops()


def mark_needs_review(topic: Topic, node_ids: list[str], reason: str) -> list[dict]:
    p = Patch(topic)
    for i in node_ids:
        n = topic.canvas.get(i)
        if n and n.status not in ("rejected", "disputed"):
            n.status, n.status_reason = "needs_review", reason
            p.touch(n)
    return p.to_ops()


def apply_impact(topic: Topic, res: ImpactResult, affected: set[str]) -> list[dict]:
    cv = topic.canvas
    p = Patch(topic)
    for it in res.items:
        n = cv.get(it.node_id)
        if n is None or n.id not in affected:
            continue
        if it.decision == "keep":
            n.status, n.status_reason, n.revise_text = "active", None, None
        elif it.decision == "revise":
            n.status_reason = _clean(it.reason, 200)
            n.revise_text = _clean(it.new_text) or None
        else:
            n.status_reason = _clean(it.reason, 200)
            _create_node(topic, p, section="questions", parent_id=None,
                         text=f"Оставляем «{n.text}»?", goal=None, author="agent", source=None)
        p.touch(n)
    return p.to_ops()
