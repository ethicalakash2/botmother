"""
Database module — thread-safe SQLite operations for all bot data.
Handles: users, files, subscriptions, admins, approvals, bans, versions, resource logs.
"""
import sqlite3
import threading
import logging
from datetime import datetime
from config import DATABASE_PATH

logger = logging.getLogger(__name__)
DB_LOCK = threading.Lock()


def get_connection():
    """Create a new SQLite connection."""
    return sqlite3.connect(DATABASE_PATH, check_same_thread=False)


def init_db(owner_id, admin_id):
    """Initialize all database tables."""
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = get_connection()
        c = conn.cursor()

        # --- Existing tables ---
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                      PRIMARY KEY (user_id, file_name))''')

        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY, joined_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')

        # --- New tables ---
        c.execute('''CREATE TABLE IF NOT EXISTS pending_approvals
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      file_name TEXT,
                      file_type TEXT,
                      submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                      status TEXT DEFAULT 'pending',
                      reviewed_by INTEGER,
                      reviewed_at TEXT,
                      reject_reason TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS banned_users
                     (user_id INTEGER PRIMARY KEY,
                      banned_by INTEGER,
                      reason TEXT,
                      banned_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS file_versions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      file_name TEXT,
                      version INTEGER,
                      backup_path TEXT,
                      created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS auto_restart_config
                     (user_id INTEGER,
                      file_name TEXT,
                      enabled INTEGER DEFAULT 1,
                      max_retries INTEGER DEFAULT 5,
                      restart_count INTEGER DEFAULT 0,
                      last_restart TEXT,
                      PRIMARY KEY (user_id, file_name))''')

        c.execute('''CREATE TABLE IF NOT EXISTS resource_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      file_name TEXT,
                      cpu_percent REAL,
                      memory_mb REAL,
                      logged_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS maintenance_mode
                     (id INTEGER PRIMARY KEY DEFAULT 1,
                      enabled INTEGER DEFAULT 0,
                      message TEXT DEFAULT "Bot is under maintenance. Please try again later.",
                      ends_at TEXT)''')
                      
        c.execute('''CREATE TABLE IF NOT EXISTS script_env_vars
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      file_name TEXT,
                      env_key TEXT,
                      env_value TEXT,
                      UNIQUE(user_id, file_name, env_key))''')

        # Insert defaults
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (owner_id,))
        if admin_id != owner_id:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
        c.execute('INSERT OR IGNORE INTO maintenance_mode (id, enabled) VALUES (1, 0)')

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully with all tables.")
    except Exception as e:
        logger.error(f"Database initialization error: {e}", exc_info=True)


# ==================== ACTIVE USERS ====================

def add_active_user(user_id):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO active_users (user_id, joined_at) VALUES (?, ?)',
                      (user_id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error adding active user {user_id}: {e}")


def get_all_active_users():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM active_users')
        users = {row[0] for row in c.fetchall()}
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Error getting active users: {e}")
        return set()


def get_total_user_count():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM active_users')
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error counting users: {e}")
        return 0


# ==================== USER FILES ====================

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, uploaded_at) VALUES (?, ?, ?, ?)',
                      (user_id, file_name, file_type, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            logger.info(f"Saved file '{file_name}' ({file_type}) for user {user_id}")
        except Exception as e:
            logger.error(f"Error saving file for {user_id}: {e}")


def remove_user_file(user_id, file_name):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            conn.close()
            logger.info(f"Removed file '{file_name}' for user {user_id}")
        except Exception as e:
            logger.error(f"Error removing file for {user_id}: {e}")


def get_user_files(user_id):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT file_name, file_type FROM user_files WHERE user_id = ?', (user_id,))
        files = c.fetchall()
        conn.close()
        return files
    except Exception as e:
        logger.error(f"Error getting files for {user_id}: {e}")
        return []


def get_user_file_count(user_id):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM user_files WHERE user_id = ?', (user_id,))
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error counting files for {user_id}: {e}")
        return 0


def get_all_user_files():
    """Returns dict of {user_id: [(file_name, file_type), ...]}."""
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        result = {}
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in result:
                result[user_id] = []
            result[user_id].append((file_name, file_type))
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error getting all user files: {e}")
        return {}


def get_total_file_count():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM user_files')
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error counting total files: {e}")
        return 0


# ==================== SUBSCRIPTIONS ====================

def save_subscription(user_id, expiry):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (user_id, expiry.isoformat()))
            conn.commit()
            conn.close()
            logger.info(f"Saved subscription for {user_id}, expiry {expiry}")
        except Exception as e:
            logger.error(f"Error saving subscription for {user_id}: {e}")


def remove_subscription(user_id):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            logger.info(f"Removed subscription for {user_id}")
        except Exception as e:
            logger.error(f"Error removing subscription for {user_id}: {e}")


def get_subscription(user_id):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT expiry FROM subscriptions WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row[0])
        return None
    except Exception as e:
        logger.error(f"Error getting subscription for {user_id}: {e}")
        return None


def get_all_subscriptions():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        subs = {}
        for user_id, expiry in c.fetchall():
            try:
                subs[user_id] = datetime.fromisoformat(expiry)
            except ValueError:
                pass
        conn.close()
        return subs
    except Exception as e:
        logger.error(f"Error getting all subscriptions: {e}")
        return {}


# ==================== ADMINS ====================

def add_admin(admin_id):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            conn.close()
            logger.info(f"Added admin {admin_id}")
        except Exception as e:
            logger.error(f"Error adding admin {admin_id}: {e}")


def remove_admin(admin_id, owner_id):
    if admin_id == owner_id:
        return False
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            removed = c.rowcount > 0
            conn.commit()
            conn.close()
            if removed:
                logger.info(f"Removed admin {admin_id}")
            return removed
        except Exception as e:
            logger.error(f"Error removing admin {admin_id}: {e}")
            return False


def get_all_admins():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM admins')
        admins = {row[0] for row in c.fetchall()}
        conn.close()
        return admins
    except Exception as e:
        logger.error(f"Error getting admins: {e}")
        return set()


# ==================== PENDING APPROVALS ====================

def add_pending_approval(user_id, file_name, file_type):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''INSERT INTO pending_approvals (user_id, file_name, file_type, submitted_at, status)
                         VALUES (?, ?, ?, ?, 'pending')''',
                      (user_id, file_name, file_type, datetime.now().isoformat()))
            approval_id = c.lastrowid
            conn.commit()
            conn.close()
            logger.info(f"Added pending approval #{approval_id} for {file_name} by user {user_id}")
            return approval_id
        except Exception as e:
            logger.error(f"Error adding pending approval: {e}")
            return None


