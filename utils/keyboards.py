"""
Keyboard builders — all Telegram inline and reply keyboard markup generation.
"""
from telebot import types
from config import UPDATE_CHANNEL, YOUR_USERNAME


def main_menu_inline(user_id, admin_ids, bot_locked, pending_count=0):
    """Build the main menu inline keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=2)

    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 My Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
        types.InlineKeyboardButton('📞 Contact Owner',
                                   url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'),
    ]

    if user_id in admin_ids:
        pending_label = f'📋 Pending ({pending_count})' if pending_count > 0 else '📋 Pending (0)'
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'))
        markup.add(buttons[3], buttons[4])
        markup.add(buttons[5])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(buttons[4])
        markup.add(buttons[5])

    markup.add(types.InlineKeyboardButton('⏱ Uptime', callback_data='uptime'))
    return markup


def reply_keyboard_main(user_id, admin_ids):
    """Build the reply keyboard for main menu."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    user_buttons = [
        ["📢 Updates Channel", "⏱ Uptime"],
        ["📤 Upload File", "📂 My Files"],
        ["⚡ Bot Speed", "📊 Statistics"],
        ["📞 Contact Owner"],
    ]

    admin_buttons = [
        ["📢 Updates Channel", "/ping"],
        ["📤 Upload File", "📂 My Files"],
        ["👑 Admin Panel"],
        ["⚡ Bot Speed", "📊 Statistics"],
        ["📞 Contact Owner", "⏱ Uptime"],
    ]

    layout = admin_buttons if user_id in admin_ids else user_buttons
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup


def file_control_buttons(script_owner_id, file_name, is_running=True, auto_restart_enabled=True):
    """Build control buttons for a specific file."""
    markup = types.InlineKeyboardMarkup(row_width=2)

    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📊 Resources", callback_data=f'resources_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )

    # Auto-restart toggle
    ar_label = "🔄 Auto-Restart: ON" if auto_restart_enabled else "🔄 Auto-Restart: OFF"
    markup.row(
        types.InlineKeyboardButton(ar_label, callback_data=f'toggle_ar_{script_owner_id}_{file_name}')
    )

    # Variables and Version history
    markup.row(
        types.InlineKeyboardButton("🔐 Variables", callback_data=f'envs_{script_owner_id}_{file_name}'),
        types.InlineKeyboardButton("📦 Versions", callback_data=f'versions_{script_owner_id}_{file_name}')
    )

    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup


def log_stream_keyboard(script_owner_id, file_name, is_streaming=False):
    """Build keyboard for log streaming."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_streaming:
        markup.add(types.InlineKeyboardButton("⏹ Stop Stream", callback_data=f"stoplog_{script_owner_id}_{file_name}"))
    else:
        markup.add(types.InlineKeyboardButton("🔄 Live Stream (30s)", callback_data=f"streamlog_{script_owner_id}_{file_name}"))
    
    markup.add(types.InlineKeyboardButton("🔙 Back to Script", callback_data=f"file_{script_owner_id}_{file_name}"))
    return markup


def env_vars_keyboard(script_owner_id, file_name, env_keys):
    """Build keyboard to manage environment variables for a script."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # List existing envs
    for key in env_keys:
        markup.add(types.InlineKeyboardButton(
            f"❌ Delete: {key}", 
            callback_data=f"delenv_{script_owner_id}_{file_name}_{key}"
        ))
        
    markup.add(types.InlineKeyboardButton(
        "➕ Add Variable", 
        callback_data=f"addenv_{script_owner_id}_{file_name}"
    ))
    markup.add(types.InlineKeyboardButton(
        "🔙 Back to Script", 
        callback_data=f"file_{script_owner_id}_{file_name}"
    ))
    return markup


def admin_panel_keyboard(bot_locked=False, pending_count=0):
    """Build the comprehensive admin panel keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    pending_label = f'📋 Pending ({pending_count})' if pending_count > 0 else '📋 Pending Files'
    
    markup.add(
        types.InlineKeyboardButton('📈 Dashboard', callback_data='dashboard'),
        types.InlineKeyboardButton(pending_label, callback_data='pending_files'),
        types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
        types.InlineKeyboardButton('🚫 Ban Manager', callback_data='ban_manager'),
        types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
        types.InlineKeyboardButton('🟢 Run All Scripts', callback_data='run_all_scripts'),
        types.InlineKeyboardButton('🔒 Lock' if not bot_locked else '🔓 Unlock', callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
        types.InlineKeyboardButton('🛠 Maintenance', callback_data='maintenance_mode'),
        types.InlineKeyboardButton('👥 Manage Admins', callback_data='manage_admins')
    )
    markup.add(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup


def manage_admins_keyboard():
    """Build admin management keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Admin Panel', callback_data='admin_panel'))
    return markup


def subscription_menu_keyboard():
    """Build subscription management keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Admin Panel', callback_data='admin_panel'))
    return markup


def approval_buttons(approval_id):
    """Build approve/reject buttons for a pending file."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('✅ Approve', callback_data=f'approve_{approval_id}'),
        types.InlineKeyboardButton('❌ Reject', callback_data=f'reject_{approval_id}')
    )
    return markup


def ban_manager_keyboard():
    """Build ban management keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('🚫 Ban User', callback_data='ban_user'),
        types.InlineKeyboardButton('✅ Unban User', callback_data='unban_user')
    )
    markup.row(types.InlineKeyboardButton('📋 Ban List', callback_data='ban_list'))
    markup.row(types.InlineKeyboardButton('🔙 Admin Panel', callback_data='admin_panel'))
    return markup


def version_list_keyboard(user_id, file_name, versions):
    """Build version list keyboard for file rollback."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for v in versions[:5]:  # Show max 5 versions
        markup.add(types.InlineKeyboardButton(
            f"v{v['version']} — {v['created_at'][:16]}",
            callback_data=f"rollback_{user_id}_{file_name}_{v['version']}"
        ))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f'file_{user_id}_{file_name}'))
    return markup


def maintenance_keyboard(is_enabled):
    """Build maintenance mode control keyboard."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_enabled:
        markup.add(types.InlineKeyboardButton('🟢 Disable Maintenance', callback_data='maintenance_off'))
    else:
        markup.add(types.InlineKeyboardButton('🔴 Enable Maintenance', callback_data='maintenance_on'))
    markup.add(types.InlineKeyboardButton('✏️ Set Message', callback_data='maintenance_msg'))
    markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='admin_panel'))
    return markup
