"""Граф зависимостей: какие узлы держатся на изменившихся ограничениях."""
from .models import Canvas, Node


def affected(canvas: Canvas, changed: set[str]) -> list[Node]:
    hit: set[str] = set()
    frontier = set(changed)
    while frontier:                                   # узлы могут зависеть от узлов
        nxt = {n.id for n in canvas.nodes
               if n.id not in hit and n.id not in changed and n.status != "rejected"
               and frontier & set(n.depends_on)}
        hit |= nxt
        frontier = nxt
    return [n for n in canvas.nodes if n.id in hit]
