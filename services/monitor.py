"""
Monitor Service — Auto-restart watchdog and resource tracking.
Runs as a background thread, checking script health and collecting metrics.
"""
import threading
import time
import logging
import psutil
from datetime import datetime

import database as db
from services.script_runner import (
    running_scripts, is_running, get_script_resource_usage, stop_script,
    run_python_script, run_js_script, get_script_key
)
from services.file_manager import get_user_folder
from config import WATCHDOG_INTERVAL, MAX_RESTART_ATTEMPTS, MEMORY_ALERT_THRESHOLD

logger = logging.getLogger(__name__)

_monitor_thread = None
_stop_event = threading.Event()


def start_monitor(bot, admin_ids):
    """Start the background monitoring thread."""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        logger.warning("Monitor already running.")
        return

    _stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop, args=(bot, admin_ids),
        daemon=True, name="ScriptMonitor"
    )
    _monitor_thread.start()
    logger.info(f"Monitor started (interval={WATCHDOG_INTERVAL}s)")


def stop_monitor():
    """Stop the monitoring thread."""
    _stop_event.set()
    if _monitor_thread:
        _monitor_thread.join(timeout=10)
    logger.info("Monitor stopped.")


def _monitor_loop(bot, admin_ids):
    """Main monitoring loop — checks script health and collects metrics."""
    while not _stop_event.is_set():
        try:
            _check_scripts(bot, admin_ids)
            _collect_metrics(bot, admin_ids)
            _check_maintenance()
        except Exception as e:
            logger.error(f"Monitor loop error: {e}", exc_info=True)

        _stop_event.wait(WATCHDOG_INTERVAL)


