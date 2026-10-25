"""Разбор команды /no_idea — чистые функции без Telegram."""
import re
from dataclasses import dataclass

SUBCOMMANDS = {"start", "stop", "export", "ask", "list", "help"}
QUOTES = {'"': '"', "«": "»", "“": "”", "„": "“", "'": "'"}
CMD_RE = re.compile(r"^/no_idea(?:@(\w+))?(?=\s|$)", re.IGNORECASE)


@dataclass
class Cmd:
    sub: str                 # start / stop / export / ask / list / help
    name: str | None = None  # имя темы (в кавычках или первое слово)
    rest: str = ""           # всё после имени (для ask — вопрос)
    quoted: bool = False     # имя было в кавычках
    raw_args: str = ""       # всё после подкоманды


def is_command(text: str | None, bot_username: str | None = None) -> bool:
    if not text:
        return False
    m = CMD_RE.match(text.strip())
    if not m:
        return False
    addressed = m.group(1)
    return not (addressed and bot_username and addressed.lower() != bot_username.lower())


def split_name(args: str) -> tuple[str | None, str, bool]:
    """Имя в кавычках или первое слово → (name, rest, quoted)."""
    args = args.strip()
    if not args:
        return None, "", False
    q = args[0]
    if q in QUOTES:
        end = args.find(QUOTES[q], 1)
        if end > 0:
            return args[1:end].strip() or None, args[end + 1:].strip(), True
    parts = args.split(maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else ""), False


def parse(text: str) -> Cmd:
    text = text.strip()
    m = CMD_RE.match(text)
    args = text[m.end():].strip() if m else text
    if not args:
        return Cmd("help")
    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    if sub not in SUBCOMMANDS:
        return Cmd("help", raw_args=args)
    raw = parts[1] if len(parts) > 1 else ""
    name, rest, quoted = split_name(raw)
    return Cmd(sub, name, rest, quoted, raw.strip())


def norm_key(name: str) -> str:
    return " ".join(name.casefold().split())


def find_key(keys: list[str], name: str) -> str | None:
    """Точное совпадение без регистра/пробелов, иначе единственное по префиксу."""
    k = norm_key(name)
    if not k:
        return None
    if k in keys:
        return k
    pref = [x for x in keys if x.startswith(k)]
    return pref[0] if len(pref) == 1 else None
