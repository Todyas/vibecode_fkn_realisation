# Модель данных и Patch-операции (по техдоку v2)

> Источник: `docs/project_materials/no_idea — техдок и спринты.pdf`.

---

## 1. Схемы Pydantic (`models.py`)

```python
from typing import Literal
from pydantic import BaseModel

Section = Literal[
    "essence",      # Суть (всегда 1 узел, меняет только set_essence)
    "problem",      # Проблема
    "audience",     # Для кого
    "solution",     # Решение
    "constraints",  # Ограничения (на них ссылаются depends_on других узлов)
    "risks",        # Риски
    "questions"     # Открытые вопросы
]

Status = Literal["new", "active", "disputed", "needs_review", "rejected"]

class Node(BaseModel):
    id: str                          # N12 (выдает сервер, а не LLM)
    section: Section
    parent_id: str | None = None     # None = тезис уровня 1 (до 10 слов), иначе деталь
    text: str                        # формулировка до 10 слов без воды
    goal: str | None = None          # зачем это предлагают (для канонизации целей)
    authors: list[str] = []          # кто высказал или поддержал; "agent" — предложил агент
    source_msgs: list[int] = []      # seq сообщений, из которых взят тезис
    status: Status = "new"
    status_reason: str | None = None # почему rejected / needs_review
    depends_on: list[str] = []       # id ограничений, на которых держится тезис
    pinned: bool = False             # правлен человеком — агент не трогает текст
    tag: str | None = None           # свободная пометка, например "этап 2"
    version: int = 1

class Conflict(BaseModel):
    id: str                          # K3
    a: str                           # id узла 1
    b: str                           # id узла 2
    explanation: str                 # за что конкурируют
    question_id: str                 # узел-вопрос в блоке questions
    status: Literal["open", "resolved"] = "open"
    resolution: str | None = None

class PlanStep(BaseModel):
    id: str
    text: str
    from_nodes: list[str]            # из каких тезисов вырос шаг
    owner: str | None = None         # если в чате кто-то взял на себя

class PlanStage(BaseModel):
    id: str
    title: str
    goal: str
    steps: list[PlanStep]

class Plan(BaseModel):
    stages: list[PlanStage]
    open_questions: list[str]        # id узлов-вопросов
    built_from_version: int          # с какой версией канваса собран

class Canvas(BaseModel):
    version: int = 0
    nodes: list[Node] = []
    conflicts: list[Conflict] = []
    plan: Plan | None = None
    mode: Literal["live", "plan"] = "live"

class Message(BaseModel):
    seq: int
    author: str
    role: str
    text: str
    ts: float
    is_agent: bool = False

class Idea(BaseModel):               # одна комната = одна идея
    id: str
    title: str
    created_at: float
    participants: dict[str, str] = {}   # user -> role
    messages: list[Message] = []
    canvas: Canvas = Canvas()
    counters: dict[str, int] = {}
```

---

## 2. Закрытый набор операций (`Reducer`)

Операции возвращают LLM и UI, а применяет детерминированный `Reducer`.  
Операция с несуществующим ID или нарушающая правила (правка `pinned`, глубина дерева `> 2`) отбрасывается с логированием.

| Операция | Поля | Кто вызывает | Назначение |
| :--- | :--- | :--- | :--- |
| `add_node` | `section`, `parent_id` / `parent_ref`, `text`, `goal`, `source_msg`, `possible_conflict_with` | Extractor, Mediator | Добавить новый тезис или деталь |
| `attach_source` | `node_id`, `source_msg`, `author` | Extractor | Зафиксировать повтор или поддержку (+1 к счетчику) |
| `update_node` | `node_id`, `text` | Extractor, Consolidator | Обновить текст (кроме pinned) |
| `change_constraint` | `node_id`, `new_text` | Extractor | Изменить существующее ограничение (триггерит Impact-анализ) |
| `resolve_question` | `question_id`, `answer`, `source_msg` | Extractor | Снять открытый вопрос ответом из чата |
| `merge_nodes` | `node_ids`, `new_text`, `goal` | Consolidator, Mediator | Слить тезисы (старые становятся детьми или объединяются) |
| `move_node` | `node_id`, `section`, `parent_id` | Consolidator | Переместить узел в правильный блок |
| `set_essence` | `text` | Consolidator | Обновить главную суть идеи (блок essence) |
| `add_conflict` | `a`, `b`, `explanation`, `question_text`, `options` | Mediator | Зафиксировать реальный конфликт (красная рамка + ребро) |
| `resolve_conflict` | `conflict_id`, `keep_ids`, `reject_ids`, `reason` | Mediator | Закрыть конфликт после выбора команды |
| `set_status` | `node_id`, `status`, `reason` | Mediator, Impact | Сменить статус (`needs_review`, `rejected` и др.) |
| `add_dependency` | `node_id`, `constraint_id` | Extractor, Mediator | Привязать тезис к ограничению |
| `set_plan` | `plan` | Planner | Зафиксировать построенный тезис-план |
| `edit_node` / `pin` / `unpin` | `node_id`, `text` | UI (человек, без LLM) | Ручная правка и закрепление человеком |
