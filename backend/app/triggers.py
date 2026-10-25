"""Trigger policy: когда вмешиваться решают правила на Python."""
from dataclasses import dataclass, field

from . import config, impact
from .models import Canvas
from .reducer import Signals

PRIORITY = {"IMPACT": 1, "RESOLVE": 2, "CONFLICT": 2, "SAME_GOAL": 3, "CONSOLIDATE": 4}


@dataclass
class Trigger:
    kind: str
    focus_ids: list[str]
    key: str
    conflict_id: str | None = None
    changes: list[tuple[str, str, str]] = field(default_factory=list)  # IMPACT: (id, было, стало)
    source_seq: int | None = None    # сообщение-причина (для реплая бота)

    @property
    def priority(self) -> int:
        return PRIORITY[self.kind]


def _goal_key(g: str | None) -> str:
    return " ".join((g or "").casefold().replace("ё", "е").split())


def same_goal_groups(cv: Canvas) -> list[list[str]]:
    """≥2 активных тезиса разных авторов в solution с одним goal, ещё не сведены."""
    groups: dict[str, list] = {}
    blocked = cv.open_conflict_nodes()
    for n in cv.nodes:
        if (n.section == "solution" and n.goal and n.status in ("new", "active")
                and n.id not in blocked and "agent" not in n.authors):
            groups.setdefault(_goal_key(n.goal), []).append(n)
    out = []
    for key, nodes in groups.items():
        # дети под тезисом с той же целью уже сведены
        nodes = [n for n in nodes
                 if not ((p := cv.get(n.parent_id)) and _goal_key(p.goal) == key)]
        if len(nodes) < 2:
            continue
        if len({n.parent_id for n in nodes}) == 1 and nodes[0].parent_id is not None:
            continue                                   # уже под общим тезисом
        authors = {a for n in nodes for a in n.authors}
        if len(authors) < 2:
            continue
        out.append(sorted(n.id for n in nodes))
    return out


def collect(room, sig: Signals, batch) -> None:
    """Добавить в room.pending новые триггеры из сигналов Reducer."""
    cv: Canvas = room.canvas
    seq_of = {m.seq for m in batch}
    last_seq = batch[-1].seq if batch else None
    changed = [c for c in sig.changed]
    if changed:
        hit = impact.affected(cv, {c[0] for c in changed})
        if hit:
            room.pending.append(Trigger("IMPACT", [c[0] for c in changed] + [n.id for n in hit],
                                        key="IMPACT:" + ",".join(c[0] for c in changed) + f"@{last_seq}",
                                        changes=changed, source_seq=last_seq))
    # реплай на вопрос бота
    for m in batch:
        if m.resolves_conflict and (c := cv.conflict(m.resolves_conflict)) and c.status == "open":
            sig.resolves.append((c.id, m.seq))
    seen = set()
    for cid, seq in sig.resolves:
        if cid in seen:
            continue
        seen.add(cid)
        c = cv.conflict(cid)
        if c and c.status == "open":
            room.pending.append(Trigger("RESOLVE", [c.a, c.b], key=f"RESOLVE:{cid}",
                                        conflict_id=cid, source_seq=seq))
    for new_id, other in sig.conflicts:
        key = "CONFLICT:" + ",".join(sorted([new_id, other]))
        if key in room.done_keys:
            continue
        n = cv.get(new_id)
        src = n.source_msgs[-1] if n and n.source_msgs and n.source_msgs[-1] in seq_of else last_seq
        room.pending.append(Trigger("CONFLICT", [new_id, other], key=key, source_seq=src))
    for ids in same_goal_groups(cv):
        key = "SAME_GOAL:" + ",".join(ids)
        if key not in room.done_keys:
            room.pending.append(Trigger("SAME_GOAL", ids, key=key, source_seq=last_seq))
    over = any(len(cv.theses(s)) > config.MAX_THESES_PER_BLOCK for s in
               ("problem", "audience", "solution", "constraints", "risks", "questions"))
    if room.human_since_consolidate >= config.CONSOLIDATE_EVERY or over:
        room.pending.append(Trigger("CONSOLIDATE", [], key=f"CONSOLIDATE@{last_seq}"))


def pick(room) -> Trigger | None:
    """Самый приоритетный триггер; дубли по key выкидываем."""
    uniq: dict[str, Trigger] = {}
    for t in room.pending:
        if t.key not in room.done_keys:
            uniq.setdefault(t.key, t)
    pend = sorted(uniq.values(), key=lambda t: t.priority)
    if not pend:
        room.pending = []
        return None
    best = pend[0]
    room.pending = pend[1:]
    return best


def mark_done(room, trig: Trigger) -> None:
    room.done_keys.add(trig.key)
    if trig.kind == "CONSOLIDATE":
        room.human_since_consolidate = 0
        room.pending = [t for t in room.pending if t.kind != "CONSOLIDATE"]
