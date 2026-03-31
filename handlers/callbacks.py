"""
Callback handler — routes all inline button callbacks to appropriate logic.
"""
import os
import time
import threading
import logging
from datetime import datetime

import telebot
from telebot import types

import database as db
from config import OWNER_ID, YOUR_USERNAME, UPDATE_CHANNEL
from utils.helpers import get_uptime, get_user_status, get_file_limit, format_limit
from utils.keyboards import (
    main_menu_inline, file_control_buttons, admin_panel_keyboard,
    subscription_menu_keyboard, ban_manager_keyboard, approval_buttons,
    version_list_keyboard, maintenance_keyboard
)
from services.script_runner import (
    is_running, stop_script, get_script_resource_usage,
    run_python_script, run_js_script, get_running_count, get_user_running_count
)
from services.file_manager import (
    get_user_folder, delete_user_file, rollback_to_version
)
from services.approval import handle_approve, handle_reject

logger = logging.getLogger(__name__)


def register(bot, admin_ids, subscriptions, bot_locked_ref):
    """Register the main callback query handler."""

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        user_id = call.from_user.id
        data = call.data
        chat_id = call.message.chat.id

        # Ban check
        if db.is_banned(user_id):
            bot.answer_callback_query(call.id, "🚫 You are banned.", show_alert=True)
            return

        # Maintenance check (allow some actions)
        safe_actions = {'back_to_main', 'speed', 'stats', 'uptime'}
        maint = db.get_maintenance_status()
        if maint['enabled'] and user_id not in admin_ids and data not in safe_actions:
            bot.answer_callback_query(call.id, "🛠 Bot is under maintenance.", show_alert=True)
            return

        # Lock check
        if bot_locked_ref[0] and user_id not in admin_ids and data not in safe_actions:
            bot.answer_callback_query(call.id, "🔒 Bot is locked.", show_alert=True)
            return

        try:
            # ==================== GENERAL ====================
            if data == 'upload':
                _cb_upload(call, bot, admin_ids, subscriptions)
            elif data == 'check_files':
                _cb_check_files(call, bot)
            elif data == 'speed':
                _cb_speed(call, bot, admin_ids, subscriptions, bot_locked_ref)
            elif data == 'stats':
                _cb_stats(call, bot, admin_ids, bot_locked_ref)
            elif data == 'uptime':
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, f"⏱ Uptime: `{get_uptime()}`", parse_mode='Markdown')
            elif data == 'back_to_main':
                _cb_back_to_main(call, bot, admin_ids, subscriptions, bot_locked_ref)

            # ==================== FILE CONTROLS ====================
            elif data.startswith('file_'):
                _cb_file_control(call, bot, admin_ids)
            elif data.startswith('start_'):
                _cb_start_script(call, bot, admin_ids)
            elif data.startswith('stop_'):
                _cb_stop_script(call, bot, admin_ids)
            elif data.startswith('restart_'):
                _cb_restart_script(call, bot, admin_ids)
            elif data.startswith('delete_'):
                _cb_delete_script(call, bot, admin_ids)
            elif data.startswith('logs_'):
                _cb_logs(call, bot, admin_ids)
            elif data.startswith('resources_'):
                _cb_resources(call, bot, admin_ids)
            elif data.startswith('toggle_ar_'):
                _cb_toggle_auto_restart(call, bot, admin_ids)
            elif data.startswith('versions_'):
                _cb_versions(call, bot, admin_ids)
            elif data.startswith('rollback_'):
                _cb_rollback(call, bot, admin_ids)
            elif data.startswith('envs_'):
                _cb_envs(call, bot, admin_ids)
            elif data.startswith('addenv_'):
                _cb_addenv(call, bot, admin_ids)
            elif data.startswith('delenv_'):
                _cb_delenv(call, bot, admin_ids)
            elif data.startswith('streamlog_'):
                _cb_streamlog(call, bot, admin_ids)
            elif data.startswith('stoplog_'):
                _cb_stoplog(call, bot, admin_ids)

            # ==================== APPROVAL SYSTEM ====================
            elif data.startswith('approve_'):
                _cb_approve(call, bot, admin_ids)
            elif data.startswith('reject_'):
                _cb_reject_init(call, bot, admin_ids)

            # ==================== ADMIN ====================
            elif data == 'admin_panel':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    pending = db.get_pending_count()
                    try:
                        bot.edit_message_text("👑 **Admin Panel**", chat_id, call.message.message_id,
                                              reply_markup=admin_panel_keyboard(bot_locked_ref[0], pending), parse_mode='Markdown')
                    except Exception:
                        bot.send_message(chat_id, "👑 **Admin Panel**",
                                         reply_markup=admin_panel_keyboard(bot_locked_ref[0], pending), parse_mode='Markdown')
            
            elif data == 'manage_admins':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    from utils.keyboards import manage_admins_keyboard
                    try:
                        bot.edit_message_text("👥 **Manage Admins**", chat_id, call.message.message_id,
                                              reply_markup=manage_admins_keyboard(), parse_mode='Markdown')
                    except Exception:
                        pass

            elif data == 'subscription':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    try:
                        bot.edit_message_text("💳 **Subscription Management**", chat_id, call.message.message_id,
                                              reply_markup=subscription_menu_keyboard(), parse_mode='Markdown')
                    except Exception:
                        pass

            elif data == 'broadcast':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "📢 Send the broadcast message or /cancel.")
                    bot.register_next_step_handler(msg, bot._process_broadcast)

            elif data.startswith('confirm_broadcast_'):
                if user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
                    return
                try:
                    original = call.message.reply_to_message
                    if not original:
                        raise ValueError("Could not get original message.")
                    text = original.text
                    photo_id = original.photo[-1].file_id if original.photo else None
                    video_id = original.video.file_id if original.video else None
                    caption = original.caption if (photo_id or video_id) else None
                    bot.answer_callback_query(call.id, "Broadcasting...")
                    bot.edit_message_text(f"📢 Broadcasting...", chat_id, call.message.message_id, reply_markup=None)
                    threading.Thread(target=bot._broadcast_executor,
                                     args=(text, photo_id, video_id, caption, chat_id)).start()
                except Exception as e:
                    bot.edit_message_text(f"❌ Error: {e}", chat_id, call.message.message_id, reply_markup=None)

            elif data == 'cancel_broadcast':
                bot.answer_callback_query(call.id, "Cancelled.")
                bot.delete_message(chat_id, call.message.message_id)

            elif data == 'lock_bot':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot_locked_ref[0] = True
                    bot.answer_callback_query(call.id, "🔒 Bot locked.")
                    pending = db.get_pending_count()
                    try:
                        bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                                      reply_markup=main_menu_inline(user_id, admin_ids, True, pending))
                    except Exception:
                        pass

            elif data == 'unlock_bot':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot_locked_ref[0] = False
                    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
                    pending = db.get_pending_count()
                    try:
                        bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                                      reply_markup=main_menu_inline(user_id, admin_ids, False, pending))
                    except Exception:
                        pass

            elif data == 'run_all_scripts':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    bot._run_all_scripts(user_id, chat_id, bot)

            elif data == 'pending_files':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    bot._show_pending_files(chat_id, bot)

            elif data == 'dashboard':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    bot._send_dashboard(chat_id, bot)

            elif data == 'ban_manager':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    try:
                        bot.edit_message_text("🚫 **Ban Manager**", chat_id, call.message.message_id,
                                              reply_markup=ban_manager_keyboard(), parse_mode='Markdown')
                    except Exception:
                        bot.send_message(chat_id, "🚫 **Ban Manager**",
                                         reply_markup=ban_manager_keyboard(), parse_mode='Markdown')

            elif data == 'ban_user':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter User ID to ban (optionally with reason):\n`user_id reason`\nor /cancel", parse_mode='Markdown')
                    bot.register_next_step_handler(msg, _process_ban_from_callback, bot, admin_ids)

            elif data == 'unban_user':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter User ID to unban, or /cancel.")
                    bot.register_next_step_handler(msg, _process_unban_from_callback, bot)

            elif data == 'ban_list':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    banned = db.get_banned_users()
                    if not banned:
                        bot.send_message(chat_id, "✅ No banned users.")
                    else:
                        lines = [f"• `{b['user_id']}` — {b['reason']}" for b in banned[:20]]
                        bot.send_message(chat_id, "🚫 **Banned Users:**\n\n" + "\n".join(lines), parse_mode='Markdown')

            # Admin management
            elif data == 'add_admin':
                if user_id != OWNER_ID:
                    bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
                    return
                bot.answer_callback_query(call.id)
                msg = bot.send_message(chat_id, "Enter User ID to promote to Admin, or /cancel.")
                bot.register_next_step_handler(msg, _process_add_admin, bot, admin_ids)

            elif data == 'remove_admin':
                if user_id != OWNER_ID:
                    bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
                    return
                bot.answer_callback_query(call.id)
                msg = bot.send_message(chat_id, "Enter Admin ID to remove, or /cancel.")
                bot.register_next_step_handler(msg, _process_remove_admin, bot, admin_ids)

            elif data == 'list_admins':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    admin_list = "\n".join(
                        f"• `{aid}` {'👑 (Owner)' if aid == OWNER_ID else ''}"
                        for aid in sorted(admin_ids)
                    )
                    try:
                        from utils.keyboards import manage_admins_keyboard
                        bot.edit_message_text(
                            f"👑 **Admins:**\n\n{admin_list or '(None)'}",
                            chat_id, call.message.message_id,
                            reply_markup=manage_admins_keyboard(), parse_mode='Markdown'
                        )
                    except Exception:
                        pass

            # Subscription callbacks
            elif data == 'add_subscription':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter `user_id days` or /cancel.", parse_mode='Markdown')
                    bot.register_next_step_handler(msg, bot._sub_add)

            elif data == 'remove_subscription':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter User ID to remove sub, or /cancel.")
                    bot.register_next_step_handler(msg, bot._sub_remove)

            elif data == 'check_subscription':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter User ID to check, or /cancel.")
                    bot.register_next_step_handler(msg, bot._sub_check)

            # Maintenance
            elif data == 'maintenance_mode':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    maint_status = db.get_maintenance_status()
                    try:
                        bot.edit_message_text(
                            f"🛠 **Maintenance Mode**\nStatus: `{'ON' if maint_status['enabled'] else 'OFF'}`\n"
                            f"Message: _{maint_status['message']}_",
                            chat_id, call.message.message_id,
                            reply_markup=maintenance_keyboard(maint_status['enabled']),
                            parse_mode='Markdown'
                        )
                    except Exception:
                        pass

            elif data == 'maintenance_on':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    db.set_maintenance(True)
                    bot.answer_callback_query(call.id, "🔴 Maintenance ON")
                    maint_status = db.get_maintenance_status()
                    try:
                        bot.edit_message_text(
                            f"🛠 **Maintenance Mode**\nStatus: `ON`\nMessage: _{maint_status['message']}_",
                            chat_id, call.message.message_id,
                            reply_markup=maintenance_keyboard(True), parse_mode='Markdown'
                        )
                    except Exception:
                        pass

            elif data == 'maintenance_off':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    db.set_maintenance(False)
                    bot.answer_callback_query(call.id, "🟢 Maintenance OFF")
                    try:
                        bot.edit_message_text(
                            "🛠 **Maintenance Mode**\nStatus: `OFF`",
                            chat_id, call.message.message_id,
                            reply_markup=maintenance_keyboard(False), parse_mode='Markdown'
                        )
                    except Exception:
                        pass

            elif data == 'maintenance_msg':
                _admin_required(call, bot, admin_ids)
                if user_id in admin_ids:
                    bot.answer_callback_query(call.id)
                    msg = bot.send_message(chat_id, "Enter new maintenance message, or /cancel.")
                    bot.register_next_step_handler(msg, _process_maintenance_msg, bot)

            else:
                bot.answer_callback_query(call.id, "Unknown action.")
                logger.warning(f"Unhandled callback: {data} from {user_id}")

        except Exception as e:
            logger.error(f"Callback error '{data}' user {user_id}: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, "❌ Error occurred.", show_alert=True)
            except Exception:
                pass


# ==================== CALLBACK HELPERS ====================

def _admin_required(call, bot, admin_ids):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "🔒 Admin required.", show_alert=True)


