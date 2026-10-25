"""Прогон тестовых чатов через запущенный сервер и автоматические проверки.

python run_scenarios.py [demo_shop teammates coffee] [--base http://localhost:8000] [--delay 4]
"""
import argparse
import json
import time
import urllib.request

CHECKS = "дубли текстов, слияние узлов конфликта, длина тезисов ≤ 10 слов, ≤ 6 тезисов на блок, source_msgs"


def req(base: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"content-type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.startswith(("{", "[")) else raw


def check(state: dict) -> list[str]:
    nodes = state["canvas"]["nodes"]
    problems = []
    live = [n for n in nodes if n["status"] != "rejected"]
    texts = [n["text"].casefold() for n in live]
    for t in set(texts):
        if texts.count(t) > 1:
            problems.append(f"дубль: {t}")
    by_id = {n["id"]: n for n in nodes}
    for c in state["canvas"]["conflicts"]:
        a, b = by_id.get(c["a"]), by_id.get(c["b"])
        if a and b and (a["parent_id"] == b["id"] or b["parent_id"] == a["id"]
                        or (a["parent_id"] and a["parent_id"] == b["parent_id"]
                            and by_id[a["parent_id"]]["authors"] == ["agent"])):
            problems.append(f"узлы конфликта {c['id']} слиты")
    for n in live:
        if len(n["text"].split()) > 10 and n["section"] not in ("essence", "questions"):
            problems.append(f"длинный тезис {n['id']}: {n['text']}")
        if not n["source_msgs"] and "agent" not in n["authors"]:
            problems.append(f"нет source_msgs у {n['id']}")
    for s in {n["section"] for n in live}:
        k = len([n for n in live if n["section"] == s and not n["parent_id"]])
        if k > 6:
            problems.append(f"в блоке {s} {k} тезисов")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", default=["demo_shop", "teammates", "coffee"])
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--delay", type=float, default=4.0)
    args = ap.parse_args()
    for sc in args.scenarios:
        t = req(args.base, "POST", "/api/dev/topic", {"name": f"test {sc}"})
        tok = t["token"]
        req(args.base, "POST", f"/api/dev/topics/{tok}/reset")
        n = req(args.base, "POST", f"/api/topics/{tok}/replay",
                {"scenario": sc, "delay": args.delay})["messages"]
        time.sleep(n * args.delay + 15)
        state = req(args.base, "GET", f"/api/topics/{tok}")
        compact = req(args.base, "GET", f"/api/dev/topics/{tok}/compact")
        bot = [m["text"] for m in state["messages"] if m["is_agent"]]
        print(f"\n===== {sc} ({t['url']}) =====\n{compact}\n\nБот в чат ({len(bot)}):")
        for b in bot:
            print("  -", b)
        problems = check(state)
        print(f"\nПроверки ({CHECKS}):", "OK" if not problems else "")
        for p in problems:
            print("  ✗", p)


if __name__ == "__main__":
    main()
