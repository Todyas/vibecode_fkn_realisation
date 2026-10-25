import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

load_dotenv(ROOT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
FAST_MODEL = os.getenv("FAST_MODEL", "qwen/qwen3-235b-a22b-2507")
STRONG_MODEL = os.getenv("STRONG_MODEL", "deepseek/deepseek-r1-0528")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "")
LLM_TIMEOUT = _f("LLM_TIMEOUT", 90)          # думающие модели отвечают долго
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "12000"))  # с запасом под рассуждения
# auto | json_schema | prompt — как просить JSON у провайдера
LLM_JSON_MODE = os.getenv("LLM_JSON_MODE", "auto")
CACHE_MODE = os.getenv("CACHE_MODE", "off")  # off | record | replay

DEBOUNCE_S = _f("DEBOUNCE_S", 1.5)
BOT_COOLDOWN_S = _f("BOT_COOLDOWN_S", 60)
CONSOLIDATE_EVERY = int(os.getenv("CONSOLIDATE_EVERY", "8"))
MAX_THESES_PER_BLOCK = int(os.getenv("MAX_THESES_PER_BLOCK", "6"))
MEDIATOR_MIN_CONFIDENCE = _f("MEDIATOR_MIN_CONFIDENCE", 0.6)
# «Имя: текст» в начале сообщения = автор «Имя» (один человек играет всю команду)
ROLEPLAY_NAMES = os.getenv("ROLEPLAY_NAMES", "1") == "1"
SNAPSHOT_TIMEOUT_S = _f("SNAPSHOT_TIMEOUT_S", 10)

DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data"))
TOPICS_DIR = DATA_DIR / "topics"
CACHE_DIR = DATA_DIR / "llm_cache"
LLM_LOG = DATA_DIR / "llm_log.jsonl"
PROMPTS_DIR = APP_DIR / "prompts"
SCENARIOS_DIR = APP_DIR / "scenarios"
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", ROOT_DIR / "frontend" / "dist"))

for d in (DATA_DIR, TOPICS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def llm_enabled() -> bool:
    return bool(LLM_API_KEY) or CACHE_MODE == "replay"


def canvas_url(token: str, node_id: str | None = None) -> str:
    url = f"{PUBLIC_BASE_URL}/t/{token}"
    return f"{url}#{node_id}" if node_id else url
