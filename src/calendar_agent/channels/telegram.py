"""Telegram front end. Forward WhatsApp messages / screenshots to the bot; tap Confirm to add them."""
from __future__ import annotations

import asyncio
from datetime import datetime

from ..agent import Agent
from ..config import Config
from ..models import Attachment, Inbound
from ..render import render


def run(cfg: Config, agent: Agent) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

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
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("Add to calendar", callback_data=f"ok:{proposal.id}"),
                InlineKeyboardButton("Cancel", callback_data=f"no:{proposal.id}"),
            ]])
        footer = "\n\n(Reply to this message to change it.)" if proposal.events else ""
        sent = await m.reply_text(render(proposal) + footer, reply_markup=markup)
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
                await q.edit_message_text(f"{q.message.text}\n\nFailed to add: {e}")
                return
            if ids is None:
                await q.edit_message_text(f"{q.message.text}\n\n(Already handled or expired.)")
                return
            undo = InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"undo:{pid}")]])
            await q.edit_message_text(f"{q.message.text}\n\nAdded {len(ids)} event(s).", reply_markup=undo)
        elif action == "no":
            done = await asyncio.to_thread(agent.cancel, pid, uid)
            await q.edit_message_text(f"{q.message.text}\n\n{'Cancelled.' if done else '(Already handled.)'}")
        elif action == "undo":
            n = await asyncio.to_thread(agent.undo, pid)
            await q.edit_message_text(f"Removed {n} event(s).")

    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.IMAGE, on_message))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()
