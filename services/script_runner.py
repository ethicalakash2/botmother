"""
Script Runner — manages running Python and JS scripts as subprocesses.
Handles: starting, stopping, restarting, checking status, process tree cleanup.
"""
import subprocess
import os
import sys
import time
import threading
import logging
import psutil
import database as db
from datetime import datetime

from utils.installer import (
    install_pip_package, install_npm_package,
    detect_missing_python_module, detect_missing_node_module
)

logger = logging.getLogger(__name__)

# Global registry of running scripts: {script_key: {process, log_file, ...}}
running_scripts = {}
SCRIPTS_LOCK = threading.Lock()


def get_script_key(user_id, file_name):
    """Generate unique key for a script."""
    return f"{user_id}_{file_name}"


def is_running(user_id, file_name):
    """Check if a script is currently running."""
    script_key = get_script_key(user_id, file_name)
    script_info = running_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            alive = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not alive:
                logger.warning(f"Process {script_info['process'].pid} for {script_key} is dead. Cleaning up.")
                _cleanup_script(script_key)
            return alive
        except psutil.NoSuchProcess:
            logger.warning(f"Process for {script_key} no longer exists. Cleaning up.")
            _cleanup_script(script_key)
            return False
        except Exception as e:
            logger.error(f"Error checking process for {script_key}: {e}")
            return False
    return False


def get_running_count():
    """Get total number of running scripts."""
    count = 0
    for key in list(running_scripts.keys()):
        parts = key.split('_', 1)
        if len(parts) == 2:
            try:
                if is_running(int(parts[0]), parts[1]):
                    count += 1
            except (ValueError, Exception):
                pass
    return count


def get_user_running_count(user_id):
    """Get number of running scripts for a specific user."""
    count = 0
    for key, info in list(running_scripts.items()):
        if info.get('script_owner_id') == user_id:
            if is_running(user_id, info['file_name']):
                count += 1
    return count


def get_all_running():
    """Return snapshot of all running scripts."""
    result = []
    for key, info in list(running_scripts.items()):
        parts = key.split('_', 1)
        if len(parts) == 2:
            try:
                uid = int(parts[0])
                fname = parts[1]
                if is_running(uid, fname):
                    result.append({
                        'user_id': uid,
                        'file_name': fname,
                        'file_type': info.get('type', '?'),
                        'pid': info['process'].pid if info.get('process') else None,
                        'start_time': info.get('start_time'),
                    })
            except (ValueError, Exception):
                pass
    return result


def _cleanup_script(script_key):
    """Clean up script entry from registry."""
    with SCRIPTS_LOCK:
        info = running_scripts.pop(script_key, None)
        if info:
            if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                try:
                    info['log_file'].close()
                except Exception:
                    pass


def kill_process_tree(process_info):
    """Kill a process and all its children."""
    script_key = process_info.get('script_key', 'N/A')
    try:
        # Close log file first
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            if not process_info['log_file'].closed:
                try:
                    process_info['log_file'].close()
                except Exception as e:
                    logger.error(f"Error closing log for {script_key}: {e}")

        process = process_info.get('process')
        if not process or not hasattr(process, 'pid') or not process.pid:
            return

        pid = process.pid
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)

            # Terminate children
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
                except Exception:
                    try:
                        child.kill()
                    except Exception:
                        pass

            # Wait for children
            gone, alive = psutil.wait_procs(children, timeout=2)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass

            # Terminate parent
            try:
                parent.terminate()
                try:
                    parent.wait(timeout=2)
                except psutil.TimeoutExpired:
                    parent.kill()
            except psutil.NoSuchProcess:
                pass

            logger.info(f"Killed process tree for {script_key} (PID: {pid})")

        except psutil.NoSuchProcess:
            logger.warning(f"Process {pid} for {script_key} already gone.")
    except Exception as e:
        logger.error(f"Error killing process tree for {script_key}: {e}", exc_info=True)


