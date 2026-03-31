# BotMother - Telegram Bot Hosting Platform

BotMother is a production-grade Telegram bot designed to host, manage, and execute Python and Node.js scripts directly from Telegram. It provides a robust, self-hosted platform allowing users to easily deploy their bots and scripts with comprehensive administrative control.

## 🚀 Key Features

* **Script Hosting & Execution**: Easily upload and run `.py`, `.js`, and `.zip` files right from your Telegram chat.
* **Admin Approval System**: Built-in pending approval workflow for new script uploads to ensure server security.
* **Auto-Restart & Resource Monitoring**: Includes a background watchdog that monitors server memory usage (alerts at 80% usage) and automatically restarts failed or crashed scripts.
* **Subscription & Tiered Limits**: Configurable script execution quotas for Free users, Subscribed users, and Admins.
* **User Management & Bans**: Easily control platform access, restrict abusive users (`/ban`, `/unban`, `/banlist`), and broadcast announcements (`/broadcast`).
* **Maintenance Mode**: Admins can lock the bot temporarily during server maintenance (`/lockbot`).
* **Automated File Versioning**: Automatically keeps track of the last 3 versions of any uploaded script for easy rollbacks.
* **Keep-Alive Server**: Built-in Flask health-check endpoint to maintain 24/7 uptime on strict hosting providers.

## 🛠 Configuration & Environment Setup

The bot utilizes a `.env` file to securely load all private configurations. To run the bot, you must define the following variables in your `.env`:

* `BOT_TOKEN`: Your Telegram Bot API Token from BotFather.
* `OWNER_ID`: Your numerical Telegram User ID.
* `ADMIN_ID`: (Optional) Additional admin numerical Telegram User ID.
* `YOUR_USERNAME`: Your contact handle (e.g., `@aaka8h`).
* `UPDATE_CHANNEL`: Link to your official Telegram updates channel.

*(Optional advanced settings such as `FLASK_PORT`, `WATCHDOG_INTERVAL`, and `FREE_USER_LIMIT` can also be customized via `.env`)*

---

## 👨‍💻 Developer Information
- **Telegram Username**: [@aaka8h](https://t.me/aaka8h)

## 🔒 Security & `.gitignore` Details
To maintain the security of this project, a `.gitignore` file is strictly utilized. This ensures that sensitive information and unnecessary transient files are not tracked by version control or exposed publicly on GitHub.

The following items are securely ignored:
- `.env`: Contains private environment variables, API keys, and administrative IDs. **Never commit this file**.
- `__pycache__/` and `*.pyc`: Compiled Python files generated locally.
- `file_versions/` and `upload_bots/`: Local server directories handling temporary caching and user-uploaded script files.
- `pending_files/` and `data/`: Internal directories containing unapproved scripts and the main SQLite database (`bot_data.db`).
