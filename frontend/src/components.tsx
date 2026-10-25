import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { Toast } from "./store";
import type { AgentStatus, Canvas, Message, Node, Section, TopicInfo } from "./types";
import { SECTIONS } from "./types";

const FLASH_MS = 3000;

export function authorLabel(a: string) {
  return a === "agent" ? "бот" : a;
}

function Authors({ authors }: { authors: string[] }) {
  if (!authors.length) return null;
  const [first, ...rest] = authors;
  return (
    <div className="authors">
      <span className={`author ${first === "agent" ? "author-bot" : ""}`}>{authorLabel(first)}</span>
      {rest.length > 0 && (
        <span className="plus" title={rest.map(authorLabel).join(", ")}>+{rest.length}</span>
      )}
    </div>
  );
}

interface CardCtx {
  canvas: Canvas;
  touched: Record<string, number>;
  focusId: string | null;
  print: boolean;
  onOpen: (n: Node) => void;
}

function conflictWith(canvas: Canvas, id: string): string[] {
  return canvas.conflicts
    .filter((c) => c.status === "open" && (c.a === id || c.b === id))
    .map((c) => (c.a === id ? c.b : c.a));
}

function nodeText(canvas: Canvas, id: string) {
  return canvas.nodes.find((n) => n.id === id)?.text ?? id;
}

function NodeBody({ node, ctx, detail }: { node: Node; ctx: CardCtx; detail?: boolean }) {
  const t = ctx.touched[node.id];
  const flashing = t && Date.now() - t < FLASH_MS + 500;
  const opp = conflictWith(ctx.canvas, node.id);
  return (
    <div
      className={`node ${detail ? "detail" : "thesis"} st-${node.status} ${ctx.focusId === node.id ? "focus" : ""}`}
      data-node-id={node.id}
      title={node.status_reason ?? undefined}
      onClick={(e) => { e.stopPropagation(); ctx.onOpen(node); }}
    >
      {flashing && !ctx.print && <span key={t} className="flash" />}
      <div className="node-row">
        <span className="node-text">{node.text}</span>
        {node.tag && <span className="tag">{node.tag}</span>}
      </div>
      {opp.length > 0 && (
        <div className="badge badge-conflict">конфликт: {opp.map((o) => nodeText(ctx.canvas, o)).join(", ")}</div>
      )}
      {node.status === "needs_review" && (
        <div className="badge badge-review">
          пересмотреть{node.status_reason ? `: ${node.status_reason}` : ""}
        </div>
      )}
      {node.status === "needs_review" && node.revise_text && (
        <div className="revise">→ {node.revise_text}</div>
      )}
      {node.status === "rejected" && node.status_reason && (
        <div className="reason">{node.section === "questions" ? "решено" : "отказались"}: {node.status_reason}</div>
      )}
      <Authors authors={node.authors} />
    </div>
  );
}