def run_python_script(script_path, user_id, user_folder, file_name, bot, chat_id, attempt=1):
    """Run a Python script as a subprocess."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.send_message(chat_id, f"❌ Failed to run `{file_name}` after {max_attempts} attempts.", parse_mode='Markdown')
        return

    script_key = get_script_key(user_id, file_name)
    logger.info(f"Attempt {attempt} to run Python: {script_path} (Key: {script_key})")

    if not os.path.exists(script_path):
        bot.send_message(chat_id, f"❌ Script `{file_name}` not found!", parse_mode='Markdown')
        return

    # Pre-check for missing modules
    if attempt == 1:
        check_proc = None
        try:
            check_proc = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore'
            )
            stdout, stderr = check_proc.communicate(timeout=5)
            if check_proc.returncode != 0 and stderr:
                missing = detect_missing_python_module(stderr)
                if missing:
                    bot.send_message(chat_id, f"📦 Module `{missing}` not found. Installing...", parse_mode='Markdown')
                    success, msg = install_pip_package(missing)
                    if success:
                        bot.send_message(chat_id, f"✅ {msg}\nRetrying...", parse_mode='Markdown')
                        time.sleep(1)
                        threading.Thread(target=run_python_script,
                                         args=(script_path, user_id, user_folder, file_name, bot, chat_id, attempt + 1)).start()
                        return
                    else:
                        bot.send_message(chat_id, f"❌ {msg}", parse_mode='Markdown')
                        return
                else:
                    error_summary = stderr[:500]
                    bot.send_message(chat_id, f"❌ Error in `{file_name}`:\n```\n{error_summary}\n```", parse_mode='Markdown')
                    return
        except subprocess.TimeoutExpired:
            logger.info(f"Pre-check timed out for {script_key} — likely OK. Proceeding.")
            if check_proc and check_proc.poll() is None:
                check_proc.kill()
                check_proc.communicate()
        except Exception as e:
            logger.error(f"Pre-check error for {script_key}: {e}")
            bot.send_message(chat_id, f"⚠️ Pre-check error for `{file_name}`: {e}")
            return
        finally:
            if check_proc and check_proc.poll() is None:
                check_proc.kill()
                check_proc.communicate()

    # Start the long-running process
    log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    log_file = None
    process = None

    try:
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
    except Exception as e:
        bot.send_message(chat_id, f"❌ Cannot create log file: {e}")
        return

    try:
        startupinfo = None
        creationflags = 0
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        # Inject custom DB environment variables
        env = os.environ.copy()
        custom_envs = db.get_script_env(user_id, file_name)
        if custom_envs:
            env.update(custom_envs)

        process = subprocess.Popen(
            [sys.executable, script_path], cwd=user_folder,
            stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
            startupinfo=startupinfo, creationflags=creationflags,
            env=env, encoding='utf-8', errors='ignore'
        )

        with SCRIPTS_LOCK:
            running_scripts[script_key] = {
                'process': process,
                'log_file': log_file,
                'file_name': file_name,
                'chat_id': chat_id,
                'script_owner_id': user_id,
                'start_time': datetime.now(),
                'user_folder': user_folder,
                'type': 'py',
                'script_key': script_key,
            }

        logger.info(f"Started Python process PID={process.pid} for {script_key}")
        bot.send_message(chat_id,
                         f"✅ Python script `{file_name}` started!\n"
                         f"📌 PID: `{process.pid}` | Owner: `{user_id}`",
                         parse_mode='Markdown')

    except Exception as e:
        if log_file and not log_file.closed:
            log_file.close()
        if process and process.poll() is None:
            kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
        with SCRIPTS_LOCK:
            running_scripts.pop(script_key, None)
        bot.send_message(chat_id, f"❌ Error starting `{file_name}`: {str(e)}")
        logger.error(f"Error starting Python script {script_key}: {e}", exc_info=True)


def run_js_script(script_path, user_id, user_folder, file_name, bot, chat_id, attempt=1):
    """Run a JavaScript script as a subprocess."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.send_message(chat_id, f"❌ Failed to run `{file_name}` after {max_attempts} attempts.", parse_mode='Markdown')
        return

    script_key = get_script_key(user_id, file_name)
    logger.info(f"Attempt {attempt} to run JS: {script_path} (Key: {script_key})")

    if not os.path.exists(script_path):
        bot.send_message(chat_id, f"❌ Script `{file_name}` not found!", parse_mode='Markdown')
        return

    # Pre-check
    if attempt == 1:
        check_proc = None
        try:
            check_proc = subprocess.Popen(
                ['node', script_path], cwd=user_folder,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore'
            )
            stdout, stderr = check_proc.communicate(timeout=5)
            if check_proc.returncode != 0 and stderr:
                missing = detect_missing_node_module(stderr)
                if missing:
                    bot.send_message(chat_id, f"📦 Node module `{missing}` not found. Installing...", parse_mode='Markdown')
                    success, msg = install_npm_package(missing, user_folder)
                    if success:
                        bot.send_message(chat_id, f"✅ {msg}\nRetrying...", parse_mode='Markdown')
                        time.sleep(1)
                        threading.Thread(target=run_js_script,
                                         args=(script_path, user_id, user_folder, file_name, bot, chat_id, attempt + 1)).start()
                        return
                    else:
                        bot.send_message(chat_id, f"❌ {msg}", parse_mode='Markdown')
                        return
                else:
                    error_summary = stderr[:500]
                    bot.send_message(chat_id, f"❌ Error in `{file_name}`:\n```\n{error_summary}\n```", parse_mode='Markdown')
                    return
        except subprocess.TimeoutExpired:
            logger.info(f"JS Pre-check timed out for {script_key} — likely OK.")
            if check_proc and check_proc.poll() is None:
                check_proc.kill()
                check_proc.communicate()
        except FileNotFoundError:
            bot.send_message(chat_id, "❌ `node` not found. Node.js is not installed.")
            return
        except Exception as e:
            logger.error(f"JS pre-check error for {script_key}: {e}")
            bot.send_message(chat_id, f"⚠️ Pre-check error: {e}")
            return
        finally:
            if check_proc and check_proc.poll() is None:
                check_proc.kill()
                check_proc.communicate()

    # Start the long-running process
    log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    log_file = None
    process = None

    try:
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
    except Exception as e:
        bot.send_message(chat_id, f"❌ Cannot create log file: {e}")
        return

    try:
        startupinfo = None
        creationflags = 0
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        # Inject custom DB environment variables
        env = os.environ.copy()
        custom_envs = db.get_script_env(user_id, file_name)
        if custom_envs:
            env.update(custom_envs)

        process = subprocess.Popen(
            ['node', script_path], cwd=user_folder,
            stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
            startupinfo=startupinfo, creationflags=creationflags,
            env=env, encoding='utf-8', errors='ignore'
        )

        with SCRIPTS_LOCK:
            running_scripts[script_key] = {
                'process': process,
                'log_file': log_file,
                'file_name': file_name,
                'chat_id': chat_id,
                'script_owner_id': user_id,
                'start_time': datetime.now(),
                'user_folder': user_folder,
                'type': 'js',
                'script_key': script_key,
            }

        logger.info(f"Started JS process PID={process.pid} for {script_key}")
        bot.send_message(chat_id,
                         f"✅ JS script `{file_name}` started!\n"
                         f"📌 PID: `{process.pid}` | Owner: `{user_id}`",
                         parse_mode='Markdown')

    except FileNotFoundError:
        if log_file and not log_file.closed:
            log_file.close()
        bot.send_message(chat_id, "❌ `node` not found for running JS scripts.")
        with SCRIPTS_LOCK:
            running_scripts.pop(script_key, None)
    except Exception as e:
        if log_file and not log_file.closed:
            log_file.close()
        if process and process.poll() is None:
            kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
        with SCRIPTS_LOCK:
            running_scripts.pop(script_key, None)
        bot.send_message(chat_id, f"❌ Error starting `{file_name}`: {str(e)}")
        logger.error(f"Error starting JS script {script_key}: {e}", exc_info=True)


