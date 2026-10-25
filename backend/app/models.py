from typing import Literal

from pydantic import BaseModel, Field

Section = Literal["essence", "problem", "audience", "solution",
                  "constraints", "risks", "questions"]
SECTIONS: list[str] = ["essence", "problem", "audience", "solution",
                       "constraints", "risks", "questions"]
SECTION_TITLES = {
    "essence": "Суть", "problem": "Проблема", "audience": "Для кого",
    "solution": "Решение", "constraints": "Ограничения", "risks": "Риски",
    "questions": "Открытые вопросы",
}
Status = Literal["new", "active", "disputed", "needs_review", "rejected"]
AgentStatus = Literal["idle", "reading", "thinking"]


# ---------- канвас ----------

class Node(BaseModel):
    id: str                          # N12
    section: Section
    parent_id: str | None = None     # None = тезис уровня 1, иначе деталь
    text: str                        # до 10 слов
    goal: str | None = None          # зачем это предлагают (для поиска общей цели)
    authors: list[str] = []          # имена из Telegram; "agent" — добавил бот
    source_msgs: list[int] = []      # seq сообщений-источников
    status: Status = "new"
    status_reason: str | None = None # почему rejected / needs_review
    depends_on: list[str] = []       # id ограничений, на которых держится
    tag: str | None = None           # свободная пометка, например "этап 2"
    revise_text: str | None = None   # формулировка, предложенная Impact
    version: int = 1


class Conflict(BaseModel):
    id: str                          # K3
    a: str
    b: str
    explanation: str
    question_id: str | None = None   # узел-вопрос в блоке questions
    status: Literal["open", "resolved"] = "open"
    resolution: str | None = None


class Canvas(BaseModel):
    version: int = 0
    nodes: list[Node] = []
    conflicts: list[Conflict] = []

    def get(self, node_id: str | None) -> Node | None:
        if not node_id:
            return None
        return next((n for n in self.nodes if n.id == node_id), None)

    def conflict(self, conflict_id: str | None) -> Conflict | None:
        return next((c for c in self.conflicts if c.id == conflict_id), None)

    def children(self, node_id: str) -> list[Node]:
        return [n for n in self.nodes if n.parent_id == node_id]

    def theses(self, section: str) -> list[Node]:
        return [n for n in self.nodes
                if n.section == section and n.parent_id is None and n.status != "rejected"]

    def open_conflict_nodes(self) -> set[str]:
        return {x for c in self.conflicts if c.status == "open" for x in (c.a, c.b)}


# ---------- тема и чат ----------

class Message(BaseModel):
    seq: int
    ts: float
    text: str
    author: str                      # имя из Telegram (first_name или @username)
    tg_user_id: int | None = None
    tg_message_id: int | None = None
    reply_to_seq: int | None = None  # если это реплай — контекст для Extractor
    is_agent: bool = False
    resolves_conflict: str | None = None  # реплай на вопрос бота по конфликту


class Topic(BaseModel):
    id: str                          # T3
    chat_id: int
    name: str                        # как написали в start
    key: str                         # name.casefold().strip() — для поиска
    web_token: str
    status: Literal["open", "closed"] = "open"
    opened_by: str = ""
    opened_at: float = 0
    closed_at: float | None = None
    summary: str | None = None
    messages: list[Message] = []
    canvas: Canvas = Canvas()
    counters: dict[str, int] = {}

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status,
                "opened_at": self.opened_at, "closed_at": self.closed_at,
                "summary": self.summary}

    def next_id(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}{self.counters[prefix]}"


class ChatState(BaseModel):
    chat_id: int
    open_topic_id: str | None = None
    topics: dict[str, str] = {}      # key -> topic_id
    last_bot_message_ts: float = 0   # для cooldown


# ---------- ответы LLM ----------

MsgLabel = Literal["idea", "requirement", "objection", "change",
                   "agreement", "question", "answer", "chatter"]


class Label(BaseModel):
    seq: int
    label: MsgLabel


class ExtractOp(BaseModel):
    op: Literal["add_node", "attach_source", "update_node", "change_constraint",
                "resolve_question", "add_dependency"]
    ref: str | None = None              # временный id нового узла
    section: Section | None = None
    parent_id: str | None = None        # существующий id или ref
    node_id: str | None = None          # для attach/update/change/resolve/dependency
    text: str | None = None             # текст узла / новое ограничение / ответ на вопрос
    goal: str | None = None
    constraint_id: str | None = None    # для add_dependency
    possible_conflict_with: list[str] = []
    source_msg: int
    author: str


class ExtractResult(BaseModel):
    labels: list[Label] = []
    ops: list[ExtractOp] = []


class CanvasOp(BaseModel):
    op: Literal["add_node", "merge_nodes", "move_node", "update_node", "set_essence",
                "add_conflict", "resolve_conflict", "set_status", "add_dependency"]
    ref: str | None = None
    section: Section | None = None
    parent_id: str | None = None
    node_id: str | None = None
    node_ids: list[str] = []            # merge_nodes
    merge_mode: Literal["group", "absorb"] = "group"  # group: общий тезис + дети; absorb: дубли в один
    text: str | None = None
    goal: str | None = None
    depends_on: list[str] = []
    a: str | None = None                # add_conflict
    b: str | None = None
    explanation: str | None = None
    question_text: str | None = None
    conflict_id: str | None = None      # resolve_conflict
    keep_ids: list[str] = []
    reject_ids: list[str] = []
    reason: str | None = None
    status: Status | None = None        # set_status
    tag: str | None = None


class ConsolidateResult(BaseModel):
    ops: list[CanvasOp] = []


class GoalItem(BaseModel):
    node_id: str
    wants: str


class MediatorResult(BaseModel):
    goals: list[GoalItem] = []
    same_underlying_goal: bool
    same_goal_reason: str = ""
    jointly_satisfiable: bool
    contested_resource: str | None = None
    verdict: Literal["same_goal", "complementary", "conflict", "misunderstanding"]
    confidence: float
    ops: list[CanvasOp] = []
    chat_message: str | None = None     # None = бот в чат не пишет
    toast: str | None = None


class ImpactItem(BaseModel):
    node_id: str
    decision: Literal["keep", "revise", "drop"]
    reason: str
    new_text: str | None = None


class ImpactResult(BaseModel):
    items: list[ImpactItem] = []
    chat_message: str | None = None


class SummaryResult(BaseModel):
    lines: list[str] = Field(default_factory=list)


class AnswerResult(BaseModel):
    answer: str
    node_ids: list[str] = []
