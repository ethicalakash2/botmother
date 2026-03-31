"""
Configuration module — loads all settings from .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram Bot ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
YOUR_USERNAME = os.getenv('YOUR_USERNAME', '@unknown')
UPDATE_CHANNEL = os.getenv('UPDATE_CHANNEL', '')

# --- Directories ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
PENDING_DIR = os.path.join(BASE_DIR, 'pending_files')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATABASE_PATH = os.path.join(DATA_DIR, 'bot_data.db')
VERSIONS_DIR = os.path.join(BASE_DIR, 'file_versions')

# --- User Limits ---
FREE_USER_LIMIT = int(os.getenv('FREE_USER_LIMIT', '20'))
SUBSCRIBED_USER_LIMIT = int(os.getenv('SUBSCRIBED_USER_LIMIT', '15'))
ADMIN_LIMIT = int(os.getenv('ADMIN_LIMIT', '999'))
OWNER_LIMIT = float('inf')

# --- Flask ---
FLASK_PORT = int(os.getenv('FLASK_PORT', '8080'))

# --- Monitoring ---
WATCHDOG_INTERVAL = int(os.getenv('WATCHDOG_INTERVAL', '30'))
MAX_RESTART_ATTEMPTS = int(os.getenv('MAX_RESTART_ATTEMPTS', '5'))
MEMORY_ALERT_THRESHOLD = int(os.getenv('MEMORY_ALERT_THRESHOLD', '80'))

# --- File Constraints ---
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS = ['.py', '.js', '.zip']
MAX_VERSIONS_KEPT = 3

# --- Create directories ---
for d in [UPLOAD_BOTS_DIR, PENDING_DIR, DATA_DIR, VERSIONS_DIR]:
    os.makedirs(d, exist_ok=True)
