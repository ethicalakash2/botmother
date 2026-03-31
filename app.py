"""
Telegram Bot Hosting Platform — Main Entry Point
=================================================
A production-grade Telegram bot that hosts and runs Python/JS scripts.
Features: Admin file approval, auto-restart, resource monitoring, ban system,
maintenance mode, file versioning, broadcast, subscriptions.
"""
import os
import sys
import time
import logging
import atexit

import telebot
from flask import Flask
from threading import Thread

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger('HostingBot')

# --- Load Configuration ---
from config import BOT_TOKEN, OWNER_ID, ADMIN_ID, FLASK_PORT

# --- Initialize Bot ---
bot = telebot.TeleBot(BOT_TOKEN)

# --- Initialize Database ---
import database as db
db.init_db(OWNER_ID, ADMIN_ID)

# --- Load Runtime State ---
admin_ids = db.get_all_admins()
admin_ids.add(OWNER_ID)
if ADMIN_ID:
    admin_ids.add(ADMIN_ID)

# Load subscriptions into memory cache
subscriptions = db.get_all_subscriptions()

# Bot locked state — using list for mutability in closures
bot_locked = [False]

# --- Flask Keep-Alive ---
flask_app = Flask('HostingBot')

@flask_app.route('/')
def home():
    return "Bot is running"

@flask_app.route('/health')
def health():
    from services.script_runner import get_running_count
    return {
        'status': 'ok',
        'running_scripts': get_running_count(),
        'users': db.get_total_user_count(),
    }

def run_flask():
    port = int(os.environ.get("PORT", FLASK_PORT))
    flask_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    logger.info(f"Flask Keep-Alive server started on port {FLASK_PORT}.")


# --- Register All Handlers ---
from handlers import start, files, admin, subscription, callbacks

start.register(bot, admin_ids, subscriptions, bot_locked)
files.register(bot, admin_ids, subscriptions, bot_locked)
admin.register(bot, admin_ids, subscriptions, bot_locked)
subscription.register(bot, admin_ids, subscriptions, bot_locked)
callbacks.register(bot, admin_ids, subscriptions, bot_locked)


# --- Text Button Handler (reply keyboard) ---
# Map button text to handler functions
BUTTON_TEXT_MAP = {
    "📢 Updates Channel": lambda m: bot.reply_to(m, f"📢 Visit our Updates Channel:", reply_markup=_updates_markup()),
    "📤 Upload File": lambda m: _dispatch_cmd(m, 'uploadfile'),
    "📂 My Files": lambda m: _dispatch_cmd(m, 'checkfiles'),
    "⚡ Bot Speed": lambda m: _dispatch_cmd(m, 'ping'),
    "📊 Statistics": lambda m: _dispatch_cmd(m, 'status'),
    "⏱ Uptime": lambda m: _dispatch_cmd(m, 'uptime'),
    "💳 Subscriptions": lambda m: _dispatch_cmd(m, 'subscriptions'),
    "📢 Broadcast": lambda m: _dispatch_cmd(m, 'broadcast'),
    "🔒 Lock Bot": lambda m: _dispatch_cmd(m, 'lockbot'),
    "🟢 Run All Scripts": lambda m: _dispatch_cmd(m, 'runall'),
    "👑 Admin Panel": lambda m: _dispatch_cmd(m, 'adminpanel'),
    "📋 Pending Files": lambda m: _dispatch_cmd(m, 'pending'),
    "📈 Dashboard": lambda m: _dispatch_cmd(m, 'dashboard'),
    "🚫 Ban Manager": lambda m: bot.reply_to(m, "🚫 **Ban Manager**\nUse `/ban`, `/unban`, `/banlist` commands.", parse_mode='Markdown'),
    "📞 Contact Owner": lambda m: bot.reply_to(m, "📞 Contact the owner:",
        reply_markup=telebot.types.InlineKeyboardMarkup().add(
            telebot.types.InlineKeyboardButton('📞 Contact', url=f'https://t.me/{_get_username()}'))),
}

def _updates_markup():
    from config import UPDATE_CHANNEL
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    return markup

def _get_username():
    from config import YOUR_USERNAME
    return YOUR_USERNAME.replace('@', '')

def _dispatch_cmd(message, cmd):
    """Simulate a command dispatch."""
    message.text = f'/{cmd}'
    bot.process_new_messages([message])


@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_MAP)
def handle_reply_buttons(message):
    handler = BUTTON_TEXT_MAP.get(message.text)
    if handler:
        handler(message)


# --- Cleanup on Exit ---
def cleanup():
    from services.script_runner import cleanup_all
    cleanup_all()

atexit.register(cleanup)


# --- Start Monitor ---
from services.monitor import start_monitor
start_monitor(bot, admin_ids)


# --- Main ---
if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("  HOSTING BOT — STARTING UP")
    logger.info(f"  Python: {sys.version.split()[0]}")
    logger.info(f"  Owner: {OWNER_ID}")
    logger.info(f"  Admins: {admin_ids}")
    logger.info(f"  Users: {db.get_total_user_count()}")
    logger.info(f"  Files: {db.get_total_file_count()}")
    logger.info("=" * 50)

    keep_alive()
    logger.info("Starting Telegram polling...")

    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except Exception as e:
            error_name = type(e).__name__
            if 'ReadTimeout' in error_name or 'ConnectionError' in error_name:
                logger.warning(f"Polling {error_name}. Retrying in 5s...")
                time.sleep(5)
            else:
                logger.critical(f"Polling error: {e}", exc_info=True)
                time.sleep(15)
        finally:
            logger.warning("Polling loop restarting...")
            time.sleep(1)