def _cb_upload(call, bot, admin_ids, subscriptions):
    user_id = call.from_user.id
    limits = {'free': 20, 'subscribed': 15, 'admin': 999}
    file_limit = get_file_limit(user_id, OWNER_ID, admin_ids, subscriptions, limits)
    current = db.get_user_file_count(user_id)
    if current >= file_limit:
        bot.answer_callback_query(call.id, f"Limit reached ({current}/{format_limit(file_limit)}).", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
                     "📤 Send your `.py`, `.js`, or `.zip` file.\n⚠️ Requires admin approval.")


def _cb_check_files(call, bot):
    from handlers.files import _show_user_files
    bot.answer_callback_query(call.id)
    _show_user_files(call.from_user.id, call.message.chat.id, bot, message_id=call.message.message_id)


def _cb_speed(call, bot, admin_ids, subscriptions, bot_locked_ref):
    user_id = call.from_user.id
    start = time.time()
    try:
        bot.edit_message_text("⚡ Testing speed...", call.message.chat.id, call.message.message_id)
        latency = round((time.time() - start) * 1000, 2)
        status, _ = get_user_status(user_id, OWNER_ID, admin_ids, subscriptions)
        pending = db.get_pending_count() if user_id in admin_ids else 0
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            f"⚡ **Bot Speed**\n\n"
            f"📡 Latency: `{latency}ms`\n"
            f"🔒 Bot: `{'Locked' if bot_locked_ref[0] else 'Unlocked'}`\n"
            f"🏷 Level: {status}",
            call.message.chat.id, call.message.message_id,
            reply_markup=main_menu_inline(user_id, admin_ids, bot_locked_ref[0], pending),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Speed test error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)


