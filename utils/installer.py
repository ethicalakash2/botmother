"""
Auto-installer for missing Python pip and Node npm packages.
"""
import subprocess
import sys
import re
import logging

logger = logging.getLogger(__name__)

# Common module -> pip package mapping
PIP_PACKAGE_MAP = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon',
    'bs4': 'beautifulsoup4',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'PIL': 'Pillow',
    'pillow': 'Pillow',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'requests': 'requests',
    'aiohttp': 'aiohttp',
    'fastapi': 'fastapi',
    'uvicorn': 'uvicorn',
    'pydantic': 'pydantic',
    'redis': 'redis',
    'celery': 'celery',
    'boto3': 'boto3',
    'pymongo': 'pymongo',
    'motor': 'motor',
    'httpx': 'httpx',
    'websockets': 'websockets',
    'cryptography': 'cryptography',
    'jwt': 'PyJWT',
    'tgcrypto': 'TgCrypto',
}

# Standard library modules — don't try to install these
STDLIB_MODULES = {
    'asyncio', 'json', 'datetime', 'os', 'sys', 're', 'time', 'math',
    'random', 'logging', 'threading', 'subprocess', 'zipfile', 'tempfile',
    'shutil', 'sqlite3', 'atexit', 'signal', 'hashlib', 'base64',
    'collections', 'functools', 'itertools', 'pathlib', 'typing',
    'io', 'csv', 'xml', 'html', 'http', 'urllib', 'uuid', 'copy',
    'traceback', 'inspect', 'contextlib', 'abc', 'enum', 'dataclasses',
    'argparse', 'configparser', 'struct', 'socket', 'ssl', 'email',
    'multiprocessing', 'concurrent', 'queue', 'heapq', 'bisect',
}


def get_pip_package_name(module_name):
    """Get the pip package name for a module."""
    base_module = module_name.split('.')[0].lower()
    if base_module in STDLIB_MODULES:
        return None
    return PIP_PACKAGE_MAP.get(base_module, base_module)


def install_pip_package(module_name):
    """Attempt to install a pip package. Returns (success, message)."""
    package_name = get_pip_package_name(module_name)
    if package_name is None:
        return False, f"'{module_name}' is a standard library module."

    try:
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        logger.info(f"Installing pip package: {' '.join(command)}")
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            encoding='utf-8', errors='ignore', timeout=120
        )
        if result.returncode == 0:
            logger.info(f"Installed {package_name} successfully")
            return True, f"Package `{package_name}` installed successfully."
        else:
            error = result.stderr or result.stdout
            logger.error(f"Failed to install {package_name}: {error[:500]}")
            return False, f"Failed to install `{package_name}`:\n```\n{error[:500]}\n```"
    except subprocess.TimeoutExpired:
        return False, f"Installation of `{package_name}` timed out."
    except Exception as e:
        logger.error(f"Error installing {package_name}: {e}", exc_info=True)
        return False, f"Error installing `{package_name}`: {str(e)}"


def install_npm_package(module_name, cwd):
    """Attempt to install an npm package locally. Returns (success, message)."""
    try:
        command = ['npm', 'install', module_name]
        logger.info(f"Installing npm package: {' '.join(command)} in {cwd}")
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            cwd=cwd, encoding='utf-8', errors='ignore', timeout=120
        )
        if result.returncode == 0:
            logger.info(f"Installed npm package {module_name}")
            return True, f"Package `{module_name}` installed."
        else:
            error = result.stderr or result.stdout
            return False, f"Failed to install `{module_name}`:\n```\n{error[:500]}\n```"
    except FileNotFoundError:
        return False, "Error: `npm` not found. Node.js may not be installed."
    except subprocess.TimeoutExpired:
        return False, f"Installation of `{module_name}` timed out."
    except Exception as e:
        logger.error(f"Error installing npm {module_name}: {e}", exc_info=True)
        return False, f"Error installing `{module_name}`: {str(e)}"


def detect_missing_python_module(stderr):
    """Detect missing Python module from error output."""
    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def detect_missing_node_module(stderr):
    """Detect missing Node module from error output."""
    match = re.search(r"Cannot find module '(.+?)'", stderr)
    if match:
        module = match.group(1).strip().strip("'\"")
        if not module.startswith('.') and not module.startswith('/'):
            return module
    return None
