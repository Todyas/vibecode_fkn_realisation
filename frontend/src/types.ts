// Типы по контракту /mnt/project-files/no_idea/api-contract.md (v1)
export type Section =
  | "essence" | "problem" | "audience" | "solution"
  | "constraints" | "risks" | "questions";
export type Status = "new" | "active" | "disputed" | "needs_review" | "rejected";

export interface Node {
  id: string;
  section: Section;
  parent_id: string | null;
  text: string;
  goal: string | null;
  authors: string[];
  source_msgs: number[];
  status: Status;
  status_reason: string | null;
  depends_on: string[];
  tag: string | null;
  revise_text?: string | null;
  version: number;
}

export interface Conflict {
  id: string;
  a: string;
  b: string;
  explanation: string;
  question_id: string | null;
  status: "open" | "resolved";
  resolution: string | null;
}

export interface Canvas {
  version: number;
  nodes: Node[];
  conflicts: Conflict[];
}

export interface Message {
  seq: number;
  ts: number;
  text: string;
  author: string;
  is_agent: boolean;
  reply_to_seq: number | null;
}

export interface TopicInfo {
  id: string;
  name: string;
  status: "open" | "closed";
  opened_at: number;
  closed_at: number | null;
  summary: string | null;
}

export type AgentStatus = "idle" | "reading" | "thinking";

export interface Snapshot {
  topic: TopicInfo;
  messages: Message[];
  canvas: Canvas;
  agent_status: AgentStatus;
}

export type Op =
  | { op: "upsert_node"; node: Node }
  | { op: "remove_node"; id: string }
  | { op: "upsert_conflict"; conflict: Conflict };

export type ServerEvent =
  | ({ type: "snapshot" } & Snapshot)
  | { type: "message"; message: Message }
  | { type: "canvas_patch"; version: number; ops: Op[] }
  | { type: "toast"; text: string; focus_ids: string[] }
  | { type: "agent_status"; status: AgentStatus }
  | { type: "topic_status"; status: "open" | "closed"; summary: string | null };

export const SECTIONS: { key: Section; title: string; hint: string }[] = [
  { key: "essence", title: "Суть", hint: "На чём сейчас стоит команда" },
  { key: "problem", title: "Проблема", hint: "Боль, ради которой всё затевается" },
  { key: "audience", title: "Для кого", hint: "Аудитория и сегменты" },
  { key: "constraints", title: "Ограничения", hint: "Бюджет, сроки, люди" },
  { key: "solution", title: "Решение", hint: "Предложения и способы" },
  { key: "risks", title: "Риски", hint: "Опасения и возражения" },
  { key: "questions", title: "Открытые вопросы", hint: "Что ещё не решили" },
];
