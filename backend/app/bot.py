"""Telegram-бот: роутер команд /no_idea, приём сообщений, отправка фото/файлов, cooldown."""
import asyncio
import html
import logging
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (BotCommand, BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message as TgMessage)

from . import commands, config, pipeline, render, snapshot
from .topics import Registry, TopicError, TopicRoom

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Я веду канвас обсуждения темы прямо из чата. Добавь меня в группу (privacy mode off), "
    "открой тему — и пишите как обычно, я соберу тезисы на сайте.\n\n"
    "/no_idea start <имя> — открыть тему (или переоткрыть старую)\n"
    "/no_idea stop [имя] — закрыть: картинка канваса + итог\n"
    "/no_idea export [имя] — картинка + файл .md\n"
    "/no_idea ask [имя] <вопрос> — спросить по канвасу (или ответь реплаем на меня)\n"
    "/no_idea list — темы чата\n\n"
    "Имя из нескольких слов — в кавычках: /no_idea start \"Редизайн онбординга\""
)


def author_name(m: TgMessage) -> str:
    u = m.from_user
    if u is None:
        return m.sender_chat.title if m.sender_chat else "?"
    name = " ".join(x for x in (u.first_name, u.last_name) if x).strip()
    return name or (f"@{u.username}" if u.username else str(u.id))


ROLE_RE = re.compile(r"^\s*([A-ZА-ЯЁ][\w\-]{1,20})\s*:\s+(.+)$", re.S)


def split_role(text: str, fallback: str) -> tuple[str, str]:
    """«Маркетолог: текст» -> (Маркетолог, текст), чтобы один человек мог играть всех на тесте."""
    if config.ROLEPLAY_NAMES and (m := ROLE_RE.match(text)):
        return m.group(1), m.group(2).strip()
    return fallback, text


def canvas_button(room: TopicRoom, node_id: str | None = None) -> InlineKeyboardMarkup | None:
    url = config.canvas_url(room.topic.web_token, node_id)
    if not url.startswith("https://"):   # Telegram не принимает localhost в кнопках
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Показать на канвасе", url=url)]])


