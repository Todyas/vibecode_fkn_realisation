import type { AgentStatus, Canvas, Message, Op, ServerEvent, Snapshot, TopicInfo } from "./types";

export interface Toast { id: number; text: string; focus_ids: string[] }

export interface State {
  loaded: boolean;
  error: string | null;
  topic: TopicInfo | null;
  messages: Message[];
  canvas: Canvas;
  agent: AgentStatus;
  toasts: Toast[];
  // id узла -> момент последнего изменения (для подсветки 3 с)
  touched: Record<string, number>;
  needsResync: boolean;
}

export const initialState: State = {
  loaded: false,
  error: null,
  topic: null,
  messages: [],
  canvas: { version: 0, nodes: [], conflicts: [] },
  agent: "idle",
  toasts: [],
  touched: {},
  needsResync: false,
};

export type Action =
  | { type: "server"; event: ServerEvent }
  | { type: "loaded"; snapshot: Snapshot }
  | { type: "error"; error: string }
  | { type: "dismiss_toast"; id: number }
  | { type: "resync_started" };

let toastSeq = 0;

function applyOps(canvas: Canvas, ops: Op[], version: number, touched: Record<string, number>) {
  const nodes = new Map(canvas.nodes.map((n) => [n.id, n]));
  const conflicts = new Map(canvas.conflicts.map((c) => [c.id, c]));
  const now = Date.now();
  const t = { ...touched };
  for (const op of ops) {
    if (op.op === "upsert_node") {
      const prev = nodes.get(op.node.id);
      if (!prev || prev.text !== op.node.text || prev.status !== op.node.status ||
          prev.authors.length !== op.node.authors.length) t[op.node.id] = now;
      nodes.set(op.node.id, op.node);
    } else if (op.op === "remove_node") {
      nodes.delete(op.id);
    } else if (op.op === "upsert_conflict") {
      conflicts.set(op.conflict.id, op.conflict);
    }
  }
  return {
    canvas: { version, nodes: [...nodes.values()], conflicts: [...conflicts.values()] },
    touched: t,
  };
}

function fromSnapshot(state: State, s: Snapshot): State {
  return {
    ...state,
    loaded: true,
    error: null,
    topic: s.topic,
    messages: s.messages,
    canvas: s.canvas,
    agent: s.agent_status ?? "idle",
    needsResync: false,
  };
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "loaded":
      return fromSnapshot(state, action.snapshot);
    case "error":
      return { ...state, error: action.error };
    case "dismiss_toast":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    case "resync_started":
      return { ...state, needsResync: false };
    case "server": {
      const e = action.event;
      switch (e.type) {
        case "snapshot":
          return fromSnapshot(state, e);
        case "message":
          if (state.messages.some((m) => m.seq === e.message.seq)) return state;
          return { ...state, messages: [...state.messages, e.message] };
        case "canvas_patch": {
          if (e.version <= state.canvas.version) return state; // дубль
          if (e.version !== state.canvas.version + 1) return { ...state, needsResync: true };
          return { ...state, ...applyOps(state.canvas, e.ops, e.version, state.touched) };
        }
        case "toast":
          return {
            ...state,
            toasts: [...state.toasts, { id: ++toastSeq, text: e.text, focus_ids: e.focus_ids ?? [] }].slice(-4),
          };
        case "agent_status":
          return { ...state, agent: e.status };
        case "topic_status":
          if (!state.topic) return state;
          return { ...state, topic: { ...state.topic, status: e.status, summary: e.summary ?? state.topic.summary } };
      }
    }
  }
  return state;
}
