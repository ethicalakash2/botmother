"""
File handlers — upload, check files, document handler.
All uploads now go through admin approval workflow.
"""
import os
import logging
import telebot

import database as db
from config import OWNER_ID, MAX_FILE_SIZE, ALLOWED_EXTENSIONS
from utils.helpers import get_file_limit, format_limit
from services.approval import submit_for_approval
from services.file_manager import get_user_folder, handle_zip_upload, save_file_to_pending, handle_git_clone
from services.script_runner import is_running

logger = logging.getLogger(__name__)


def register(bot, admin_ids, subscriptions, bot_locked_ref):
    """Register file-related handlers."""

    def _check_access(message):
        """Check if user can access the bot."""
        user_id = message.from_user.id
        if db.is_banned(user_id):
            bot.reply_to(message, "🚫 You are banned.")
            return False
        maint = db.get_maintenance_status()
        if maint['enabled'] and user_id not in admin_ids:
            bot.reply_to(message, f"🛠 {maint['message']}")
            return False
        if bot_locked_ref[0] and user_id not in admin_ids:
            bot.reply_to(message, "🔒 Bot is locked.")
            return False
        return True

    @bot.message_handler(commands=['uploadfile'])
    def cmd_upload(message):
        if not _check_access(message):
            return
        user_id = message.from_user.id
        limits = {'free': 20, 'subscribed': 15, 'admin': 999}
        file_limit = get_file_limit(user_id, OWNER_ID, admin_ids, subscriptions, limits)
        current = db.get_user_file_count(user_id)
        if current >= file_limit:
            bot.reply_to(message, f"📁 File limit reached ({current}/{format_limit(file_limit)}). Delete files first.")
            return
        bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\n⚠️ Files require admin approval before they can run.")

    @bot.message_handler(commands=['checkfiles'])
    def cmd_check_files(message):
        if not _check_access(message):
            return
        _show_user_files(message.from_user.id, message.chat.id, bot, reply_to=message)

    @bot.message_handler(commands=['clone'])
    def cmd_clone(message):
        if not _check_access(message):
            return
            
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Usage: `/clone <github_repo_url>`", parse_mode='Markdown')
            return
            
        git_url = args[1]
        if not git_url.startswith("http"):
            bot.reply_to(message, "❌ Invalid URL format.")
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # Check file limit
        limits = {'free': 20, 'subscribed': 15, 'admin': 999}
        file_limit = get_file_limit(user_id, OWNER_ID, admin_ids, subscriptions, limits)
        current = db.get_user_file_count(user_id)
        if current >= file_limit:
            bot.reply_to(message, f"📁 File limit reached ({current}/{format_limit(file_limit)}).")
            return

        result = handle_git_clone(git_url, user_id, bot, chat_id)
        if result:
            main_script, file_type = result
            approval_id = submit_for_approval(user_id, main_script, file_type, None, bot, admin_ids)
            if approval_id:
                bot.send_message(chat_id,
                                 f"📋 Repository cloned! Main script: `{main_script}`\n"
                                 f"⏳ Submitted for admin approval (ID: #{approval_id}).\n"
                                 f"You'll be notified when approved.",
                                 parse_mode='Markdown')
            else:
                bot.send_message(chat_id, "❌ Error submitting repo for approval.")

    @bot.message_handler(content_types=['document'])
    def handle_document(message):
        if not _check_access(message):
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
        doc = message.document

        logger.info(f"Doc from {user_id}: {doc.file_name} ({doc.mime_type}), Size: {doc.file_size}")

        # Check file limit
        limits = {'free': 20, 'subscribed': 15, 'admin': 999}
        file_limit = get_file_limit(user_id, OWNER_ID, admin_ids, subscriptions, limits)
        current = db.get_user_file_count(user_id)
        if current >= file_limit:
            bot.reply_to(message, f"📁 File limit reached ({current}/{format_limit(file_limit)}).")
            return

        file_name = doc.file_name
        if not file_name:
            bot.reply_to(message, "❌ File has no name.")
            return

        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            bot.reply_to(message, f"❌ Only `.py`, `.js`, `.zip` files allowed.")
            return

        if doc.file_size > MAX_FILE_SIZE:
            bot.reply_to(message, f"❌ File too large (Max: {MAX_FILE_SIZE // 1024 // 1024} MB).")
            return

        try:
            # Forward to owner for record
            try:
                bot.forward_message(OWNER_ID, chat_id, message.message_id)
                bot.send_message(OWNER_ID,
                                 f"📥 File `{file_name}` from {message.from_user.first_name} (`{user_id}`)",
                                 parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to forward to owner: {e}")

            # Download file
            wait_msg = bot.reply_to(message, f"⬇️ Downloading `{file_name}`...", parse_mode='Markdown')
            file_info = bot.get_file(doc.file_id)
            file_content = bot.download_file(file_info.file_path)
            
            if file_content is None:
                bot.edit_message_text(f"❌ Error: Failed to download `{file_name}`. Telegram API returned empty data.",
                                      chat_id, wait_msg.message_id, parse_mode='Markdown')
                return

            bot.edit_message_text(f"✅ Downloaded `{file_name}`. Processing...",
                                  chat_id, wait_msg.message_id, parse_mode='Markdown')

            if file_ext == '.zip':
                # Handle ZIP — extract and submit main script for approval
                result = handle_zip_upload(file_content, file_name, user_id, bot, chat_id)
                if result:
                    main_script, file_type = result
                    approval_id = submit_for_approval(user_id, main_script, file_type, None, bot, admin_ids)
                    if approval_id:
                        bot.send_message(chat_id,
                                         f"📋 ZIP extracted! Main script: `{main_script}`\n"
                                         f"⏳ Submitted for admin approval (ID: #{approval_id}).\n"
                                         f"You'll be notified when approved.",
                                         parse_mode='Markdown')
                    else:
                        bot.send_message(chat_id, "❌ Error submitting for approval.")
            else:
                # Single file — save to pending and submit for approval
                file_type = 'py' if file_ext == '.py' else 'js'
                save_file_to_pending(user_id, file_name, file_content)
                approval_id = submit_for_approval(user_id, file_name, file_type, None, bot, admin_ids)
                if approval_id:
                    bot.send_message(chat_id,
                                     f"📋 File `{file_name}` received!\n"
                                     f"⏳ Submitted for admin approval (ID: #{approval_id}).\n"
                                     f"You'll be notified when approved.",
                                     parse_mode='Markdown')
                else:
                    bot.send_message(chat_id, "❌ Error submitting for approval.")

        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Telegram API error handling file for {user_id}: {e}")
            if "file is too big" in str(e).lower():
                bot.reply_to(message, "❌ File too large for Telegram API (~20MB limit).")
            else:
                bot.reply_to(message, f"❌ Telegram API Error: {str(e)}")
        except Exception as e:
            logger.error(f"Error handling file for {user_id}: {e}", exc_info=True)
            bot.reply_to(message, f"❌ Error: {str(e)}")


def _show_user_files(user_id, chat_id, bot, reply_to=None, message_id=None):
    """Show user's files with status indicators."""
    from telebot import types

    files = db.get_user_files(user_id)
    if not files:
        text = "📂 **Your Files**\n\n_(No files uploaded yet)_"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload File", callback_data='upload'))
        if reply_to:
            bot.reply_to(reply_to, text, reply_markup=markup, parse_mode='Markdown')
        elif message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            except Exception:
                bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(files):
        running = is_running(user_id, file_name)
        status = "🟢 Running" if running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) — {status}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))

    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))

    text = "📂 **Your Files**\nTap a file to manage it."
    if reply_to:
        bot.reply_to(reply_to, text, reply_markup=markup, parse_mode='Markdown')
    elif message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logger.error(f"Error editing file list: {e}")