def e(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def link(room: TopicRoom, node_id: str | None = None, label: str = "Открыть канвас") -> str:
    """HTML-ссылка на канвас; localhost Telegram не делает ссылкой, тогда адрес текстом."""
    url = config.canvas_url(room.topic.web_token, node_id)
    if url.startswith("https://"):
        return f'<a href="{html.escape(url)}">{label}</a>'
    return f"{label}: {e(url)}"


def _link_suffix(room: TopicRoom, node_id: str | None = None) -> str:
    """Под сообщением бота: кнопка, если https; иначе ссылка текстом."""
    url = config.canvas_url(room.topic.web_token, node_id)
    return "" if url.startswith("https://") else "\n\n" + link(room, node_id, "Показать на канвасе")


class TelegramNotifier:
    def __init__(self, bot: Bot, registry: Registry):
        self.bot, self.registry = bot, registry

    async def _send(self, coro_fn):
        for _ in range(3):
            try:
                return await coro_fn()
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except TelegramForbiddenError:
                log.warning("bot was removed from chat")
                return None
        return None

    async def agent_say(self, room: TopicRoom, text: str, *, reply_to_tg: int | None,
                        focus_ids: list[str], conflict_id: str | None,
                        initiative: bool) -> int | None:
        chat_id = room.topic.chat_id
        if chat_id == 0:                    # тема из replay без Telegram
            return None
        cs = self.registry.chat(chat_id)
        now = time.time()
        if initiative and now - cs.last_bot_message_ts < config.BOT_COOLDOWN_S:
            log.info("cooldown: skip telegram message in %s", chat_id)
            return None
        node = focus_ids[0] if focus_ids else None
        kwargs = dict(reply_markup=canvas_button(room, node))
        if reply_to_tg:
            kwargs["reply_to_message_id"] = reply_to_tg
            kwargs["allow_sending_without_reply"] = True
        msg = await self._send(lambda: self.bot.send_message(
            chat_id, e(text) + _link_suffix(room, node), **kwargs))
        if msg is None:
            return None
        cs.last_bot_message_ts = now
        return msg.message_id


# ---------- команды ----------

def _topics_list(reg: Registry, chat_id: int) -> str:
    rooms = reg.topics_of(chat_id)
    if not rooms:
        return "В этом чате ещё нет тем. Начни: /no_idea start <имя>"
    lines = [f"{'🟢' if r.topic.status == 'open' else '⚪️'} {link(r, label=e(r.topic.name))}" for r in rooms]
    return "Темы чата:\n" + "\n".join(lines)


def _resolve(reg: Registry, chat_id: int, cmd: commands.Cmd) -> tuple[TopicRoom | None, str]:
    """Тема по имени или текущая. Для ask -> (room, вопрос)."""
    if cmd.sub == "ask":
        if cmd.name:
            room = reg.find(chat_id, cmd.name)
            if room is not None:
                return room, cmd.rest
            if cmd.quoted:
                return None, ""
        cur = reg.current(chat_id)
        return cur, cmd.raw_args
    if cmd.name:
        return reg.find(chat_id, cmd.raw_args) or reg.find(chat_id, cmd.name), ""
    return reg.current(chat_id), ""


async def send_canvas(bot: Bot, room: TopicRoom, chat_id: int, caption: str,
                      reply_to: int | None = None) -> None:
    try:
        png = await asyncio.wait_for(snapshot.canvas_png(room.topic.web_token), 20)
    except Exception:  # noqa: BLE001
        log.exception("snapshot failed")
        png = None
    markup = canvas_button(room)
    if png:
        try:
            await bot.send_photo(chat_id, BufferedInputFile(png, f"{room.topic.name}.png"),
                                 caption=caption, reply_markup=markup,
                                 reply_to_message_id=reply_to, allow_sending_without_reply=True)
            return
        except TelegramBadRequest as e:   # слишком большая картинка -> файлом
            log.warning("send_photo failed: %s", e)
            try:
                await bot.send_document(chat_id, BufferedInputFile(png, f"{room.topic.name}.png"),
                                        caption=caption, reply_markup=markup)
                return
            except TelegramBadRequest:
                pass
    try:
        await bot.send_message(chat_id, caption, reply_markup=markup,
                               reply_to_message_id=reply_to, allow_sending_without_reply=True)
    except Exception:  # noqa: BLE001
        log.exception("text fallback failed")


async def send_pdf(bot: Bot, room: TopicRoom, chat_id: int) -> None:
    try:
        pdf = await snapshot.canvas_pdf(room.topic.web_token)
        if pdf:
            await bot.send_document(chat_id, BufferedInputFile(pdf, f"{room.topic.name}.pdf"))
    except Exception:  # noqa: BLE001
        log.exception("send_pdf failed")


STOP_LLM_TIMEOUT_S = 30


async def summary_quick(room: TopicRoom) -> str:
    """Итог с жёстким таймаутом: думающая модель не должна вешать stop/export."""
    try:
        return await asyncio.wait_for(pipeline.summarize(room), STOP_LLM_TIMEOUT_S)
    except Exception as err:  # noqa: BLE001
        log.warning("summary timeout/fail: %r", err)
        room.topic.summary = pipeline.fallback_summary(room)
        return room.topic.summary


async def close_topic(room: TopicRoom, reg: Registry) -> str:
    """stop: отцепить от чата -> оборвать текущую обработку -> Consolidator -> Summarizer -> closed.

    Не ждём очередь темы: медленная думающая модель раньше вешала stop на минуты."""
    reg.detach(room)
    room.cancel_work()
    try:
        await asyncio.wait_for(pipeline.consolidate(room), STOP_LLM_TIMEOUT_S)
    except Exception as err:  # noqa: BLE001
        log.warning("consolidate on stop skipped: %r", err)
    summary = await summary_quick(room)
    await room.set_status("idle")
    await room.set_topic_status("closed")
    return summary


async def export_md(room: TopicRoom) -> bytes:
    return render.markdown(room.topic).encode("utf-8")


async def handle_command(m: TgMessage, bot: Bot, reg: Registry) -> None:
    cmd = commands.parse(m.text or "")
    chat_id = m.chat.id
    if cmd.sub == "help":
        cur = reg.current(chat_id)
        tail = f"\n\nСейчас слушаю: «{e(cur.topic.name)}» · {link(cur)}" if cur else "\n\nСейчас тема не открыта."
        await m.reply(e(HELP) + tail)
        return
    if cmd.sub == "list":
        await m.reply(_topics_list(reg, chat_id))
        return
    if cmd.sub == "start":
        name = cmd.raw_args if not cmd.quoted else cmd.name
        if not name:
            await m.reply("Напиши имя темы: /no_idea start Онбординг")
            return
        try:
            room, reopened = await reg.start(chat_id, name, author_name(m))
        except TopicError as err:
            await m.reply(html.escape(str(err), quote=False))
            return
        verb = "Снова слушаю" if reopened else "Слушаю"
        await m.reply(f"{verb} тему <b>«{e(room.topic.name)}»</b>. Пишите как обычно — я соберу тезисы.\n\n"
                      f"🗺 {link(room)}",
                      reply_markup=canvas_button(room), disable_web_page_preview=True)
        return

    room, question = _resolve(reg, chat_id, cmd)
    if room is None:
        if cmd.name or cmd.sub == "ask":
            await m.reply("Не нашёл такую тему.\n\n" + _topics_list(reg, chat_id))
        else:
            await m.reply("Сейчас тема не открыта. Укажи имя: /no_idea "
                          f"{cmd.sub} &lt;имя&gt;\n\n" + _topics_list(reg, chat_id))
        return

    if cmd.sub == "stop":
        if room.topic.status != "open":
            await m.reply(f"Тема «{e(room.topic.name)}» не открыта.")
            return
        await m.reply(f"Закрываю «{e(room.topic.name)}», собираю итог и картинку…")
        await bot.send_chat_action(chat_id, "upload_photo")
        summary = await close_topic(room, reg)
        caption = f"Тема <b>«{e(room.topic.name)}»</b> закрыта.\n\n{e(summary[:700])}\n\n🗺 {link(room)}"
        await send_canvas(bot, room, chat_id, caption, reply_to=m.message_id)
        await send_pdf(bot, room, chat_id)
    elif cmd.sub == "export":
        await m.reply(f"Собираю экспорт «{e(room.topic.name)}»…")
        await bot.send_chat_action(chat_id, "upload_photo")
        if room.topic.status == "open" or not room.topic.summary:
            await summary_quick(room)
        caption = f"<b>«{e(room.topic.name)}»</b>\n\n{e((room.topic.summary or '')[:700])}\n\n🗺 {link(room)}"
        await send_canvas(bot, room, chat_id, caption, reply_to=m.message_id)
        await send_pdf(bot, room, chat_id)
        await bot.send_document(chat_id, BufferedInputFile(await export_md(room),
                                                           f"{room.topic.name}.md"))
    elif cmd.sub == "reset":
        room.cancel_work()
        await room.reset()
        await m.reply(f"Тема «{e(room.topic.name)}» очищена, ссылка та же. 🗺 {link(room)}")
    elif cmd.sub == "ask":
        if not question.strip():
            await m.reply("Что спросить? /no_idea ask &lt;имя&gt; &lt;вопрос&gt;")
            return
        await answer_question(m, room, question)


async def answer_question(m: TgMessage, room: TopicRoom, question: str) -> None:
    await m.bot.send_chat_action(m.chat.id, "typing")
    res = await pipeline.answer(room, question)
    node = res.node_ids[0] if res.node_ids else None
    sent = await m.reply(e(res.answer) + _link_suffix(room, node), reply_markup=canvas_button(room, node))
    room.remember_bot_msg(sent.message_id, None)


# ---------- роутер ----------

def setup(registry: Registry) -> Router:
    @router.message(F.text | F.caption)
    async def on_message(m: TgMessage, bot: Bot) -> None:
        text = m.text or m.caption or ""
        me = await bot.me()
        if m.from_user and m.from_user.id == me.id:
            return
        if m.text and commands.is_command(m.text, me.username):
            try:
                await handle_command(m, bot, registry)
            except Exception:
                log.exception("command failed")
                await m.reply("Что-то сломалось, попробуй ещё раз.")
            return
        if m.text and re.match(r"^/(start|help)(@\w+)?(\s|$)", m.text):
            addressed = re.match(r"^/\w+@(\w+)", m.text)
            if m.chat.type == "private" or (addressed and addressed.group(1).lower() == me.username.lower()):
                await m.reply(e(HELP))
            return
        if text.startswith("/"):
            return                                    # чужие команды
        reply = m.reply_to_message
        if reply and reply.from_user and reply.from_user.id == me.id:
            found = registry.room_for_bot_reply(m.chat.id, reply.message_id)
            room, conflict_id = found if found else (registry.current(m.chat.id), None)
            if room is None:
                return
            if conflict_id and room.topic.status == "open":
                # ответ на вопрос бота по конфликту -> RESOLVE
                who, text = split_role(text, author_name(m))
                await room.on_message(who, text, tg_user_id=m.from_user.id,
                                      tg_message_id=m.message_id, reply_to_tg=reply.message_id,
                                      resolves_conflict=conflict_id)
                return
            if conflict_id is None and found is not None:
                await answer_question(m, room, text)  # реплай на другое сообщение бота = ask
                return
        room = registry.current(m.chat.id)
        if room is None:
            return
        who, text = split_role(text, author_name(m))
        await room.on_message(who, text,
                              tg_user_id=m.from_user.id if m.from_user else None,
                              tg_message_id=m.message_id,
                              reply_to_tg=reply.message_id if reply else None)

    return router


async def run_polling(registry: Registry) -> None:
    bot = Bot(config.TG_BOT_TOKEN, default=DefaultBotProperties(
        parse_mode="HTML", link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(setup(registry))
    registry.notifier = TelegramNotifier(bot, registry)
    me = await bot.get_me()
    try:
        await bot.set_my_commands([
            BotCommand(command="no_idea", description="start|stop|export|ask|list <тема> — канвас обсуждения"),
            BotCommand(command="help", description="Как пользоваться"),
        ])
    except Exception:  # noqa: BLE001
        log.exception("set_my_commands failed")
    log.info("bot @%s started (long polling)", me.username)
    try:
        await dp.start_polling(bot, handle_signals=False,
                               allowed_updates=["message"])
    finally:
        await bot.session.close()