def approve_file(approval_id, admin_id):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''UPDATE pending_approvals SET status='approved', reviewed_by=?, reviewed_at=?
                         WHERE id=? AND status='pending' ''',
                      (admin_id, datetime.now().isoformat(), approval_id))
            updated = c.rowcount > 0
            conn.commit()
            if updated:
                c.execute('SELECT user_id, file_name, file_type FROM pending_approvals WHERE id=?', (approval_id,))
                row = c.fetchone()
                conn.close()
                if row:
                    return {'user_id': row[0], 'file_name': row[1], 'file_type': row[2]}
            conn.close()
            return None
        except Exception as e:
            logger.error(f"Error approving file #{approval_id}: {e}")
            return None


def reject_file(approval_id, admin_id, reason="No reason provided"):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''UPDATE pending_approvals SET status='rejected', reviewed_by=?, reviewed_at=?, reject_reason=?
                         WHERE id=? AND status='pending' ''',
                      (admin_id, datetime.now().isoformat(), reason, approval_id))
            updated = c.rowcount > 0
            conn.commit()
            if updated:
                c.execute('SELECT user_id, file_name, file_type FROM pending_approvals WHERE id=?', (approval_id,))
                row = c.fetchone()
                conn.close()
                if row:
                    return {'user_id': row[0], 'file_name': row[1], 'file_type': row[2]}
            conn.close()
            return None
        except Exception as e:
            logger.error(f"Error rejecting file #{approval_id}: {e}")
            return None


