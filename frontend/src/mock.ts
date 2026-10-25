// Мок бэкенда: проигрывает сценарий demo_shop из техдока прямо в браузере.
// Включается на /t/demo или с ?mock=1. ?upto=N — сразу показать состояние после реплики N.
import type { Canvas, Conflict, Message, Node, Op, ServerEvent, Snapshot } from "./types";

type Emit = (e: ServerEvent) => void;

class MockServer {
  canvas: Canvas = { version: 0, nodes: [], conflicts: [] };
  messages: Message[] = [];
  seq = 0;
  status: "open" | "closed" = "open";
  summary: string | null = null;
  nid = 0;

  constructor(public emit: Emit) {}

  snapshot(): Snapshot {
    return {
      topic: { id: "T1", name: "Продажи", status: this.status, opened_at: Date.now() / 1000 - 600,
               closed_at: this.status === "closed" ? Date.now() / 1000 : null, summary: this.summary },
      messages: this.messages,
      canvas: this.canvas,
      agent_status: "idle",
    };
  }

  say(author: string, text: string, is_agent = false, reply_to_seq: number | null = null) {
    const m: Message = { seq: ++this.seq, ts: Date.now() / 1000, text, author, is_agent, reply_to_seq };
    this.messages.push(m);
    this.emit({ type: "message", message: m });
    return m.seq;
  }

  patch(ops: Op[]) {
    const byId = new Map(this.canvas.nodes.map((n) => [n.id, n]));
    const conf = new Map(this.canvas.conflicts.map((c) => [c.id, c]));
    for (const op of ops) {
      if (op.op === "upsert_node") byId.set(op.node.id, op.node);
      if (op.op === "remove_node") byId.delete(op.id);
      if (op.op === "upsert_conflict") conf.set(op.conflict.id, op.conflict);
    }
    this.canvas = { version: this.canvas.version + 1, nodes: [...byId.values()], conflicts: [...conf.values()] };
    this.emit({ type: "canvas_patch", version: this.canvas.version, ops });
  }

  node(id: string) { return this.canvas.nodes.find((n) => n.id === id)!; }

  add(p: Partial<Node> & Pick<Node, "section" | "text">): Op {
    const n: Node = {
      id: p.id ?? `N${++this.nid}`, parent_id: null, goal: null, authors: [], source_msgs: [],
      status: "new", status_reason: null, depends_on: [], tag: null, revise_text: null, version: 1, ...p,
    };
    return { op: "upsert_node", node: n };
  }

  upd(id: string, p: Partial<Node>): Op {
    const n = this.node(id);
    return { op: "upsert_node", node: { ...n, ...p, version: n.version + 1 } };
  }

  conflict(c: Conflict): Op { return { op: "upsert_conflict", conflict: c }; }
}

type Step = (s: MockServer) => void;

