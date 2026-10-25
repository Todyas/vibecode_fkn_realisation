"""Telegram-бот: роутер команд /no_idea, приём сообщений, отправка фото/файлов, cooldown."""
import asyncio
import logging
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message as TgMessage)

from . import commands, config, pipeline, render, snapshot
from .topics import Registry, TopicError, TopicRoom

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Я веду канвас обсуждения темы прямо из чата.\n\n"
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


def _link_suffix(room: TopicRoom, node_id: str | None = None) -> str:
    url = config.canvas_url(room.topic.web_token, node_id)
    return "" if url.startswith("https://") else f"\n{url}"


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
            chat_id, text + _link_suffix(room, node), **kwargs))
        if msg is None:
            return None
        cs.last_bot_message_ts = now
        return msg.message_id


# ---------- команды ----------

def _topics_list(reg: Registry, chat_id: int) -> str:
    rooms = reg.topics_of(chat_id)
    if not rooms:
        return "В этом чате ещё нет тем. Начни: /no_idea start <имя>"
    lines = [f"{'🟢' if r.topic.status == 'open' else '⚪️'} {r.topic.name} — {r.url}" for r in rooms]
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
    png = await snapshot.canvas_png(room.topic.web_token)
    caption = caption[:1000]
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
    await bot.send_message(chat_id, caption, reply_markup=markup,
                           reply_to_message_id=reply_to, allow_sending_without_reply=True)


async def close_topic(room: TopicRoom, reg: Registry) -> str:
    """stop: отцепить от чата -> дождаться пачек -> Consolidator -> closed -> Summarizer."""
    reg.detach(room)

    async def job():
        await pipeline.consolidate(room)
        summary = await pipeline.summarize(room)
        await room.set_topic_status("closed")
        return summary
    return await room.run_job(job)


async def export_md(room: TopicRoom) -> bytes:
    return render.markdown(room.topic).encode("utf-8")


async def handle_command(m: TgMessage, bot: Bot, reg: Registry) -> None:
    cmd = commands.parse(m.text or "")
    chat_id = m.chat.id
    if cmd.sub == "help":
        cur = reg.current(chat_id)
        tail = f"\n\nСейчас слушаю: «{cur.topic.name}» — {cur.url}" if cur else "\n\nСейчас тема не открыта."
        await m.reply(HELP + tail, disable_web_page_preview=True)
        return
    if cmd.sub == "list":
        await m.reply(_topics_list(reg, chat_id), disable_web_page_preview=True)
        return
    if cmd.sub == "start":
        name = cmd.raw_args if not cmd.quoted else cmd.name
        if not name:
            await m.reply("Напиши имя темы: /no_idea start Онбординг")
            return
        try:
            room, reopened = await reg.start(chat_id, name, author_name(m))
        except TopicError as e:
            await m.reply(str(e))
            return
        verb = "Снова слушаю" if reopened else "Слушаю"
        await m.reply(f"{verb} тему «{room.topic.name}». Канвас: {room.url}",
                      reply_markup=canvas_button(room), disable_web_page_preview=True)
        return

    room, question = _resolve(reg, chat_id, cmd)
    if room is None:
        if cmd.name or cmd.sub == "ask":
            await m.reply("Не нашёл такую тему.\n\n" + _topics_list(reg, chat_id),
                          disable_web_page_preview=True)
        else:
            await m.reply("Сейчас тема не открыта. Укажи имя: /no_idea "
                          f"{cmd.sub} <имя>\n\n" + _topics_list(reg, chat_id),
                          disable_web_page_preview=True)
        return

    if cmd.sub == "stop":
        if room.topic.status != "open":
            await m.reply(f"Тема «{room.topic.name}» не открыта.")
            return
        await bot.send_chat_action(chat_id, "upload_photo")
        summary = await close_topic(room, reg)
        caption = f"Тема «{room.topic.name}» закрыта.\n\n{summary}\n\nКанвас: {room.url}"
        await send_canvas(bot, room, chat_id, caption, reply_to=m.message_id)
    elif cmd.sub == "export":
        await bot.send_chat_action(chat_id, "upload_photo")
        if room.topic.status == "open" or not room.topic.summary:
            await room.run_job(lambda: pipeline.summarize(room))
        caption = f"«{room.topic.name}»\n\n{room.topic.summary or ''}\n\nКанвас: {room.url}"
        await send_canvas(bot, room, chat_id, caption, reply_to=m.message_id)
        await bot.send_document(chat_id, BufferedInputFile(await export_md(room),
                                                           f"{room.topic.name}.md"))
    elif cmd.sub == "reset":
        await room.run_job(room.reset)
        await m.reply(f"Тема «{room.topic.name}» очищена, ссылка та же: {room.url}",
                      disable_web_page_preview=True)
    elif cmd.sub == "ask":
        if not question.strip():
            await m.reply("Что спросить? /no_idea ask <имя> <вопрос>")
            return
        await answer_question(m, room, question)


async def answer_question(m: TgMessage, room: TopicRoom, question: str) -> None:
    await m.bot.send_chat_action(m.chat.id, "typing")
    res = await pipeline.answer(room, question)
    node = res.node_ids[0] if res.node_ids else None
    sent = await m.reply(res.answer + _link_suffix(room, node), reply_markup=canvas_button(room, node),
                         disable_web_page_preview=True)
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
    bot = Bot(config.TG_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(setup(registry))
    registry.notifier = TelegramNotifier(bot, registry)
    me = await bot.get_me()
    log.info("bot @%s started (long polling)", me.username)
    try:
        await dp.start_polling(bot, handle_signals=False,
                               allowed_updates=["message"])
    finally:
        await bot.session.close()
