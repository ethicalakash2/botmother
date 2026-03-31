"""
File Manager — handles file uploads, zip extraction, storage, and versioning.
"""
import os
import shutil
import zipfile
import tempfile
import subprocess
import sys
import logging

import database as db
from config import UPLOAD_BOTS_DIR, PENDING_DIR, VERSIONS_DIR, MAX_VERSIONS_KEPT

logger = logging.getLogger(__name__)


def get_user_folder(user_id):
    """Get (and create) the folder for a user's scripts."""
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder


def get_pending_folder(user_id):
    """Get (and create) folder for pending approval files."""
    folder = os.path.join(PENDING_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder


def get_version_folder(user_id, file_name):
    """Get (and create) folder for file version backups."""
    folder = os.path.join(VERSIONS_DIR, str(user_id), os.path.splitext(file_name)[0])
    os.makedirs(folder, exist_ok=True)
    return folder


def save_file_to_pending(user_id, file_name, file_content):
    """Save uploaded file to pending directory for admin review."""
    pending_folder = get_pending_folder(user_id)
    file_path = os.path.join(pending_folder, file_name)
    with open(file_path, 'wb') as f:
        f.write(file_content)
    logger.info(f"Saved pending file: {file_path}")
    return file_path


def move_to_approved(user_id, file_name):
    """Move file from pending to user's active folder after approval."""
    pending_path = os.path.join(get_pending_folder(user_id), file_name)
    user_folder = get_user_folder(user_id)
    target_path = os.path.join(user_folder, file_name)

    if not os.path.exists(pending_path):
        logger.error(f"Pending file not found: {pending_path}")
        return None

    # Create version backup if file already exists
    if os.path.exists(target_path):
        create_version_backup(user_id, file_name, target_path)

    shutil.move(pending_path, target_path)
    logger.info(f"Moved approved file: {pending_path} -> {target_path}")
    return target_path


def delete_pending_file(user_id, file_name):
    """Delete a pending file (rejected or cleanup)."""
    pending_path = os.path.join(get_pending_folder(user_id), file_name)
    if os.path.exists(pending_path):
        os.remove(pending_path)
        logger.info(f"Deleted pending file: {pending_path}")
        return True
    return False


def create_version_backup(user_id, file_name, source_path):
    """Create a version backup of an existing file."""
    version_num = db.get_latest_version_number(user_id, file_name) + 1
    version_folder = get_version_folder(user_id, file_name)
    backup_name = f"v{version_num}_{file_name}"
    backup_path = os.path.join(version_folder, backup_name)

    shutil.copy2(source_path, backup_path)
    db.save_file_version(user_id, file_name, version_num, backup_path)

    # Cleanup old versions
    old_paths = db.delete_old_versions(user_id, file_name, keep=MAX_VERSIONS_KEPT)
    for old_path in old_paths:
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
                logger.info(f"Deleted old version: {old_path}")
            except OSError as e:
                logger.error(f"Failed to delete old version {old_path}: {e}")

    logger.info(f"Created version backup v{version_num} for {file_name} (user {user_id})")
    return version_num


def rollback_to_version(user_id, file_name, version):
    """Rollback a file to a specific version."""
    versions = db.get_file_versions(user_id, file_name)
    target_version = None
    for v in versions:
        if v['version'] == version:
            target_version = v
            break

    if not target_version:
        return False, "Version not found."

    backup_path = target_version['backup_path']
    if not os.path.exists(backup_path):
        return False, "Version backup file is missing."

    user_folder = get_user_folder(user_id)
    target_path = os.path.join(user_folder, file_name)

    # Backup current before rollback
    if os.path.exists(target_path):
        create_version_backup(user_id, file_name, target_path)

    shutil.copy2(backup_path, target_path)
    logger.info(f"Rolled back {file_name} to version {version} for user {user_id}")
    return True, f"Rolled back to version {version}."


def delete_user_file(user_id, file_name):
    """Delete a user's file and its log."""
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")

    deleted = []
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            deleted.append(file_name)
        except OSError as e:
            logger.error(f"Error deleting {file_path}: {e}")
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
            deleted.append(os.path.basename(log_path))
        except OSError as e:
            logger.error(f"Error deleting {log_path}: {e}")

    db.remove_user_file(user_id, file_name)
    return deleted


def delete_all_user_files(user_id):
    """Delete all files for a user (used when banning)."""
    user_folder = get_user_folder(user_id)
    if os.path.exists(user_folder):
        try:
            shutil.rmtree(user_folder)
            logger.info(f"Deleted all files for user {user_id}")
        except Exception as e:
            logger.error(f"Error deleting all files for {user_id}: {e}")

    # Clear DB records
    files = db.get_user_files(user_id)
    for fname, ftype in files:
        db.remove_user_file(user_id, fname)


def handle_zip_upload(file_content, zip_name, user_id, bot, chat_id):
    """
    Process a ZIP file upload for pending approval.
    Extracts, installs deps, finds main script, saves to pending.
    Returns (main_script_name, file_type) or None on error.
    """
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zip_path = os.path.join(temp_dir, zip_name)
        with open(zip_path, 'wb') as f:
            f.write(file_content)

        # Validate and extract
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Unsafe path in zip: {member.filename}")
            zip_ref.extractall(temp_dir)

        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        js_files = [f for f in extracted if f.endswith('.js')]

        # Install Python dependencies
        if 'requirements.txt' in extracted:
            req_path = os.path.join(temp_dir, 'requirements.txt')
            bot.send_message(chat_id, "📦 Installing Python dependencies...")
            try:
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', req_path],
                    capture_output=True, text=True, check=True,
                    encoding='utf-8', errors='ignore', timeout=120
                )
                bot.send_message(chat_id, "✅ Python dependencies installed.")
            except subprocess.CalledProcessError as e:
                error = (e.stderr or e.stdout)[:500]
                bot.send_message(chat_id, f"❌ Failed to install deps:\n```\n{error}\n```", parse_mode='Markdown')
                return None

        # Install Node dependencies
        if 'package.json' in extracted:
            bot.send_message(chat_id, "📦 Installing Node dependencies...")
            try:
                subprocess.run(
                    ['npm', 'install'], cwd=temp_dir,
                    capture_output=True, text=True, check=True,
                    encoding='utf-8', errors='ignore', timeout=120
                )
                bot.send_message(chat_id, "✅ Node dependencies installed.")
            except FileNotFoundError:
                bot.send_message(chat_id, "⚠️ `npm` not found. Skipping Node deps.")
            except subprocess.CalledProcessError as e:
                error = (e.stderr or e.stdout)[:500]
                bot.send_message(chat_id, f"❌ Failed to install Node deps:\n```\n{error}\n```", parse_mode='Markdown')
                return None

        # Find main script
        main_script = None
        file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']
        preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']

        for p in preferred_py:
            if p in py_files:
                main_script = p
                file_type = 'py'
                break
        if not main_script:
            for p in preferred_js:
                if p in js_files:
                    main_script = p
                    file_type = 'js'
                    break
        if not main_script:
            if py_files:
                main_script = py_files[0]
                file_type = 'py'
            elif js_files:
                main_script = js_files[0]
                file_type = 'js'

        if not main_script:
            bot.send_message(chat_id, "❌ No `.py` or `.js` file found in archive!")
            return None

        # Move all extracted files to pending folder
        pending_folder = get_pending_folder(user_id)
        for item_name in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item_name)
            dst = os.path.join(pending_folder, item_name)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)

        logger.info(f"ZIP extracted for user {user_id}, main script: {main_script}")
        return main_script, file_type

    except zipfile.BadZipFile as e:
        bot.send_message(chat_id, f"❌ Invalid ZIP file: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing ZIP for {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, f"❌ Error processing ZIP: {str(e)}")
        return None
    finally:
        if temp_dir and os.path.exists(temp_dir):
            import stat
            def remove_readonly(func, path, excinfo):
                os.chmod(path, stat.S_IWRITE)
                try: func(path)
                except Exception: pass
            shutil.rmtree(temp_dir, onerror=remove_readonly)


def handle_git_clone(git_url, user_id, bot, chat_id):
    """
    Clone a git repository, install dependencies, find main script, and save to pending.
    Returns (main_script_name, file_type) or None on error.
    """
    temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_git_")
    try:
        bot.send_message(chat_id, f"📥 Cloning repository `{git_url}`...", parse_mode='Markdown')
        try:
            subprocess.run(["git", "clone", "--depth", "1", git_url, temp_dir],
                           capture_output=True, text=True, check=True, timeout=60)
        except subprocess.CalledProcessError as e:
            bot.send_message(chat_id, f"❌ Failed to clone repository:\n```\n{e.stderr[:500]}\n```", parse_mode='Markdown')
            return None
        except FileNotFoundError:
            bot.send_message(chat_id, "❌ `git` is not installed on the server.")
            return None
            
        # Remove .git folder
        git_dir = os.path.join(temp_dir, '.git')
        if os.path.exists(git_dir):
            import stat
            def remove_readonly(func, path, excinfo):
                os.chmod(path, stat.S_IWRITE)
                try: func(path)
                except Exception: pass
            shutil.rmtree(git_dir, onerror=remove_readonly)
            
        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        js_files = [f for f in extracted if f.endswith('.js')]

        # Install Python dependencies
        if 'requirements.txt' in extracted:
            req_path = os.path.join(temp_dir, 'requirements.txt')
            bot.send_message(chat_id, "📦 Installing Python dependencies...")
            try:
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', req_path],
                    capture_output=True, text=True, check=True,
                    encoding='utf-8', errors='ignore', timeout=120
                )
                bot.send_message(chat_id, "✅ Python dependencies installed.")
            except subprocess.CalledProcessError as e:
                error = (e.stderr or e.stdout)[:500]
                bot.send_message(chat_id, f"❌ Failed to install deps:\n```\n{error}\n```", parse_mode='Markdown')
                return None

        # Find main script
        main_script = None
        file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']
        preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']

        for p in preferred_py:
            if p in py_files:
                main_script = p
                file_type = 'py'
                break
        if not main_script:
            for p in preferred_js:
                if p in js_files:
                    main_script = p
                    file_type = 'js'
                    break
        if not main_script:
            if py_files:
                main_script = py_files[0]
                file_type = 'py'
            elif js_files:
                main_script = js_files[0]
                file_type = 'js'

        if not main_script:
            bot.send_message(chat_id, "❌ No `.py` or `.js` file found in the repository!")
            return None

        # Move all files to pending folder
        pending_folder = get_pending_folder(user_id)
        for item_name in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item_name)
            dst = os.path.join(pending_folder, item_name)
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            elif os.path.exists(dst):
                try: os.remove(dst)
                except Exception: pass
            shutil.move(src, dst)

        logger.info(f"Git clone processed for user {user_id}, main script: {main_script}")
        return main_script, file_type

    except Exception as e:
        logger.error(f"Error processing git clone for {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, f"❌ Error processing clone: {str(e)}")
        return None
    finally:
        if temp_dir and os.path.exists(temp_dir):
            import stat
            def remove_readonly(func, path, excinfo):
                os.chmod(path, stat.S_IWRITE)
                try: func(path)
                except Exception: pass
            shutil.rmtree(temp_dir, onerror=remove_readonly)