def get_pending_approvals():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('''SELECT id, user_id, file_name, file_type, submitted_at
                     FROM pending_approvals WHERE status='pending' ORDER BY submitted_at ASC''')
        rows = c.fetchall()
        conn.close()
        return [{'id': r[0], 'user_id': r[1], 'file_name': r[2], 'file_type': r[3], 'submitted_at': r[4]} for r in rows]
    except Exception as e:
        logger.error(f"Error getting pending approvals: {e}")
        return []


def get_pending_count():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM pending_approvals WHERE status='pending'")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error counting pending: {e}")
        return 0


# ==================== BANNED USERS ====================

def ban_user(user_id, banned_by, reason="No reason"):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO banned_users (user_id, banned_by, reason, banned_at) VALUES (?, ?, ?, ?)',
                      (user_id, banned_by, reason, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            logger.warning(f"User {user_id} banned by {banned_by}. Reason: {reason}")
            return True
        except Exception as e:
            logger.error(f"Error banning user {user_id}: {e}")
            return False


def unban_user(user_id):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
            removed = c.rowcount > 0
            conn.commit()
            conn.close()
            if removed:
                logger.info(f"User {user_id} unbanned")
            return removed
        except Exception as e:
            logger.error(f"Error unbanning user {user_id}: {e}")
            return False


def is_banned(user_id):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT 1 FROM banned_users WHERE user_id = ?', (user_id,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error checking ban for {user_id}: {e}")
        return False


def get_banned_users():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id, reason, banned_at FROM banned_users ORDER BY banned_at DESC')
        rows = c.fetchall()
        conn.close()
        return [{'user_id': r[0], 'reason': r[1], 'banned_at': r[2]} for r in rows]
    except Exception as e:
        logger.error(f"Error getting banned users: {e}")
        return []


# ==================== FILE VERSIONS ====================

def save_file_version(user_id, file_name, version, backup_path):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''INSERT INTO file_versions (user_id, file_name, version, backup_path, created_at)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, file_name, version, backup_path, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            logger.info(f"Saved version {version} of '{file_name}' for user {user_id}")
        except Exception as e:
            logger.error(f"Error saving file version: {e}")


def get_file_versions(user_id, file_name):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('''SELECT id, version, backup_path, created_at FROM file_versions
                     WHERE user_id=? AND file_name=? ORDER BY version DESC''',
                  (user_id, file_name))
        rows = c.fetchall()
        conn.close()
        return [{'id': r[0], 'version': r[1], 'backup_path': r[2], 'created_at': r[3]} for r in rows]
    except Exception as e:
        logger.error(f"Error getting file versions: {e}")
        return []


def get_latest_version_number(user_id, file_name):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT MAX(version) FROM file_versions WHERE user_id=? AND file_name=?',
                  (user_id, file_name))
        row = c.fetchone()
        conn.close()
        return row[0] if row and row[0] else 0
    except Exception as e:
        logger.error(f"Error getting latest version: {e}")
        return 0


def delete_old_versions(user_id, file_name, keep=3):
    """Delete versions older than the latest `keep` versions."""
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''SELECT id, backup_path FROM file_versions
                         WHERE user_id=? AND file_name=?
                         ORDER BY version DESC''',
                      (user_id, file_name))
            rows = c.fetchall()
            if len(rows) > keep:
                old_rows = rows[keep:]
                for row in old_rows:
                    c.execute('DELETE FROM file_versions WHERE id=?', (row[0],))
                    # Return paths so caller can delete files
                conn.commit()
                conn.close()
                return [r[1] for r in old_rows]
            conn.close()
            return []
        except Exception as e:
            logger.error(f"Error deleting old versions: {e}")
            return []


# ==================== AUTO-RESTART CONFIG ====================

def set_auto_restart(user_id, file_name, enabled=True, max_retries=5):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO auto_restart_config
                         (user_id, file_name, enabled, max_retries, restart_count, last_restart)
                         VALUES (?, ?, ?, ?, 0, NULL)''',
                      (user_id, file_name, 1 if enabled else 0, max_retries))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting auto-restart: {e}")