def _cb_stats(call, bot, admin_ids, bot_locked_ref):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    total_users = db.get_total_user_count()
    total_files = db.get_total_file_count()
    running_count = get_running_count()
    user_running = get_user_running_count(user_id)

    stats = (
        f"📊 **Statistics**\n\n"
        f"👥 Users: `{total_users}`\n"
        f"📁 Files: `{total_files}`\n"
        f"🟢 Active: `{running_count}`\n"
        f"📌 Yours: `{user_running}`"
    )
    if user_id in admin_ids:
        pending = db.get_pending_count()
        stats += f"\n📋 Pending: `{pending}`"

    bot.send_message(call.message.chat.id, stats, parse_mode='Markdown')


def _cb_back_to_main(call, bot, admin_ids, subscriptions, bot_locked_ref):
    user_id = call.from_user.id
    status, expiry_info = get_user_status(user_id, OWNER_ID, admin_ids, subscriptions)
    limits = {'free': 20, 'subscribed': 15, 'admin': 999}
    file_limit = get_file_limit(user_id, OWNER_ID, admin_ids, subscriptions, limits)
    current = db.get_user_file_count(user_id)
    pending = db.get_pending_count() if user_id in admin_ids else 0

    text = (
        f"👋 Welcome back, **{call.from_user.first_name}**!\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"🏷 Status: {status}{expiry_info}\n"
        f"📂 Files: {current} / {format_limit(file_limit)}"
    )
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_inline(user_id, admin_ids, bot_locked_ref[0], pending),
                              parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            logger.error(f"Back to main error: {e}")


