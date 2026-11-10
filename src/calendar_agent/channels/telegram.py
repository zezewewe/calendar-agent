"""Telegram front end. Forward WhatsApp messages / screenshots to the bot; tap Confirm to add them."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from ..agent import Agent
from ..config import Config
from ..models import Attachment, Inbound
from ..render import render, render_agenda

logger = logging.getLogger(__name__)


def run(cfg: Config, agent: Agent) -> None:
    from telegram import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        MenuButtonWebApp,
        Update,
        WebAppInfo,
    )
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

    allowed = cfg.telegram_allowed_users
    if not allowed:
        raise SystemExit("Set TELEGRAM_ALLOWED_USERS: refusing to run an open calendar-writing bot.")

    async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        m = update.effective_message
        uid = str(update.effective_user.id)
        if uid not in allowed:
            return
        atts: list[Attachment] = []
        if m.photo:
            f = await m.photo[-1].get_file()
            atts.append(Attachment(mime="image/jpeg", data=bytes(await f.download_as_bytearray())))
        elif m.document and (m.document.mime_type or "").startswith("image/"):
            f = await m.document.get_file()
            atts.append(Attachment(mime=m.document.mime_type, data=bytes(await f.download_as_bytearray())))
        now = datetime.now(cfg.tz)
        text = m.text or m.caption or ""
        await m.chat.send_action("typing")
        chat = str(m.chat_id)
        replied = m.reply_to_message
        pid = agent.proposal_for_message(chat, str(replied.message_id)) if replied else None
        try:
            if pid and text and not atts:  # reply to a pending proposal = edit it
                proposal = await asyncio.to_thread(agent.revise, pid, uid, text, now)
                if proposal is None:
                    await m.reply_text("That proposal is no longer pending. Send the event again.")
                    return
            else:
                inbound = Inbound(user_id=uid, text=text, attachments=atts, received_at=now)
                proposal = await asyncio.to_thread(agent.propose, inbound)  # blocking LLM + calendar calls
        except Exception as e:  # noqa: BLE001 - surface any failure to the user instead of silence
            await m.reply_text(f"Sorry, that failed: {e}")
            return
        markup = None
        if proposal.events:
            label = "Apply changes" if any(e.action != "create" for e in proposal.events) else "Add to calendar"
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(label, callback_data=f"ok:{proposal.id}"),
                InlineKeyboardButton("Cancel", callback_data=f"no:{proposal.id}"),
            ]])
        footer = "\n\n(Reply to this message to change it.)" if proposal.events else ""
        sent = await m.reply_text(render(proposal, cfg.family_members) + footer, reply_markup=markup)
        if proposal.events:
            agent.link_message(chat, str(sent.message_id), proposal.id)

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        uid = str(q.from_user.id)
        if uid not in allowed:
            return
        await q.answer()
        action, _, pid = q.data.partition(":")
        if action == "ok":
            try:
                ids = await asyncio.to_thread(agent.confirm, pid, uid)
            except Exception as e:  # noqa: BLE001
                await q.edit_message_text(f"{q.message.text}\n\nFailed to apply: {e}")
                return
            if ids is None:
                await q.edit_message_text(f"{q.message.text}\n\n(Already handled or expired.)")
                return
            undo = InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"undo:{pid}")]])
            await q.edit_message_text(f"{q.message.text}\n\nApplied {len(ids)} change(s).", reply_markup=undo)
        elif action == "no":
            done = await asyncio.to_thread(agent.cancel, pid, uid)
            await q.edit_message_text(f"{q.message.text}\n\n{'Cancelled.' if done else '(Already handled.)'}")
        elif action == "undo":
            n = await asyncio.to_thread(agent.undo, pid)
            await q.edit_message_text(f"Reverted {n} change(s).")

    def agenda_handler(days: int):
        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            if str(update.effective_user.id) not in allowed:
                return
            now = datetime.now(cfg.tz)
            events = await asyncio.to_thread(agent.agenda, now, now + timedelta(days=days))
            await update.effective_message.reply_text(render_agenda(events, cfg.family_members))
        return handler

    async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Forward a note, poster or chat to add events. Ask a question to see the calendar, "
            "or use /agenda (next 7 days), /list (next 30 days) and /calendar (the visual calendar)."
        )

    async def on_calendar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if str(update.effective_user.id) not in allowed:
            return
        url = cfg.menu_button_url
        if not url:
            await update.effective_message.reply_text(
                "Set CALENDAR_WEB_URL to an https URL to enable the calendar web app."
            )
            return
        await update.effective_message.reply_text(
            "Open the visual calendar:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Calendar", web_app=WebAppInfo(url=url))]]),
        )

    async def post_init(app: Application) -> None:
        url = cfg.menu_button_url  # open the web UI as a Mini App from the chat menu
        if not url:
            if cfg.calendar_web_url:
                logger.warning("Ignoring CALENDAR_WEB_URL=%r: Telegram menu buttons require an https URL.", cfg.calendar_web_url)
            return
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Calendar", web_app=WebAppInfo(url=url)))
        except Exception as e:  # noqa: BLE001 - a bad menu button must not take the bot down
            logger.warning("Could not set the calendar menu button (%s); continuing without it.", e)

    app = Application.builder().token(cfg.telegram_bot_token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("agenda", agenda_handler(7)))
    app.add_handler(CommandHandler("list", agenda_handler(30)))
    app.add_handler(CommandHandler("calendar", on_calendar))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.IMAGE, on_message))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()
