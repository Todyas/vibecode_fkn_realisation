# Модель данных и Patch-операции

## Схемы Pydantic (`models.py`)

```python
from typing import Literal
from pydantic import BaseModel, Field

ItemKind = Literal["proposal", "constraint", "objection", "question"]

class Goal(BaseModel):
    id: str                         # G1, G2...
    text: str                       # "повысить конверсию в покупку"
    is_business_goal: bool = False  # главная цель задачи

class Item(BaseModel):
    id: str                         # P1, C1, O1, Q1
    kind: ItemKind
    author: str                     # имя или роль
    text: str                       # короткая нормализованная формулировка (до 10 слов)
    goal_id: str | None = None      # ссылка на Goal.id
    means: str | None = None        # как именно предлагает достичь
    status: Literal["active", "merged", "resolved", "dropped"] = "active"
    source_msgs: list[int] = []     # из каких сообщений вытащено
    version: int = 1

class Relation(BaseModel):
    id: str                         # R1...
    a: str; b: str                  # id карточек
    type: Literal["same_goal", "complementary", "conflict", "duplicate"]
    explanation: str                # одно предложение для тултипа на ребре
    confidence: float = Field(ge=0, le=1)
    status: Literal["suggested", "confirmed", "dismissed"] = "suggested"

class Decision(BaseModel):
    id: str                         # D1...
    text: str
    rationale: str                  # почему выбрали
    covers: dict[str, str]          # item_id -> как учтено
    sacrifices: dict[str, str]       # item_id -> чем пожертвовали и почему
    depends_on: list[str]           # id ограничений и карточек, на которых держится
    status: Literal["proposed", "accepted", "needs_review", "superseded"] = "proposed"
    votes: dict[str, bool] = {}     # user -> за/против
    history: list[str] = []

class Message(BaseModel):
    seq: int
    author: str
    role: str
    text: str
    ts: float
    is_agent: bool = False

class RoomState(BaseModel):
    room_id: str
    version: int = 0
    goals: list[Goal] = []
    items: list[Item] = []
    relations: list[Relation] = []
    decisions: list[Decision] = []
    participants: dict[str, str] = {}
    counters: dict[str, int] = {}
```

## Patch Operations (Закрытый набор операций редьюсера)

| Операция | Поля | Кто вызывает |
| :--- | :--- | :--- |
| `add_goal` | `text`, `is_business_goal` | Extractor |
| `add_item` | `kind`, `text`, `goal_id` / `new_goal`, `means` | Extractor |
| `attach_to_item` | `item_id` (повтор сказанного) | Extractor |
| `update_item` | `item_id`, `text` и/или `means` | Extractor |
| `change_constraint` | `item_id`, `new_text` | Extractor |
| `add_relation` | `a`, `b`, `type`, `explanation`, `confidence` | Extractor / Mediator |
| `propose_decision` | `text`, `rationale`, `covers`, `sacrifices`, `depends_on` | Mediator |
| `add_question` | `text`, `options`, `related_ids` | Mediator |
| `resolve_question` | `question_id`, `answer` | Extractor |
| `cast_vote` | `decision_id`, `user`, `value` | Клиент (без LLM) |
| `mark_needs_review` | `decision_id`, `reason` | Impact-анализ (без LLM) |
