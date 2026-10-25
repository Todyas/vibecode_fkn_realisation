import { MotionConfig } from "framer-motion";
import { useCallback, useEffect, useReducer, useState } from "react";
import { CanvasView, Feed, Header, NodePopup, Toasts } from "./components";
import { connectWs, fetchSnapshot } from "./connection";
import { connectMock } from "./mock";
import { initialState, reducer } from "./store";
import type { Node } from "./types";

declare global {
  interface Window { __canvasReady?: boolean }
}

const params = new URLSearchParams(location.search);
const token = decodeURIComponent(location.pathname.match(/\/t\/([^/]+)/)?.[1] ?? "demo");
const print = params.get("print") === "1";
const mock = params.get("mock") === "1" || token === "demo";
const upto = params.has("upto") ? Number(params.get("upto")) : print ? 99 : null;

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [online, setOnline] = useState(true);
  const [focusId, setFocusId] = useState<string | null>(location.hash.slice(1) || null);
  const [openNode, setOpenNode] = useState<Node | null>(null);

  // Загрузка и живые обновления
  useEffect(() => {
    if (mock) {
      return connectMock((event) => dispatch({ type: "server", event }), {
        upto, delayMs: Number(params.get("delay") ?? 4000),
      });
    }
    if (print) {
      // Для скриншота хватает одного GET.
      fetchSnapshot(token).then((s) => dispatch({ type: "loaded", snapshot: s }))
        .catch((e) => dispatch({ type: "error", error: String(e.message ?? e) }));
      return;
    }
    return connectWs(token, (event) => { setOnline(true); dispatch({ type: "server", event }); }, () => setOnline(false));
  }, []);

  // Пропущенная версия патча — перезапрос полного состояния
  useEffect(() => {
    if (!state.needsResync || mock) return;
    dispatch({ type: "resync_started" });
    fetchSnapshot(token).then((s) => dispatch({ type: "loaded", snapshot: s })).catch(() => {});
  }, [state.needsResync]);

  // Если WS не ответил снапшотом за 3 с — подгружаем GET (и показываем 404, если токена нет)
  useEffect(() => {
    if (mock || print) return;
    const t = window.setTimeout(() => {
      if (!state.loaded) fetchSnapshot(token).then((s) => dispatch({ type: "loaded", snapshot: s }))
        .catch((e) => dispatch({ type: "error", error: String(e.message ?? e) }));
    }, 3000);
    return () => clearTimeout(t);
  }, [state.loaded]);

  // Флаг для Playwright: канвас отрисован
  useEffect(() => {
    if (!state.loaded) return;
    document.fonts.ready.then(() =>
      requestAnimationFrame(() => requestAnimationFrame(() => { window.__canvasReady = true; })));
  }, [state.loaded]);

  // #N7 — подсветить узел и прокрутить к нему
  useEffect(() => {
    const h = () => setFocusId(location.hash.slice(1) || null);
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    if (!focusId || print) return;
    const el = document.querySelector(`[data-node-id="${focusId}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const t = window.setTimeout(() => setFocusId(null), 4000);
    return () => clearTimeout(t);
  }, [focusId, state.canvas.nodes.length]);

  const dismiss = useCallback((id: number) => dispatch({ type: "dismiss_toast", id }), []);
  const focusSeq = useCallback((seq: number) => {
    const n = state.canvas.nodes.find((x) => x.source_msgs.includes(seq));
    if (n) setFocusId(n.id);
  }, [state.canvas.nodes]);

  if (state.error && !state.loaded) {
    return (
      <div className="center">
        <h1>{state.error === "not_found" ? "Ссылка не найдена" : "Не удалось загрузить канвас"}</h1>
        <p className="muted">
          {state.error === "not_found"
            ? "Попросите в чате свежую ссылку: /no_idea list"
            : "Сервер недоступен. Обновите страницу через минуту."}
        </p>
      </div>
    );
  }
  if (!state.loaded || !state.topic) return <div className="center muted">Загружаю канвас…</div>;

  const closed = state.topic.status === "closed";
  return (
    <MotionConfig reducedMotion={print ? "always" : "user"}>
      <div className={`app ${print ? "print" : ""} ${closed ? "closed" : ""}`}>
        <Header topic={state.topic} agent={state.agent} online={online} print={print} />
        {closed && (
          <div className="closed-banner">
            <strong>Тема закрыта.</strong> Канвас заморожен
            {state.topic.summary && <div className="summary">{state.topic.summary}</div>}
          </div>
        )}
        <main className="layout">
          <CanvasView canvas={state.canvas} touched={state.touched} focusId={focusId} print={print}
            onOpen={(n) => !print && setOpenNode(n)} />
          {!print && <Feed messages={state.messages} onFocusSeq={focusSeq} />}
        </main>
        {!print && <Toasts toasts={state.toasts} onDismiss={dismiss}
          onClick={(t) => t.focus_ids[0] && setFocusId(t.focus_ids[0])} />}
        {openNode && (
          <NodePopup node={state.canvas.nodes.find((n) => n.id === openNode.id) ?? openNode}
            messages={state.messages} canvas={state.canvas} onClose={() => setOpenNode(null)} />
        )}
        {print && <footer className="print-foot">/no_idea · {state.canvas.nodes.filter((n) => n.status !== "rejected").length} тезисов · {state.messages.length} сообщений</footer>}
      </div>
    </MotionConfig>
  );
}