// Ids фиксированы, чтобы шаги могли ссылаться друг на друга.
const STEPS: Step[] = [
  (s) => {
    const m = s.say("Руководитель", "Хотим поднять продажи на 15% за квартал, большого бюджета нет");
    s.patch([
      s.add({ id: "N1", section: "essence", text: "+15% продаж за квартал", authors: ["Руководитель"], source_msgs: [m] }),
      s.add({ id: "N2", section: "constraints", text: "Бюджет небольшой", authors: ["Руководитель"], source_msgs: [m] }),
    ]);
  },
  (s) => {
    const m = s.say("Маркетолог", "Люди уходят без покупки, нужны персональные предложения");
    s.patch([
      s.upd("N1", { status: "active" }), s.upd("N2", { status: "active" }),
      s.add({ id: "N3", section: "problem", text: "Уходят до покупки", authors: ["Маркетолог"], source_msgs: [m] }),
      s.add({ id: "N4", section: "solution", text: "Персональные предложения", goal: "довести до покупки",
              authors: ["Маркетолог"], source_msgs: [m] }),
    ]);
  },
  (s) => {
    const m = s.say("Дизайнер", "Давайте сократим путь до покупки, сейчас 5 шагов оформления");
    const n4 = s.node("N4");
    s.patch([
      s.upd("N3", { status: "active", authors: ["Маркетолог", "Дизайнер"], source_msgs: [2, m] }),
      s.add({ id: "N5", section: "solution", text: "Довести до покупки", goal: "довести до покупки",
              authors: ["Маркетолог", "Дизайнер"], source_msgs: [2, m] }),
      { op: "upsert_node", node: { ...n4, parent_id: "N5", status: "active", version: n4.version + 1 } },
      s.add({ id: "N6", section: "solution", parent_id: "N5", text: "Оформление в 2 шага вместо 5",
              authors: ["Дизайнер"], source_msgs: [m] }),
    ]);
    s.emit({ type: "toast", text: "Свёл 2 тезиса — цель одна: довести до покупки", focus_ids: ["N5"] });
  },
  (s) => {
    const m = s.say("Разработчик", "Сложные рекомендации не потянем, нас двое и месяц");
    s.patch([
      s.upd("N5", { status: "active" }), s.upd("N6", { status: "active" }),
      s.add({ id: "N7", section: "constraints", text: "2 разработчика, месяц", authors: ["Разработчик"], source_msgs: [m] }),
      s.add({ id: "N8", section: "solution", parent_id: "N5", text: "Готовые наборы по простым правилам",
              authors: ["agent"], source_msgs: [m], depends_on: ["N7"] }),
    ]);
    s.say("no_idea", "Персональные предложения можно сделать готовыми наборами по простым правилам, без ML. Добавил на канвас как вариант.", true, m);
  },
  (s) => {
    const m = s.say("Маркетолог", "И нужна рассылка со скидкой 20% всем, чтобы вернуть старых клиентов");
    s.patch([
      s.upd("N7", { status: "active" }), s.upd("N8", { status: "active" }),
      s.add({ id: "N9", section: "audience", text: "Старые клиенты", authors: ["Маркетолог"], source_msgs: [m] }),
      s.add({ id: "N10", section: "solution", text: "Рассылка со скидкой 20%", authors: ["Маркетолог"],
              source_msgs: [m], status: "disputed" }),
      s.upd("N2", { status: "disputed" }),
      s.add({ id: "N11", section: "questions", text: "Что важнее: вернуть клиентов или маржа?",
              authors: ["agent"], source_msgs: [m] }),
      s.add({ id: "N12", section: "risks", text: "Скидка съест маржу", authors: ["agent"], source_msgs: [m] }),
      s.conflict({ id: "K1", a: "N10", b: "N2", explanation: "Скидка 20% бьёт по марже при маленьком бюджете",
                   question_id: "N11", status: "open", resolution: null }),
    ]);
    s.say("no_idea", "Скидка 20% бьёт по марже, а бюджет ограничен. Что важнее: вернуть старых клиентов или сохранить маржу?", true, m);
  },
  (s) => {
    const m = s.say("Руководитель", "Маржа важнее. Скидка максимум 5% и только на наборы", false, s.seq);
    s.patch([
      s.upd("N10", { status: "rejected", status_reason: "Руководитель: маржа важнее" }),
      s.upd("N2", { status: "active" }),
      s.upd("N11", { status: "rejected", status_reason: "маржа важнее (Руководитель)" }),
      s.upd("N9", { status: "active" }), s.upd("N12", { status: "active" }),
      s.add({ id: "N13", section: "solution", parent_id: "N5", text: "Скидка 5% только на наборы",
              authors: ["Руководитель"], source_msgs: [m], depends_on: ["N2"] }),
      s.conflict({ id: "K1", a: "N10", b: "N2", explanation: "Скидка 20% бьёт по марже при маленьком бюджете",
                   question_id: "N11", status: "resolved", resolution: "Маржа важнее" }),
    ]);
  },
  (s) => {
    const m = s.say("Руководитель", "Новости: бюджет дали, но срок — 2 недели вместо месяца");
    s.patch([
      s.upd("N13", { status: "needs_review", status_reason: "Держалась на нехватке бюджета",
                     revise_text: "Можно вернуть скидку побольше" }),
      s.upd("N8", { status: "needs_review", status_reason: "За 2 недели наборы под вопросом" }),
      s.upd("N2", { text: "Бюджет выделен", source_msgs: [1, m], status: "active" }),
      s.upd("N7", { text: "2 разработчика, 2 недели", source_msgs: [4, m] }),
    ]);
    s.say("no_idea", "Изменились бюджет и срок. Скидка 5% держалась на нехватке бюджета — можно пересмотреть. Наборы за 2 недели под вопросом: начать с оформления?", true, m);
  },
  (s) => {
    const m = s.say("Дизайнер", "Да, сначала оформление, наборы вторым этапом");
    s.patch([
      s.upd("N8", { status: "active", status_reason: null, tag: "этап 2", source_msgs: [4, m] }),
      s.upd("N6", { tag: "этап 1", authors: ["Дизайнер", "Руководитель"] }),
      s.upd("N1", { text: "+15% продаж: сначала оформление, потом наборы" }),
    ]);
  },
];

export function connectMock(emit: Emit, opts: { upto: number | null; delayMs: number }) {
  const s = new MockServer(emit);
  const quiet = new MockServer(() => {});
  const upto = opts.upto ?? 0;
  // Состояние «до реплики N» собираем без событий и отдаём снапшотом.
  for (let i = 0; i < Math.min(upto, STEPS.length); i++) STEPS[i](quiet);
  Object.assign(s, { canvas: quiet.canvas, messages: quiet.messages, seq: quiet.seq });
  if (opts.upto === 99) { s.status = "closed"; s.summary = "Сначала оформление в 2 шага, потом наборы."; }
  emit({ type: "snapshot", ...s.snapshot() });

  const timers: number[] = [];
  let t = 800;
  for (let i = upto; i < STEPS.length; i++) {
    const step = STEPS[i];
    timers.push(window.setTimeout(() => emit({ type: "agent_status", status: "reading" }), t));
    timers.push(window.setTimeout(() => emit({ type: "agent_status", status: "thinking" }), t + 600));
    timers.push(window.setTimeout(() => { step(s); emit({ type: "agent_status", status: "idle" }); }, t + 1800));
    t += opts.delayMs;
  }
  timers.push(window.setTimeout(() => {
    if (upto >= STEPS.length) return;
    s.status = "closed";
    s.summary = "Цель: +15% продаж. Сначала оформление в 2 шага, наборы — этап 2. Скидку 20% отклонили: маржа важнее.";
    emit({ type: "topic_status", status: "closed", summary: s.summary });
  }, t + 1500));
  return () => timers.forEach(clearTimeout);
}

export function mockSnapshot(upto: number): Snapshot {
  let snap: Snapshot | null = null;
  connectMock((e) => { if (e.type === "snapshot") snap = e; }, { upto, delayMs: 0 })();
  return snap!;
}