def get_auto_restart(user_id, file_name):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT enabled, max_retries, restart_count FROM auto_restart_config WHERE user_id=? AND file_name=?',
                  (user_id, file_name))
        row = c.fetchone()
        conn.close()
        if row:
            return {'enabled': bool(row[0]), 'max_retries': row[1], 'restart_count': row[2]}
        return {'enabled': True, 'max_retries': 5, 'restart_count': 0}
    except Exception as e:
        logger.error(f"Error getting auto-restart config: {e}")
        return {'enabled': True, 'max_retries': 5, 'restart_count': 0}


def increment_restart_count(user_id, file_name):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''UPDATE auto_restart_config SET restart_count = restart_count + 1, last_restart = ?
                         WHERE user_id=? AND file_name=?''',
                      (datetime.now().isoformat(), user_id, file_name))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error incrementing restart count: {e}")


def reset_restart_count(user_id, file_name):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('UPDATE auto_restart_config SET restart_count = 0 WHERE user_id=? AND file_name=?',
                      (user_id, file_name))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error resetting restart count: {e}")


# ==================== MAINTENANCE MODE ====================

def set_maintenance(enabled, message=None, ends_at=None):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            if message:
                c.execute('UPDATE maintenance_mode SET enabled=?, message=?, ends_at=? WHERE id=1',
                          (1 if enabled else 0, message, ends_at))
            else:
                c.execute('UPDATE maintenance_mode SET enabled=?, ends_at=? WHERE id=1',
                          (1 if enabled else 0, ends_at))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting maintenance mode: {e}")


def get_maintenance_status():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT enabled, message, ends_at FROM maintenance_mode WHERE id=1')
        row = c.fetchone()
        conn.close()
        if row:
            return {'enabled': bool(row[0]), 'message': row[1], 'ends_at': row[2]}
        return {'enabled': False, 'message': '', 'ends_at': None}
    except Exception as e:
        logger.error(f"Error getting maintenance status: {e}")
        return {'enabled': False, 'message': '', 'ends_at': None}


# ==================== RESOURCE LOGS ====================

def log_resource_usage(user_id, file_name, cpu_percent, memory_mb):
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''INSERT INTO resource_logs (user_id, file_name, cpu_percent, memory_mb, logged_at)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, file_name, cpu_percent, memory_mb, datetime.now().isoformat()))
            # Keep only last 100 entries per script
            c.execute('''DELETE FROM resource_logs WHERE id NOT IN
                         (SELECT id FROM resource_logs WHERE user_id=? AND file_name=?
                          ORDER BY logged_at DESC LIMIT 100)
                         AND user_id=? AND file_name=?''',
                      (user_id, file_name, user_id, file_name))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging resource usage: {e}")


def get_latest_resource_usage(user_id, file_name):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('''SELECT cpu_percent, memory_mb, logged_at FROM resource_logs
                     WHERE user_id=? AND file_name=? ORDER BY logged_at DESC LIMIT 1''',
                  (user_id, file_name))
        row = c.fetchone()
        conn.close()
        if row:
            return {'cpu': row[0], 'memory_mb': row[1], 'logged_at': row[2]}
        return None
    except Exception as e:
        logger.error(f"Error getting resource usage: {e}")
        return None


# ==================== ENVIRONMENT VARIABLES ====================

def get_script_env(user_id, file_name):
    """Get all environment variables for a specific script."""
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('SELECT env_key, env_value FROM script_env_vars WHERE user_id=? AND file_name=?', 
                  (user_id, file_name))
        rows = c.fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        logger.error(f"Error getting script env vars: {e}")
        return {}

def set_script_env(user_id, file_name, env_key, env_value):
    """Set or update an environment variable for a script."""
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO script_env_vars 
                         (user_id, file_name, env_key, env_value)
                         VALUES (?, ?, ?, ?)''',
                      (user_id, file_name, env_key, env_value))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error setting script env var: {e}")
            return False

def delete_script_env(user_id, file_name, env_key):
    """Delete an environment variable from a script."""
    with DB_LOCK:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute('DELETE FROM script_env_vars WHERE user_id=? AND file_name=? AND env_key=?',
                      (user_id, file_name, env_key))
            removed = c.rowcount > 0
            conn.commit()
            conn.close()
            return removed
        except Exception as e:
            logger.error(f"Error deleting script env var: {e}")
            return False
