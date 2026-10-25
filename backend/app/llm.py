"""LLM-клиент: structured output, один ретрай с текстом ошибки, кэш off/record/replay, лог."""
import hashlib
import json
import logging
import re
import time
from collections import deque
from typing import Callable, TypeVar

from openai import AsyncOpenAI, BadRequestError
from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_client: AsyncOpenAI | None = None
_json_mode = config.LLM_JSON_MODE   # auto -> json_schema, при 400 переключаемся на prompt
recent: deque = deque(maxlen=20)    # для /debug/llm


class LLMFailed(Exception):
    pass


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY or "none",
                              timeout=config.LLM_TIMEOUT)
    return _client


def _schema_hint(schema: type[BaseModel]) -> str:
    return ("\n\nОтветь ТОЛЬКО одним JSON-объектом без пояснений и без markdown, "
            "строго по JSON-схеме:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False))


def extract_json(raw: str) -> str:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    if not raw.startswith("{"):
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            raw = raw[a:b + 1]
    return raw


def _cache_path(key: str):
    return config.CACHE_DIR / f"{key}.json"


def _log(role: str, model: str, messages: list, raw: str | None, dt: float, usage, attempt: int,
         error: str | None = None, cached: bool = False) -> None:
    rec = {"ts": time.time(), "role": role, "model": model, "attempt": attempt,
           "latency_s": round(dt, 3), "cached": cached, "error": error,
           "usage": usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else None,
           "messages": messages, "response": raw}
    recent.append(rec)
    try:
        with config.LLM_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def _call(model: str, messages: list, schema: type[BaseModel]):
    global _json_mode
    kwargs = {}
    if _json_mode in ("auto", "json_schema"):
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "schema": schema.model_json_schema()}}
    try:
        return await client().chat.completions.create(
            model=model, messages=messages, temperature=0.1, max_tokens=4000, **kwargs)
    except BadRequestError as e:
        if kwargs and _json_mode == "auto":
            log.warning("provider rejected json_schema (%s), switching to prompt mode", e)
            _json_mode = "prompt"
            return await client().chat.completions.create(
                model=model, messages=messages, temperature=0.1, max_tokens=4000)
        raise


async def structured(model: str, system: str, user: str, schema: type[T], *,
                     role: str = "", validator: Callable[[T], list[str]] | None = None,
                     max_retries: int = 1) -> T:
    key = hashlib.sha256(f"{model}|{system}|{user}".encode()).hexdigest()
    if config.CACHE_MODE in ("replay", "record") and _cache_path(key).exists():
        raw = _cache_path(key).read_text(encoding="utf-8")
        _log(role, model, [], raw, 0, None, 0, cached=True)
        return schema.model_validate_json(raw)
    if not config.LLM_API_KEY:
        raise LLMFailed(["LLM_API_KEY не задан"])

    messages = [{"role": "system", "content": system + _schema_hint(schema)},
                {"role": "user", "content": user}]
    problems: list[str] = []
    models = [model] + ([config.FALLBACK_MODEL] if config.FALLBACK_MODEL else [])
    for attempt in range(max_retries + 1):
        t0 = time.perf_counter()
        resp, err = None, None
        for m in models:
            try:
                resp = await _call(m, messages, schema)
                break
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                _log(role, m, messages, None, time.perf_counter() - t0, None, attempt, error=err)
        if resp is None:
            raise LLMFailed([err or "no response"])
        raw = resp.choices[0].message.content or ""
        _log(role, model, messages, raw, time.perf_counter() - t0, resp.usage, attempt)
        try:
            result = schema.model_validate_json(extract_json(raw))
            problems = validator(result) if validator else []
            if not problems:
                if config.CACHE_MODE in ("record", "replay"):
                    _cache_path(key).write_text(extract_json(raw), encoding="utf-8")
                return result
        except ValidationError as e:
            problems = [str(e)[:1500]]
        messages = messages + [{"role": "assistant", "content": raw},
                               {"role": "user", "content": "Исправь ответ: " + "; ".join(problems)}]
    raise LLMFailed(problems)


def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