def _check_scripts(bot, admin_ids):
    """Check all registered scripts and auto-restart crashed ones."""
    for key in list(running_scripts.keys()):
        try:
            parts = key.split('_', 1)
            if len(parts) != 2:
                continue

            user_id = int(parts[0])
            file_name = parts[1]
            info = running_scripts.get(key)

            if not info:
                continue

            process = info.get('process')
            if not process:
                continue

            # Check if process is still alive
            try:
                proc = psutil.Process(process.pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    # Process is healthy — reset restart counter on long-lived processes
                    start_time = info.get('start_time')
                    if start_time and (datetime.now() - start_time).seconds > 300:
                        db.reset_restart_count(user_id, file_name)
                    continue
            except psutil.NoSuchProcess:
                pass

            # Process has died — attempt auto-restart
            logger.warning(f"Script {key} has crashed. Checking auto-restart config...")

            ar_config = db.get_auto_restart(user_id, file_name)
            if not ar_config['enabled']:
                logger.info(f"Auto-restart disabled for {key}. Skipping.")
                _notify_crash(bot, admin_ids, user_id, file_name, auto_restarted=False)
                # Clean up
                if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                    try:
                        info['log_file'].close()
                    except Exception:
                        pass
                running_scripts.pop(key, None)
                continue

            if ar_config['restart_count'] >= ar_config['max_retries']:
                logger.warning(f"Max restart attempts ({ar_config['max_retries']}) reached for {key}.")
                _notify_crash(bot, admin_ids, user_id, file_name,
                              auto_restarted=False, max_retries_reached=True)
                if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                    try:
                        info['log_file'].close()
                    except Exception:
                        pass
                running_scripts.pop(key, None)
                continue

            # Auto-restart
            logger.info(f"Auto-restarting {key} (attempt {ar_config['restart_count'] + 1}/{ar_config['max_retries']})")
            db.increment_restart_count(user_id, file_name)

            # Clean up old entry
            if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                try:
                    info['log_file'].close()
                except Exception:
                    pass
            running_scripts.pop(key, None)

            # Restart
            user_folder = info.get('user_folder') or get_user_folder(user_id)
            import os
            script_path = os.path.join(user_folder, file_name)
            file_type = info.get('type', 'py')
            chat_id = info.get('chat_id')

            if chat_id:
                try:
                    bot.send_message(chat_id,
                                     f"🔄 Auto-restarting `{file_name}`...\n"
                                     f"(Attempt {ar_config['restart_count'] + 1}/{ar_config['max_retries']})",
                                     parse_mode='Markdown')
                except Exception:
                    pass

            if file_type == 'py':
                threading.Thread(target=run_python_script,
                                 args=(script_path, user_id, user_folder, file_name, bot, chat_id)).start()
            elif file_type == 'js':
                threading.Thread(target=run_js_script,
                                 args=(script_path, user_id, user_folder, file_name, bot, chat_id)).start()

            _notify_crash(bot, admin_ids, user_id, file_name, auto_restarted=True,
                          attempt=ar_config['restart_count'] + 1, max_retries=ar_config['max_retries'])

        except Exception as e:
            logger.error(f"Error checking script {key}: {e}", exc_info=True)


def _collect_metrics(bot, admin_ids):
    """Collect resource usage metrics and enforce hard limits for all running scripts."""
    # Hard limits (could be moved to config.py)
    MAX_RAM_MB = 200.0
    MAX_CPU_PERCENT = 80.0

    for key in list(running_scripts.keys()):
        try:
            parts = key.split('_', 1)
            if len(parts) != 2:
                continue
            user_id = int(parts[0])
            file_name = parts[1]

            usage = get_script_resource_usage(user_id, file_name)
            if usage:
                db.log_resource_usage(user_id, file_name, usage['cpu'], usage['memory_mb'])

                # Feature 4: Strict Resource Auto-Kill
                if usage['memory_mb'] > MAX_RAM_MB or usage['cpu'] > MAX_CPU_PERCENT:
                    logger.warning(f"Resource violation by {key}: CPU {usage['cpu']}%, RAM {usage['memory_mb']}MB. Auto-killing.")
                    
                    # Stop the offending script and disable its auto-restart
                    stop_script(user_id, file_name)
                    db.set_auto_restart(user_id, file_name, enabled=False)

                    # Notify the user
                    chat_id = running_scripts.get(key, {}).get('chat_id')
                    if chat_id:
                        try:
                            bot.send_message(chat_id, 
                                f"🚨 **RESOURCE LIMIT EXCEEDED** 🚨\n\n"
                                f"📄 File: `{file_name}`\n"
                                f"⚠️ Usage: CPU `{usage['cpu']}%`, RAM `{usage['memory_mb']}MB`\n"
                                f"🔻 Limits: CPU `{MAX_CPU_PERCENT}%`, RAM `{MAX_RAM_MB}MB`\n\n"
                                f"❌ **Your script has been forcibly killed and auto-restart disabled to protect the server.** "
                                f"Please review your code for memory leaks or infinite loops before restarting.", 
                                parse_mode='Markdown')
                        except Exception:
                            pass

                    # Notify admins
                    for a in admin_ids:
                        try:
                            bot.send_message(a,
                                f"🛡 **Auto-Kill Triggered**\n"
                                f"👤 User: `{user_id}`\n"
                                f"📄 File: `{file_name}`\n"
                                f"⚠️ Used: CPU {usage['cpu']}%, RAM {usage['memory_mb']}MB",
                                parse_mode='Markdown')
                        except Exception:
                            pass

        except Exception as e:
            logger.error(f"Error collecting metrics for {key}: {e}")


def _check_maintenance():
    """Check if maintenance mode should auto-disable."""
    status = db.get_maintenance_status()
    if status['enabled'] and status['ends_at']:
        try:
            ends_at = datetime.fromisoformat(status['ends_at'])
            if datetime.now() >= ends_at:
                db.set_maintenance(False)
                logger.info("Maintenance mode auto-disabled (scheduled end reached).")
        except ValueError:
            pass


def _notify_crash(bot, admin_ids, user_id, file_name, auto_restarted=False,
                   max_retries_reached=False, attempt=None, max_retries=None):
    """Notify admins about a script crash."""
    if auto_restarted:
        msg = (f"⚠️ **Script Crash Detected**\n\n"
               f"📄 File: `{file_name}`\n"
               f"👤 User: `{user_id}`\n"
               f"🔄 Auto-restarted (Attempt {attempt}/{max_retries})")
    elif max_retries_reached:
        msg = (f"🚨 **Script Crash — Max Retries Reached**\n\n"
               f"📄 File: `{file_name}`\n"
               f"👤 User: `{user_id}`\n"
               f"❌ Auto-restart disabled: max retries exhausted.")
    else:
        msg = (f"⚠️ **Script Crashed**\n\n"
               f"📄 File: `{file_name}`\n"
               f"👤 User: `{user_id}`\n"
               f"ℹ️ Auto-restart is disabled for this script.")

    for admin_id in admin_ids:
        try:
            bot.send_message(admin_id, msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id} about crash: {e}")