function Thesis({ node, details, ctx }: { node: Node; details: Node[]; ctx: CardCtx }) {
  return (
    <motion.div
      layout={!ctx.print}
      initial={ctx.print ? false : { opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.3 }}
      className="card"
    >
      <NodeBody node={node} ctx={ctx} />
      {details.length > 0 && (
        <div className="details">
          <AnimatePresence initial={false}>
            {details.map((d) => (
              <motion.div key={d.id} layout={!ctx.print}
                initial={ctx.print ? false : { opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}>
                <NodeBody node={d} ctx={ctx} detail />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  );
}

const ORDER: Record<string, number> = { disputed: 0, needs_review: 1, new: 2, active: 3, rejected: 9 };

function sortNodes(a: Node[]) {
  // Отклонённые уходят вниз, остальное — в порядке появления.
  return [...a].sort((x, y) => (ORDER[x.status] === 9 ? 1 : 0) - (ORDER[y.status] === 9 ? 1 : 0));
}

function Block({ section, ctx }: { section: (typeof SECTIONS)[number]; ctx: CardCtx }) {
  const all = ctx.canvas.nodes.filter((n) => n.section === section.key);
  const ids = new Set(all.map((n) => n.id));
  const top = sortNodes(all.filter((n) => !n.parent_id || !ids.has(n.parent_id)));
  const kids = (id: string) => {
    const out: Node[] = [];
    const walk = (pid: string) => {
      for (const n of sortNodes(ctx.canvas.nodes.filter((x) => x.parent_id === pid))) { out.push(n); walk(n.id); }
    };
    walk(id);
    return out;
  };
  const essence = section.key === "essence";
  return (
    <section className={`block block-${section.key}`}>
      <header className="block-head">
        <h2>{section.title}</h2>
        {!essence && top.length > 0 && <span className="count">{top.filter((n) => n.status !== "rejected").length}</span>}
      </header>
      {top.length === 0 && <div className="empty">{essence ? "Бот соберёт суть, как только начнётся обсуждение" : section.hint}</div>}
      <div className={essence ? "essence-list" : "cards"}>
        <AnimatePresence initial={false}>
          {top.map((n) => <Thesis key={n.id} node={n} details={kids(n.id)} ctx={ctx} />)}
        </AnimatePresence>
      </div>
    </section>
  );
}

// Рёбра: красное — открытый конфликт, жёлтый пунктир — needs_review к ограничению.
function Edges({ canvas, root }: { canvas: Canvas; root: React.RefObject<HTMLDivElement> }) {
  const [paths, setPaths] = useState<{ d: string; kind: "conflict" | "review"; key: string }[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  // useEffect, а не useLayoutEffect: ref родителя к этому моменту уже выставлен.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const edges: { a: string; b: string; kind: "conflict" | "review"; key: string }[] = [];
    for (const c of canvas.conflicts) if (c.status === "open") edges.push({ a: c.a, b: c.b, kind: "conflict", key: c.id });
    for (const n of canvas.nodes)
      if (n.status === "needs_review") for (const d of n.depends_on) edges.push({ a: n.id, b: d, kind: "review", key: `${n.id}-${d}` });

    const compute = () => {
      const base = el.getBoundingClientRect();
      setSize({ w: el.scrollWidth, h: el.scrollHeight });
      const box = (id: string) => {
        const e = el.querySelector<HTMLElement>(`[data-node-id="${id}"]`);
        if (!e) return null;
        const r = e.getBoundingClientRect();
        return { x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height };
      };
      const out: typeof paths = [];
      for (const e of edges) {
        const A = box(e.a), B = box(e.b);
        if (!A || !B) continue;
        const ac = { x: A.x + A.w / 2, y: A.y + A.h / 2 }, bc = { x: B.x + B.w / 2, y: B.y + B.h / 2 };
        let p1, p2, c1, c2;
        if (Math.abs(ac.x - bc.x) > (A.w + B.w) / 2) {
          const dir = bc.x > ac.x ? 1 : -1;
          p1 = { x: ac.x + (dir * A.w) / 2, y: ac.y }; p2 = { x: bc.x - (dir * B.w) / 2, y: bc.y };
          const k = Math.max(40, Math.abs(p2.x - p1.x) / 2);
          c1 = { x: p1.x + dir * k, y: p1.y }; c2 = { x: p2.x - dir * k, y: p2.y };
        } else {
          const dir = bc.y > ac.y ? 1 : -1;
          p1 = { x: ac.x, y: ac.y + (dir * A.h) / 2 }; p2 = { x: bc.x, y: bc.y - (dir * B.h) / 2 };
          const k = Math.max(40, Math.abs(p2.y - p1.y) / 2);
          c1 = { x: p1.x, y: p1.y + dir * k }; c2 = { x: p2.x, y: p2.y - dir * k };
        }
        out.push({ key: e.key, kind: e.kind, d: `M${p1.x},${p1.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${p2.x},${p2.y}` });
      }
      setPaths(out);
    };

    compute();
    // Пока идут layout-анимации, пересчитываем каждый кадр ~0,8 с.
    let raf = 0;
    const until = performance.now() + 800;
    const loop = () => { compute(); if (performance.now() < until) raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    window.addEventListener("resize", compute);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); window.removeEventListener("resize", compute); };
  }, [canvas, root]);

  return (
    <svg className="edges" width={size.w} height={size.h}>
      {paths.map((p) => (
        <g key={p.key} className={`edge edge-${p.kind}`}>
          <path d={p.d} />
        </g>
      ))}
    </svg>
  );
}

export function CanvasView(props: { canvas: Canvas; touched: Record<string, number>; focusId: string | null;
                                    print: boolean; onOpen: (n: Node) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const ctx: CardCtx = props;
  return (
    <div className="canvas" ref={ref}>
      {SECTIONS.map((s) => <Block key={s.key} section={s} ctx={ctx} />)}
      <Edges canvas={props.canvas} root={ref} />
    </div>
  );
}

const AGENT_LABEL: Record<AgentStatus, string> = { idle: "бот слушает", reading: "бот читает…", thinking: "бот думает…" };

export function Header({ topic, agent, online, print }: { topic: TopicInfo; agent: AgentStatus; online: boolean; print: boolean }) {
  const closed = topic.status === "closed";
  const date = new Date((closed && topic.closed_at ? topic.closed_at : topic.opened_at) * 1000);
  return (
    <header className="topbar">
      <div className="brand">/no_idea</div>
      <h1 className="topic-name">{topic.name}</h1>
      <span className={`pill ${closed ? "pill-closed" : "pill-open"}`}>{closed ? "тема закрыта" : "тема открыта"}</span>
      <div className="spacer" />
      {print ? (
        <span className="muted">{date.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}</span>
      ) : closed ? null : (
        <span className={`agent agent-${online ? agent : "offline"}`}>
          <span className="dot" />{online ? AGENT_LABEL[agent] : "нет связи, переподключаюсь…"}
        </span>
      )}
    </header>
  );
}

export function Feed({ messages, onFocusSeq }: { messages: Message[]; onFocusSeq?: (seq: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);
  const bySeq = new Map(messages.map((m) => [m.seq, m]));
  return (
    <aside className="feed">
      <h2 className="feed-head">Из чата <span className="count">{messages.length}</span></h2>
      <div className="feed-list" ref={ref}>
        {messages.length === 0 && <div className="empty">Сообщения из Telegram появятся здесь</div>}
        <AnimatePresence initial={false}>
          {messages.slice(-60).map((m) => {
            const reply = m.reply_to_seq != null ? bySeq.get(m.reply_to_seq) : undefined;
            return (
              <motion.div key={m.seq} className={`msg ${m.is_agent ? "msg-bot" : ""}`}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                onClick={() => onFocusSeq?.(m.seq)}>
                <div className="msg-author">{m.is_agent ? "🤖 no_idea" : m.author}</div>
                {reply && <div className="msg-reply">↪ {reply.is_agent ? "no_idea" : reply.author}: {reply.text.slice(0, 60)}</div>}
                <div className="msg-text">{m.text}</div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </aside>
  );
}

export function NodePopup({ node, messages, canvas, onClose }: { node: Node; messages: Message[]; canvas: Canvas; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const quotes = node.source_msgs.map((s) => messages.find((m) => m.seq === s)).filter(Boolean) as Message[];
  const section = SECTIONS.find((s) => s.key === node.section)?.title;
  const deps = node.depends_on.map((d) => nodeText(canvas, d));
  return (
    <div className="overlay" onClick={onClose}>
      <motion.div className="popup" onClick={(e) => e.stopPropagation()}
        initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <button className="close" onClick={onClose} aria-label="Закрыть">×</button>
        <div className="popup-section">{section} · {node.id}</div>
        <h3>{node.text}</h3>
        {node.goal && <p className="muted">Цель: {node.goal}</p>}
        {node.status_reason && <p className={`popup-reason st-${node.status}`}>{node.status_reason}</p>}
        {deps.length > 0 && <p className="muted">Держится на: {deps.join(", ")}</p>}
        <h4>Откуда взято</h4>
        {quotes.length === 0 && <p className="muted">Источник не найден в ленте.</p>}
        {quotes.map((q) => (
          <blockquote key={q.seq}>
            <div className="q-author">{q.is_agent ? "no_idea" : q.author} <span className="muted">#{q.seq}</span></div>
            {q.text}
          </blockquote>
        ))}
      </motion.div>
    </div>
  );
}

export function Toasts({ toasts, onClick, onDismiss }: { toasts: Toast[]; onClick: (t: Toast) => void; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timers = toasts.map((t) => window.setTimeout(() => onDismiss(t.id), 5000));
    return () => timers.forEach(clearTimeout);
  }, [toasts, onDismiss]);
  return (
    <div className="toasts">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} className="toast" onClick={() => onClick(t)}
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -20 }}>
            ✨ {t.text}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export type { Section };