def stop_script(user_id, file_name):
    """Stop a running script."""
    script_key = get_script_key(user_id, file_name)
    info = running_scripts.get(script_key)
    if info:
        kill_process_tree(info)
        with SCRIPTS_LOCK:
            running_scripts.pop(script_key, None)
        logger.info(f"Stopped script {script_key}")
        return True
    return False


def stop_all_user_scripts(user_id):
    """Stop all scripts belonging to a user."""
    stopped = 0
    keys_to_stop = [k for k, v in running_scripts.items() if v.get('script_owner_id') == user_id]
    for key in keys_to_stop:
        info = running_scripts.get(key)
        if info:
            kill_process_tree(info)
            with SCRIPTS_LOCK:
                running_scripts.pop(key, None)
            stopped += 1
    return stopped


def get_script_resource_usage(user_id, file_name):
    """Get CPU and memory usage for a running script."""
    script_key = get_script_key(user_id, file_name)
    info = running_scripts.get(script_key)
    if info and info.get('process'):
        try:
            proc = psutil.Process(info['process'].pid)
            cpu = proc.cpu_percent(interval=0.5)
            mem = proc.memory_info().rss / (1024 * 1024)  # MB
            # Include children
            for child in proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=0.1)
                    mem += child.memory_info().rss / (1024 * 1024)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return {'cpu': round(cpu, 1), 'memory_mb': round(mem, 2)}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def get_log_content(user_id, file_name, user_folder, max_kb=100):
    """Read log file content for a script."""
    log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    if not os.path.exists(log_path):
        return None

    try:
        file_size = os.path.getsize(log_path)
        max_tg_msg = 4096

        if file_size == 0:
            return "(Log is empty)"

        if file_size > max_kb * 1024:
            with open(log_path, 'rb') as f:
                f.seek(-max_kb * 1024, os.SEEK_END)
                log_bytes = f.read()
            content = log_bytes.decode('utf-8', errors='ignore')
            content = f"(Last {max_kb} KB)\n...\n" + content
        else:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

        if len(content) > max_tg_msg - 100:
            content = content[-(max_tg_msg - 100):]
            first_nl = content.find('\n')
            if first_nl != -1:
                content = "...\n" + content[first_nl + 1:]

        return content.strip() or "(No visible content)"
    except Exception as e:
        logger.error(f"Error reading log for {file_name}: {e}")
        return f"(Error reading log: {e})"


def cleanup_all():
    """Stop all running scripts — called on shutdown."""
    logger.warning("Shutdown: cleaning up all running scripts...")
    keys = list(running_scripts.keys())
    for key in keys:
        info = running_scripts.get(key)
        if info:
            logger.info(f"Stopping: {key}")
            kill_process_tree(info)
    running_scripts.clear()
    logger.warning("Cleanup finished.")
