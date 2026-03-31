"""
Subscription handlers — add, remove, check subscriptions.
"""
import logging
from datetime import datetime, timedelta

import database as db
from config import OWNER_ID
from utils.keyboards import subscription_menu_keyboard

logger = logging.getLogger(__name__)


def register(bot, admin_ids, subscriptions, bot_locked_ref):
    """Register subscription command handlers."""

    @bot.message_handler(commands=['subscriptions'])
    def cmd_subscriptions(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, "🔒 Admin only.")
            return
        bot.reply_to(message, "💳 **Subscription Management**",
                      reply_markup=subscription_menu_keyboard(), parse_mode='Markdown')

    def process_add_sub(message):
        if message.from_user.id not in admin_ids:
            return
        if message.text and message.text.lower() == '/cancel':
            bot.reply_to(message, "Cancelled.")
            return
        try:
            parts = message.text.split()
            if len(parts) != 2:
                raise ValueError("Format: `user_id days`")
            sub_user_id = int(parts[0])
            days = int(parts[1])
            if sub_user_id <= 0 or days <= 0:
                raise ValueError("Must be positive")

            current_expiry = subscriptions.get(sub_user_id)
            start = datetime.now()
            if current_expiry and current_expiry > start:
                start = current_expiry
            new_expiry = start + timedelta(days=days)

            db.save_subscription(sub_user_id, new_expiry)
            subscriptions[sub_user_id] = new_expiry

            bot.reply_to(message,
                         f"✅ Subscription for `{sub_user_id}` extended by {days} days.\n"
                         f"📅 Expires: {new_expiry:%Y-%m-%d}",
                         parse_mode='Markdown')
            try:
                bot.send_message(sub_user_id,
                                 f"⭐ Subscription activated! {days} days added.\n"
                                 f"📅 Expires: {new_expiry:%Y-%m-%d}")
            except Exception:
                pass
        except ValueError as e:
            bot.reply_to(message, f"❌ Invalid: {e}. Format: `user_id days` or /cancel", parse_mode='Markdown')
            msg = bot.send_message(message.chat.id, "Enter User ID & days, or /cancel.")
            bot.register_next_step_handler(msg, process_add_sub)

    def process_remove_sub(message):
        if message.from_user.id not in admin_ids:
            return
        if message.text and message.text.lower() == '/cancel':
            bot.reply_to(message, "Cancelled.")
            return
        try:
            uid = int(message.text.strip())
            if uid not in subscriptions:
                bot.reply_to(message, f"⚠️ User `{uid}` has no active subscription.", parse_mode='Markdown')
                return
            db.remove_subscription(uid)
            subscriptions.pop(uid, None)
            bot.reply_to(message, f"✅ Subscription removed for `{uid}`.", parse_mode='Markdown')
            try:
                bot.send_message(uid, "ℹ️ Your subscription has been removed by admin.")
            except Exception:
                pass
        except ValueError:
            bot.reply_to(message, "❌ Invalid ID. Send number or /cancel.")
            msg = bot.send_message(message.chat.id, "Enter User ID or /cancel.")
            bot.register_next_step_handler(msg, process_remove_sub)

    def process_check_sub(message):
        if message.from_user.id not in admin_ids:
            return
        if message.text and message.text.lower() == '/cancel':
            bot.reply_to(message, "Cancelled.")
            return
        try:
            uid = int(message.text.strip())
            if uid in subscriptions:
                expiry = subscriptions[uid]
                if expiry > datetime.now():
                    days_left = (expiry - datetime.now()).days
                    bot.reply_to(message,
                                 f"⭐ User `{uid}` — Active subscription.\n"
                                 f"📅 Expires: {expiry:%Y-%m-%d %H:%M} ({days_left} days left)",
                                 parse_mode='Markdown')
                else:
                    bot.reply_to(message,
                                 f"⚠️ User `{uid}` — Expired ({expiry:%Y-%m-%d})",
                                 parse_mode='Markdown')
                    db.remove_subscription(uid)
                    subscriptions.pop(uid, None)
            else:
                bot.reply_to(message, f"ℹ️ User `{uid}` has no subscription.", parse_mode='Markdown')
        except ValueError:
            bot.reply_to(message, "❌ Invalid ID.")
            msg = bot.send_message(message.chat.id, "Enter User ID or /cancel.")
            bot.register_next_step_handler(msg, process_check_sub)

    # Store for callback access
    bot._sub_add = process_add_sub
    bot._sub_remove = process_remove_sub
    bot._sub_check = process_check_sub
