# /no_idea — API-контракт бэкенд ↔ сайт канваса (v1)

Бэкенд: один процесс FastAPI на :8000 (бот + API + WS). Сайт — SPA (Vite/React), сборка кладётся в `frontend/dist/`,
её раздаёт тот же FastAPI:
- `GET /t/{token}` → `frontend/dist/index.html` (SPA сама берёт token из пути `/t/<token>`)
- `GET /assets/*` → `frontend/dist/assets/*` (Vite `base: "/"`)
- Пока `frontend/dist` нет, бэк отдаёт свою простую заглушку-страницу.
- Для dev: Vite proxy `/api` и `/ws` → `http://localhost:8000`.

## Модели (JSON)

```ts
type Section = "essence"|"problem"|"audience"|"solution"|"constraints"|"risks"|"questions";
type Status  = "new"|"active"|"disputed"|"needs_review"|"rejected";

interface Node {
  id: string;              // "N12" (вопросы тоже N-узлы в section "questions")
  section: Section;
  parent_id: string|null;  // null = тезис уровня 1, иначе деталь (глубина max 2)
  text: string;
  goal: string|null;
  authors: string[];       // имена из Telegram; "agent" = добавил бот. "+N" = authors.length-1
  source_msgs: number[];   // seq сообщений-источников (для попапа с цитатами)
  status: Status;
  status_reason: string|null;   // тултип для rejected / needs_review
  depends_on: string[];    // id ограничений -> жёлтый пунктир, когда status=needs_review
  tag: string|null;        // например "этап 2"
  revise_text: string|null;// предложенная Impact-ом формулировка, показывать под карточкой
  version: number;
}
interface Conflict {
  id: string;              // "K1"
  a: string; b: string;    // id узлов -> красное ребро, пока status="open"
  explanation: string;
  question_id: string|null;// узел-вопрос в "questions"
  status: "open"|"resolved";
  resolution: string|null;
}
interface Canvas { version: number; nodes: Node[]; conflicts: Conflict[]; }
interface Message {
  seq: number; ts: number; text: string; author: string;
  is_agent: boolean; reply_to_seq: number|null;
}
interface TopicInfo {
  id: string; name: string; status: "open"|"closed";
  opened_at: number; closed_at: number|null; summary: string|null;
}
type AgentStatus = "idle"|"reading"|"thinking";
```

## REST
- `GET /api/topics/{token}` → `{topic: TopicInfo, messages: Message[], canvas: Canvas, agent_status: AgentStatus}`; 404 если токена нет.
- `GET /api/topics/{token}/export.md` → text/markdown.
- `POST /api/topics/{token}/replay` body `{"scenario":"demo_shop","upto":4,"delay":3}` — проигрывает сценарий как из ТГ.

## WebSocket `/ws/topics/{token}` (только сервер → клиент)
Каждое сообщение — JSON с полем `type`:
- `snapshot` `{topic, messages, canvas, agent_status}` — сразу после подключения
- `message` `{message: Message}`
- `canvas_patch` `{version, ops: Op[]}` где
  `Op = {op:"upsert_node", node: Node} | {op:"remove_node", id: string} | {op:"upsert_conflict", conflict: Conflict}`.
  Правило: если `version !== local.version + 1` → перезапросить `GET /api/topics/{token}`.
- `toast` `{text, focus_ids: string[]}`
- `agent_status` `{status: AgentStatus}`
- `topic_status` `{status: "open"|"closed", summary: string|null}` — плашка «Тема закрыта» при closed

## Страница
- `/t/{token}#N7` — подсветить и проскроллить к узлу N7.
- `/t/{token}?print=1` — режим скриншота: без ленты сообщений, без анимаций, всё развёрнуто, ширина 1600px.
  Когда канвас отрисован, страница ставит `window.__canvasReady = true` (Playwright ждёт этот флаг).
- Клик по тезису → попап с цитатами `messages` по `source_msgs` (автор + текст).
