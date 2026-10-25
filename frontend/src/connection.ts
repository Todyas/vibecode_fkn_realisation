import type { ServerEvent, Snapshot } from "./types";

export async function fetchSnapshot(token: string): Promise<Snapshot> {
  const r = await fetch(`/api/topics/${encodeURIComponent(token)}`);
  if (r.status === 404) throw new Error("not_found");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// WS с переподключением. Каждый snapshot при (пере)подключении полностью заменяет состояние.
export function connectWs(token: string, onEvent: (e: ServerEvent) => void, onDown: () => void) {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 500;
  let timer: number | undefined;

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/topics/${encodeURIComponent(token)}`);
    ws.onopen = () => { backoff = 500; };
    ws.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data)); } catch (err) { console.warn("bad ws message", err); }
    };
    ws.onclose = () => {
      if (closed) return;
      onDown();
      timer = window.setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, 8000);
    };
  };
  open();
  return () => { closed = true; clearTimeout(timer); ws?.close(); };
}
