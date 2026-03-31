"""
Admin handlers — admin panel, bans, dashboard, broadcast, maintenance, approvals.
"""
import os
import time
import re
import threading
import logging
from datetime import datetime

import telebot
from telebot import types

import database as db
from config import OWNER_ID, UPDATE_CHANNEL
from utils.helpers import get_uptime, get_system_stats
from utils.keyboards import (
    admin_panel_keyboard, ban_manager_keyboard, approval_buttons,
    maintenance_keyboard, main_menu_inline
)
from services.approval import handle_approve, handle_reject
from services.script_runner import (
    stop_all_user_scripts, get_running_count, get_all_running, running_scripts
)
from services.file_manager import delete_all_user_files

logger = logging.getLogger(__name__)


def register(bot, admin_ids, subscriptions, bot_locked_ref):
    """Register admin command handlers."""

    # ==================== ADMIN PANEL ====================

    @bot.message_handler(commands=['adminpanel'])
    def cmd_admin_panel(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, "🔒 Admin only.")
            return
        bot.reply_to(message, "👑 **Admin Panel**\nManage admins and settings.",
                      reply_markup=admin_panel_keyboard(bot_locked_ref[0], db.get_pending_count()), parse_mode='Markdown')

    # ==================== BROADCAST ====================

    @bot.message_handler(commands=['broadcast'])
    def cmd_broadcast(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, "🔒 Admin only.")
            return
        msg = bot.reply_to(message, "📢 Send the message to broadcast to all users.\n/cancel to abort.")
        bot.register_next_step_handler(msg, process_broadcast)

    def process_broadcast(message):
        if message.from_user.id not in admin_ids:
            return
        if message.text and message.text.lower() == '/cancel':
            bot.reply_to(message, "Broadcast cancelled.")
            return

        content = message.text
        if not content and not (message.photo or message.video or message.document):
            bot.reply_to(message, "❌ Empty message. Send text or media, or /cancel.")
            msg = bot.send_message(message.chat.id, "Send broadcast message or /cancel.")
            bot.register_next_step_handler(msg, process_broadcast)
            return

        users = db.get_all_active_users()
        target_count = len(users)

        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
        )

        preview = (content[:800] if content else "(Media message)")
        bot.reply_to(message,
                      f"📢 **Broadcast Preview:**\n\n```\n{preview}\n```\n\n"
                      f"Send to **{target_count}** users?",
                      reply_markup=markup, parse_mode='Markdown')

    def execute_broadcast(text, photo_id, video_id, caption, admin_chat_id):
        users = list(db.get_all_active_users())
        sent = 0
        failed = 0
        blocked = 0
        start = time.time()

        for i, uid in enumerate(users):
            try:
                if text:
                    bot.send_message(uid, text, parse_mode='Markdown')
                elif photo_id:
                    bot.send_photo(uid, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
                elif video_id:
                    bot.send_video(uid, video_id, caption=caption, parse_mode='Markdown' if caption else None)
                sent += 1
            except telebot.apihelper.ApiTelegramException as e:
                err = str(e).lower()
                if any(s in err for s in ["blocked", "deactivated", "chat not found", "kicked", "restricted"]):
                    blocked += 1
                elif "flood control" in err or "too many requests" in err:
                    retry = 5
                    match = re.search(r"retry after (\d+)", err)
                    if match:
                        retry = int(match.group(1)) + 1
                    time.sleep(retry)
                    try:
                        if text:
                            bot.send_message(uid, text, parse_mode='Markdown')
                        sent += 1
                    except Exception:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            if (i + 1) % 25 == 0:
                time.sleep(1.5)
            elif i % 5 == 0:
                time.sleep(0.2)

        duration = round(time.time() - start, 2)
        result = (f"📢 **Broadcast Complete!**\n\n"
                  f"✅ Sent: {sent}\n❌ Failed: {failed}\n"
                  f"🚫 Blocked: {blocked}\n👥 Targets: {len(users)}\n"
                  f"⏱ Duration: {duration}s")
        try:
            bot.send_message(admin_chat_id, result, parse_mode='Markdown')
        except Exception:
            pass

    # Store broadcast executor for callback use
    bot._broadcast_executor = execute_broadcast

    # ==================== LOCK/UNLOCK ====================

    @bot.message_handler(commands=['lockbot'])
    def cmd_lock(message):
        if message.from_user.id not in admin_ids:
            return
        bot_locked_ref[0] = True
        bot.reply_to(message, "🔒 Bot has been **locked**.", parse_mode='Markdown')

    @bot.message_handler(commands=['unlockbot'])
    def cmd_unlock(message):
        if message.from_user.id not in admin_ids:
            return
        bot_locked_ref[0] = False
        bot.reply_to(message, "🔓 Bot has been **unlocked**.", parse_mode='Markdown')

    # ==================== BAN SYSTEM ====================

    @bot.message_handler(commands=['ban'])
    def cmd_ban(message):
        if message.from_user.id not in admin_ids:
            return
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            bot.reply_to(message, "Usage: `/ban <user_id> [reason]`", parse_mode='Markdown')
            return
        try:
            target_id = int(parts[1])
            reason = parts[2] if len(parts) > 2 else "No reason provided"
            if target_id == OWNER_ID:
                bot.reply_to(message, "❌ Cannot ban the owner.")
                return
            # Ban user
            db.ban_user(target_id, message.from_user.id, reason)
            # Stop all their scripts
            stopped = stop_all_user_scripts(target_id)
            bot.reply_to(message,
                         f"🚫 User `{target_id}` **banned**.\n"
                         f"📝 Reason: {reason}\n"
                         f"🔴 Stopped {stopped} running scripts.",
                         parse_mode='Markdown')
            try:
                bot.send_message(target_id, f"🚫 You have been **banned**.\n📝 Reason: {reason}", parse_mode='Markdown')
            except Exception:
                pass
        except ValueError:
            bot.reply_to(message, "❌ Invalid user ID.")

    @bot.message_handler(commands=['unban'])
    def cmd_unban(message):
        if message.from_user.id not in admin_ids:
            return
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Usage: `/unban <user_id>`", parse_mode='Markdown')
            return
        try:
            target_id = int(parts[1])
            if db.unban_user(target_id):
                bot.reply_to(message, f"✅ User `{target_id}` has been **unbanned**.", parse_mode='Markdown')
                try:
                    bot.send_message(target_id, "✅ You have been **unbanned**. Welcome back!", parse_mode='Markdown')
                except Exception:
                    pass
            else:
                bot.reply_to(message, f"⚠️ User `{target_id}` was not banned.", parse_mode='Markdown')
        except ValueError:
            bot.reply_to(message, "❌ Invalid user ID.")

    @bot.message_handler(commands=['banlist'])
    def cmd_banlist(message):
        if message.from_user.id not in admin_ids:
            return
        banned = db.get_banned_users()
        if not banned:
            bot.reply_to(message, "✅ No banned users.")
            return
        lines = []
        for b in banned[:20]:
            lines.append(f"• `{b['user_id']}` — {b['reason']} ({b['banned_at'][:10]})")
        text = "🚫 **Banned Users:**\n\n" + "\n".join(lines)
        if len(banned) > 20:
            text += f"\n\n...and {len(banned) - 20} more."
        bot.reply_to(message, text, parse_mode='Markdown')

    # ==================== DASHBOARD ====================

    @bot.message_handler(commands=['dashboard'])
    def cmd_dashboard(message):
        if message.from_user.id not in admin_ids:
            return
        _send_dashboard(message.chat.id, bot)

    # ==================== PENDING FILES ====================

    @bot.message_handler(commands=['pending'])
    def cmd_pending(message):
        if message.from_user.id not in admin_ids:
            return
        _show_pending_files(message.chat.id, bot)

    # ==================== RUN ALL SCRIPTS ====================

    @bot.message_handler(commands=['runall'])
    def cmd_run_all(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, "🔒 Admin only.")
            return
        _run_all_scripts(message.from_user.id, message.chat.id, bot)

    # Store helpers for callback access
    bot._send_dashboard = _send_dashboard
    bot._show_pending_files = _show_pending_files
    bot._run_all_scripts = _run_all_scripts
    bot._process_broadcast = process_broadcast


def _send_dashboard(chat_id, bot):
    """Send system dashboard."""
    stats = get_system_stats()
    uptime_str = get_uptime()
    running = get_all_running()
    running_count = len(running)
    total_users = db.get_total_user_count()
    total_files = db.get_total_file_count()
    pending = db.get_pending_count()
    banned = len(db.get_banned_users())
    maint = db.get_maintenance_status()

    dashboard = (
        f"📈 **System Dashboard**\n\n"
        f"⏱ Uptime: `{uptime_str}`\n\n"
        f"🖥 **Server Resources**\n"
        f"├ CPU: `{stats['cpu_percent']}%`\n"
        f"├ RAM: `{stats['memory_used_gb']}/{stats['memory_total_gb']} GB` ({stats['memory_percent']}%)\n"
        f"└ Disk: `{stats['disk_used_gb']}/{stats['disk_total_gb']} GB` ({stats['disk_percent']}%)\n\n"
        f"📊 **Bot Statistics**\n"
        f"├ Users: `{total_users}`\n"
        f"├ Files: `{total_files}`\n"
        f"├ Running Scripts: `{running_count}`\n"
        f"├ Pending Approvals: `{pending}`\n"
        f"├ Banned Users: `{banned}`\n"
        f"└ Maintenance: `{'ON' if maint['enabled'] else 'OFF'}`\n"
    )

    if running:
        dashboard += "\n🟢 **Active Scripts:**\n"
        for r in running[:10]:
            pid_str = f"PID:{r['pid']}" if r['pid'] else "?"
            dashboard += f"├ `{r['file_name']}` ({r['file_type']}) — User `{r['user_id']}` ({pid_str})\n"
        if len(running) > 10:
            dashboard += f"└ ...and {len(running) - 10} more.\n"

    bot.send_message(chat_id, dashboard, parse_mode='Markdown')


def _show_pending_files(chat_id, bot):
    """Show all pending approval files."""
    pending = db.get_pending_approvals()
    if not pending:
        bot.send_message(chat_id, "✅ No pending file approvals.")
        return

    for p in pending[:20]:
        text = (
            f"📋 **Pending Approval #{p['id']}**\n\n"
            f"👤 User: `{p['user_id']}`\n"
            f"📄 File: `{p['file_name']}`\n"
            f"📁 Type: `{p['file_type']}`\n"
            f"🕐 Submitted: {p['submitted_at'][:16]}"
        )
        markup = approval_buttons(p['id'])
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

    if len(pending) > 20:
        bot.send_message(chat_id, f"...and {len(pending) - 20} more pending files.")


def _run_all_scripts(admin_id, chat_id, bot):
    """Start all stopped user scripts."""
    from services.script_runner import is_running as _is_running, run_python_script, run_js_script
    from services.file_manager import get_user_folder
    import os

    bot.send_message(chat_id, "🟢 Starting all user scripts... This may take a while.")

    all_files = db.get_all_user_files()
    started = 0
    skipped = 0
    errors = []

    for user_id, files in all_files.items():
        user_folder = get_user_folder(user_id)
        for file_name, file_type in files:
            if not _is_running(user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_python_script,
                                             args=(file_path, user_id, user_folder, file_name, bot, chat_id)).start()
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script,
                                             args=(file_path, user_id, user_folder, file_name, bot, chat_id)).start()
                        started += 1
                        time.sleep(0.7)
                    except Exception as e:
                        errors.append(f"`{file_name}` (User {user_id})")
                        skipped += 1
                else:
                    skipped += 1

    summary = (
        f"🟢 **Run All Complete**\n\n"
        f"▶️ Started: {started}\n"
        f"⏭ Skipped: {skipped}"
    )
    if errors:
        summary += "\n\n❌ Errors:\n" + "\n".join(f"• {e}" for e in errors[:5])
    bot.send_message(chat_id, summary, parse_mode='Markdown')
