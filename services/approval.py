"""
Approval Service — manages the admin file approval workflow.
"""
import logging
import os

import database as db
from services.file_manager import save_file_to_pending, move_to_approved, delete_pending_file
from utils.keyboards import approval_buttons

logger = logging.getLogger(__name__)


def submit_for_approval(user_id, file_name, file_type, file_content, bot, admin_ids):
    """
    Submit a file for admin approval.
    Saves to pending directory and notifies all admins.
    Returns the approval ID.
    """
    # Save file to pending directory if content is provided
    if file_content is not None:
        save_file_to_pending(user_id, file_name, file_content)

    # Create DB record
    approval_id = db.add_pending_approval(user_id, file_name, file_type)
    if not approval_id:
        return None

    # Notify all admins
    notification = (
        f"📋 **New File Pending Approval**\n\n"
        f"👤 User: `{user_id}`\n"
        f"📄 File: `{file_name}`\n"
        f"📁 Type: `{file_type}`\n"
        f"🆔 Approval ID: `#{approval_id}`\n\n"
        f"Review and approve/reject this file."
    )

    markup = approval_buttons(approval_id)
    for admin_id in admin_ids:
        try:
            bot.send_message(admin_id, notification, reply_markup=markup, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id} about approval #{approval_id}: {e}")

    logger.info(f"Submitted approval #{approval_id}: {file_name} by user {user_id}")
    return approval_id


def handle_approve(approval_id, admin_id, bot):
    """
    Handle admin approving a file.
    Moves file to user's folder and notifies the user.
    Returns the approval info dict or None.
    """
    result = db.approve_file(approval_id, admin_id)
    if not result:
        return None

    user_id = result['user_id']
    file_name = result['file_name']
    file_type = result['file_type']

    # Move file from pending to user folder
    target_path = move_to_approved(user_id, file_name)
    if not target_path:
        logger.error(f"Failed to move approved file #{approval_id}")
        return None

    # Save to user files DB
    db.save_user_file(user_id, file_name, file_type)

    # Notify user
    try:
        bot.send_message(
            user_id,
            f"✅ **File Approved!**\n\n"
            f"Your file `{file_name}` has been approved by admin.\n"
            f"You can now start it from your file list.",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to notify user {user_id} about approval: {e}")

    logger.info(f"Approved #{approval_id}: {file_name} for user {user_id} by admin {admin_id}")
    return result


def handle_reject(approval_id, admin_id, reason, bot):
    """
    Handle admin rejecting a file.
    Deletes the pending file and notifies the user.
    Returns the rejection info dict or None.
    """
    result = db.reject_file(approval_id, admin_id, reason)
    if not result:
        return None

    user_id = result['user_id']
    file_name = result['file_name']

    # Delete pending file
    delete_pending_file(user_id, file_name)

    # Notify user
    try:
        bot.send_message(
            user_id,
            f"❌ **File Rejected**\n\n"
            f"Your file `{file_name}` was rejected by admin.\n"
            f"📝 Reason: {reason}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to notify user {user_id} about rejection: {e}")

    logger.info(f"Rejected #{approval_id}: {file_name} for user {user_id} by admin {admin_id}. Reason: {reason}")
    return result