def _cb_file_control(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        requester = call.from_user.id
        if not (requester == owner_id or requester in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        files = db.get_user_files(owner_id)
        if not any(f[0] == file_name for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        running = is_running(owner_id, file_name)
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        ar = db.get_auto_restart(owner_id, file_name)
        try:
            bot.edit_message_text(
                f"📄 **{file_name}** ({ftype})\n👤 User: `{owner_id}`\n"
                f"Status: {'🟢 Running' if running else '🔴 Stopped'}",
                call.message.chat.id, call.message.message_id,
                reply_markup=file_control_buttons(owner_id, file_name, running, ar['enabled']),
                parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid data.", show_alert=True)


def _cb_start_script(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        requester = call.from_user.id
        if not (requester == owner_id or requester in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        files = db.get_user_files(owner_id)
        file_info = next((f for f in files if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        if is_running(owner_id, file_name):
            bot.answer_callback_query(call.id, "Already running.", show_alert=True)
            return
        user_folder = get_user_folder(owner_id)
        file_path = os.path.join(user_folder, file_name)
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, "File missing! Re-upload.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Starting {file_name}...")
        ftype = file_info[1]
        if ftype == 'py':
            threading.Thread(target=run_python_script,
                             args=(file_path, owner_id, user_folder, file_name, bot, call.message.chat.id)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script,
                             args=(file_path, owner_id, user_folder, file_name, bot, call.message.chat.id)).start()
        time.sleep(1.5)
        running = is_running(owner_id, file_name)
        ar = db.get_auto_restart(owner_id, file_name)
        try:
            bot.edit_message_text(
                f"📄 **{file_name}** ({ftype})\n👤 User: `{owner_id}`\n"
                f"Status: {'🟢 Running' if running else '⏳ Starting...'}",
                call.message.chat.id, call.message.message_id,
                reply_markup=file_control_buttons(owner_id, file_name, running, ar['enabled']),
                parse_mode='Markdown'
            )
        except Exception:
            pass
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_stop_script(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Stopping {file_name}...")
        stop_script(owner_id, file_name)
        files = db.get_user_files(owner_id)
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        ar = db.get_auto_restart(owner_id, file_name)
        try:
            bot.edit_message_text(
                f"📄 **{file_name}** ({ftype})\n👤 User: `{owner_id}`\nStatus: 🔴 Stopped",
                call.message.chat.id, call.message.message_id,
                reply_markup=file_control_buttons(owner_id, file_name, False, ar['enabled']),
                parse_mode='Markdown'
            )
        except Exception:
            pass
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_restart_script(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        user_folder = get_user_folder(owner_id)
        file_path = os.path.join(user_folder, file_name)
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, "File missing!", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Restarting {file_name}...")
        stop_script(owner_id, file_name)
        time.sleep(1)
        files = db.get_user_files(owner_id)
        ftype = next((f[1] for f in files if f[0] == file_name), 'py')
        if ftype == 'py':
            threading.Thread(target=run_python_script,
                             args=(file_path, owner_id, user_folder, file_name, bot, call.message.chat.id)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script,
                             args=(file_path, owner_id, user_folder, file_name, bot, call.message.chat.id)).start()
        time.sleep(1.5)
        running = is_running(owner_id, file_name)
        ar = db.get_auto_restart(owner_id, file_name)
        try:
            bot.edit_message_text(
                f"📄 **{file_name}** ({ftype})\n👤 User: `{owner_id}`\n"
                f"Status: {'🟢 Running' if running else '⏳ Starting...'}",
                call.message.chat.id, call.message.message_id,
                reply_markup=file_control_buttons(owner_id, file_name, running, ar['enabled']),
                parse_mode='Markdown'
            )
        except Exception:
            pass
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_delete_script(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Deleting {file_name}...")
        stop_script(owner_id, file_name)
        deleted = delete_user_file(owner_id, file_name)
        deleted_str = ", ".join(f"`{f}`" for f in deleted) if deleted else "files"
        try:
            bot.edit_message_text(
                f"🗑️ Deleted `{file_name}` and {deleted_str} for user `{owner_id}`.",
                call.message.chat.id, call.message.message_id, parse_mode='Markdown'
            )
        except Exception:
            bot.send_message(call.message.chat.id, f"🗑️ Deleted `{file_name}`.", parse_mode='Markdown')
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_logs(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        user_folder = get_user_folder(owner_id)
        from services.script_runner import get_log_content
        content = get_log_content(owner_id, file_name, user_folder)
        if not content:
            bot.send_message(call.message.chat.id, f"📜 No logs for `{file_name}`.", parse_mode='Markdown')
            return
        from utils.keyboards import log_stream_keyboard
        bot.send_message(call.message.chat.id,
                         f"📜 **Logs for `{file_name}`** (User `{owner_id}`):\n```\n{content}\n```",
                         reply_markup=log_stream_keyboard(owner_id, file_name, is_streaming=False),
                         parse_mode='Markdown')
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)
        
        
active_log_streams = {}

def _cb_streamlog(call, bot, admin_ids):
    try:
        parts = call.data.split('_', 2)
        owner_id = int(parts[1])
        file_name = parts[2]
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
            
        stream_id = f"{call.message.chat.id}_{call.message.message_id}"
        if stream_id in active_log_streams:
            bot.answer_callback_query(call.id, "Already streaming.", show_alert=True)
            return
            
        bot.answer_callback_query(call.id, "Streaming logs for 30s...")
        stop_event = threading.Event()
        active_log_streams[stream_id] = stop_event
        
        from utils.keyboards import log_stream_keyboard
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, 
                                      reply_markup=log_stream_keyboard(owner_id, file_name, is_streaming=True))
        
        threading.Thread(target=_stream_logs_thread, 
                         args=(bot, call.message.chat.id, call.message.message_id, owner_id, file_name, stop_event, stream_id)).start()
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)

def _stream_logs_thread(bot, chat_id, msg_id, owner_id, file_name, stop_event, stream_id):
    from services.script_runner import get_log_content
    from services.file_manager import get_user_folder
    user_folder = get_user_folder(owner_id)
    last_content = ""
    start_time = time.time()
    
    try:
        while not stop_event.is_set() and (time.time() - start_time) < 30:
            content = get_log_content(owner_id, file_name, user_folder, max_kb=5)
            if content and content != last_content:
                last_content = content
                from utils.keyboards import log_stream_keyboard
                try:
                    bot.edit_message_text(f"🔴 **Live Logs: `{file_name}`** (User `{owner_id}`)\n```\n{content}\n```", 
                                          chat_id, msg_id, 
                                          reply_markup=log_stream_keyboard(owner_id, file_name, is_streaming=True),
                                          parse_mode='Markdown')
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" not in str(e):
                        pass
            stop_event.wait(3.5)  # Telegram API limit safety
            
        if stream_id in active_log_streams:
            del active_log_streams[stream_id]
            from utils.keyboards import log_stream_keyboard
            try:
                bot.edit_message_reply_markup(chat_id, msg_id, 
                                              reply_markup=log_stream_keyboard(owner_id, file_name, is_streaming=False))
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Live log stream error: {e}")

def _cb_stoplog(call, bot, admin_ids):
    stream_id = f"{call.message.chat.id}_{call.message.message_id}"
    if stream_id in active_log_streams:
        active_log_streams[stream_id].set() 
        bot.answer_callback_query(call.id, "Stream stopped.")
    else:
        bot.answer_callback_query(call.id, "Stream already halted.")


def _cb_resources(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        usage = get_script_resource_usage(owner_id, file_name)
        if usage:
            bot.send_message(call.message.chat.id,
                             f"📊 **Resources for `{file_name}`**\n\n"
                             f"🔧 CPU: `{usage['cpu']}%`\n"
                             f"💾 Memory: `{usage['memory_mb']} MB`",
                             parse_mode='Markdown')
        else:
            bot.send_message(call.message.chat.id, f"ℹ️ No resource data for `{file_name}`. Is it running?",
                             parse_mode='Markdown')
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_toggle_auto_restart(call, bot, admin_ids):
    try:
        parts = call.data.split('_', 3)
        # toggle_ar_ownerid_filename
        owner_id = int(parts[2])
        file_name = parts[3]
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        ar = db.get_auto_restart(owner_id, file_name)
        new_state = not ar['enabled']
        db.set_auto_restart(owner_id, file_name, enabled=new_state)
        bot.answer_callback_query(call.id, f"Auto-restart {'ON' if new_state else 'OFF'}")
        running = is_running(owner_id, file_name)
        files = db.get_user_files(owner_id)
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        try:
            bot.edit_message_text(
                f"📄 **{file_name}** ({ftype})\n👤 User: `{owner_id}`\n"
                f"Status: {'🟢 Running' if running else '🔴 Stopped'}",
                call.message.chat.id, call.message.message_id,
                reply_markup=file_control_buttons(owner_id, file_name, running, new_state),
                parse_mode='Markdown'
            )
        except Exception:
            pass
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_versions(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        versions = db.get_file_versions(owner_id, file_name)
        if not versions:
            bot.send_message(call.message.chat.id, f"📦 No version history for `{file_name}`.", parse_mode='Markdown')
            return
        markup = version_list_keyboard(owner_id, file_name, versions)
        bot.send_message(call.message.chat.id,
                         f"📦 **Versions for `{file_name}`**\nTap to rollback.",
                         reply_markup=markup, parse_mode='Markdown')
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_rollback(call, bot, admin_ids):
    try:
        parts = call.data.split('_', 3)
        owner_id = int(parts[1])
        rest = parts[2] + '_' + parts[3] if len(parts) > 3 else parts[2]
        # Parse file_name and version from rest
        last_underscore = rest.rfind('_')
        file_name = rest[:last_underscore]
        version = int(rest[last_underscore + 1:])

        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Rolling back to v{version}...")
        # Stop if running
        if is_running(owner_id, file_name):
            stop_script(owner_id, file_name)
        success, msg = rollback_to_version(owner_id, file_name, version)
        bot.send_message(call.message.chat.id,
                         f"{'✅' if success else '❌'} {msg}", parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"Rollback parse error: {e}")
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


# ==================== ENVIRONMENT VARIABLES ====================

def _cb_envs(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        
        envs = db.get_script_env(owner_id, file_name)
        from utils.keyboards import env_vars_keyboard
        markup = env_vars_keyboard(owner_id, file_name, list(envs.keys()))
        
        text = f"🔐 **Environment Variables for `{file_name}`**\n\n"
        if not envs:
            text += "ℹ️ No custom variables set."
        else:
            for k, v in envs.items():
                text += f"• `{k}` = `{v}`\n"
                
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=markup, parse_mode='Markdown')
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)

def _cb_addenv(call, bot, admin_ids):
    try:
        _, owner_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_str)
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, 
                               f"➕ Let's add an environment variable for `{file_name}`.\n\n"
                               "Format your message exactly like this:\n`KEY=VALUE`\n\nOr send /cancel.", 
                               parse_mode='Markdown')
        bot.register_next_step_handler(msg, _process_addenv, bot, admin_ids, owner_id, file_name)
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)

def _process_addenv(message, bot, admin_ids, owner_id, file_name):
    if message.from_user.id not in admin_ids and message.from_user.id != owner_id:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
        
    text = message.text or ""
    if "=" not in text:
        bot.reply_to(message, "❌ Invalid format. Must be `KEY=VALUE`.", parse_mode='Markdown')
        return
        
    key, val = text.split("=", 1)
    key = key.strip()
    val = val.strip()
    
    if db.set_script_env(owner_id, file_name, key, val):
        bot.reply_to(message, f"✅ Added variable `{key}` to `{file_name}`.\nRestart the script to apply changes.", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Failed to save variable.")

def _cb_delenv(call, bot, admin_ids):
    try:
        parts = call.data.split('_', 3)
        owner_id = int(parts[1])
        file_name = parts[2]
        env_key = parts[3]
        
        if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
            bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
            return
            
        if db.delete_script_env(owner_id, file_name, env_key):
            bot.answer_callback_query(call.id, f"Deleted {env_key}")
            # Refresh view
            _cb_envs(call, bot, admin_ids)
        else:
            bot.answer_callback_query(call.id, "Failed to delete.", show_alert=True)
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_approve(call, bot, admin_ids):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return
    try:
        approval_id = int(call.data.split('_')[1])
        result = handle_approve(approval_id, call.from_user.id, bot)
        if result:
            bot.answer_callback_query(call.id, "✅ Approved!")
            try:
                bot.edit_message_text(
                    f"✅ **Approved** `{result['file_name']}` for user `{result['user_id']}`.\n"
                    f"Approved by: `{call.from_user.id}`",
                    call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                )
            except Exception:
                pass
        else:
            bot.answer_callback_query(call.id, "Failed or already reviewed.", show_alert=True)
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _cb_reject_init(call, bot, admin_ids):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return
    try:
        approval_id = int(call.data.split('_')[1])
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id,
                               f"📝 Enter rejection reason for approval #{approval_id}, or /cancel.")
        bot.register_next_step_handler(msg, _process_reject, bot, admin_ids, approval_id, call.message)
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid.", show_alert=True)


def _process_reject(message, bot, admin_ids, approval_id, original_msg):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Rejection cancelled.")
        return
    reason = message.text or "No reason"
    result = handle_reject(approval_id, message.from_user.id, reason, bot)
    if result:
        bot.reply_to(message, f"❌ Rejected `{result['file_name']}` for user `{result['user_id']}`.",
                      parse_mode='Markdown')
        try:
            bot.edit_message_text(
                f"❌ **Rejected** `{result['file_name']}` for user `{result['user_id']}`.\n"
                f"Reason: {reason}",
                original_msg.chat.id, original_msg.message_id, parse_mode='Markdown'
            )
        except Exception:
            pass
    else:
        bot.reply_to(message, "Failed or already reviewed.")


# ==================== STEP HANDLERS ====================

def _process_ban_from_callback(message, bot, admin_ids):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        parts = message.text.split(maxsplit=1)
        target_id = int(parts[0])
        reason = parts[1] if len(parts) > 1 else "No reason"
        if target_id == OWNER_ID:
            bot.reply_to(message, "❌ Cannot ban owner.")
            return
        from services.script_runner import stop_all_user_scripts
        db.ban_user(target_id, message.from_user.id, reason)
        stopped = stop_all_user_scripts(target_id)
        bot.reply_to(message, f"🚫 Banned `{target_id}`. Stopped {stopped} scripts.", parse_mode='Markdown')
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID.")


def _process_unban_from_callback(message, bot):
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if db.unban_user(uid):
            bot.reply_to(message, f"✅ Unbanned `{uid}`.", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"⚠️ User `{uid}` was not banned.", parse_mode='Markdown')
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID.")


def _process_add_admin(message, bot, admin_ids):
    if message.from_user.id != OWNER_ID:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        new_id = int(message.text.strip())
        if new_id == OWNER_ID:
            bot.reply_to(message, "Owner is already owner.")
            return
        if new_id in admin_ids:
            bot.reply_to(message, f"`{new_id}` already admin.", parse_mode='Markdown')
            return
        db.add_admin(new_id)
        admin_ids.add(new_id)
        bot.reply_to(message, f"✅ `{new_id}` promoted to Admin.", parse_mode='Markdown')
        try:
            bot.send_message(new_id, "🎉 You are now an **Admin**!", parse_mode='Markdown')
        except Exception:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID.")


def _process_remove_admin(message, bot, admin_ids):
    if message.from_user.id != OWNER_ID:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        target_id = int(message.text.strip())
        if target_id == OWNER_ID:
            bot.reply_to(message, "Cannot remove owner.")
            return
        if target_id not in admin_ids:
            bot.reply_to(message, f"`{target_id}` is not an admin.", parse_mode='Markdown')
            return
        if db.remove_admin(target_id, OWNER_ID):
            admin_ids.discard(target_id)
            bot.reply_to(message, f"✅ Removed admin `{target_id}`.", parse_mode='Markdown')
            try:
                bot.send_message(target_id, "ℹ️ You are no longer an Admin.")
            except Exception:
                pass
        else:
            bot.reply_to(message, "Failed to remove.")
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID.")


def _process_maintenance_msg(message, bot):
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    db.set_maintenance(db.get_maintenance_status()['enabled'], message=message.text)
    bot.reply_to(message, f"✅ Maintenance message updated to:\n_{message.text}_", parse_mode='Markdown')
