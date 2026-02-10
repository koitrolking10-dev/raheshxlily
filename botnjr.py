# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import threading
import re
import sys
import atexit
import requests
from flask import Flask
from threading import Thread

# --- Flask Keep Alive ---
app = Flask('')

@app.route('/')
def home():
    return "NJR HOSTING - Running"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Server started")

# --- Configuration ---
TOKEN = '8545190651:AAHJdBFPN5sz7ovfqZVvNoKucGV_s0JekqQ'  # Replace with your token
OWNER_ID = 7624692476  # Replace with your Owner ID
ADMIN_ID = 7624692476  # Replace with your Admin ID
UPDATE_CHANNEL = 'https://t.me/telegram'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATABASE_PATH = os.path.join(DATA_DIR, 'bot_data.db')

# File limits
FREE_USER_LIMIT = 2  # Only 2 files for free users
PREMIUM_USER_LIMIT = 20
ADMIN_LIMIT = 100
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_files
                 (user_id INTEGER, file_name TEXT, file_type TEXT,
                  PRIMARY KEY (user_id, file_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS active_users
                 (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id INTEGER PRIMARY KEY)''')
    c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
    if ADMIN_ID != OWNER_ID:
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
    conn.commit()
    conn.close()

def load_data():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT user_id, expiry FROM subscriptions')
    for user_id, expiry in c.fetchall():
        try:
            user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
        except ValueError:
            pass
    
    c.execute('SELECT user_id, file_name, file_type FROM user_files')
    for user_id, file_name, file_type in c.fetchall():
        if user_id not in user_files:
            user_files[user_id] = []
        user_files[user_id].append((file_name, file_type))
    
    c.execute('SELECT user_id FROM active_users')
    active_users.update(user_id for (user_id,) in c.fetchall())
    
    c.execute('SELECT user_id FROM admins')
    admin_ids.update(user_id for (user_id,) in c.fetchall())
    
    conn.close()

init_db()
load_data()

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return PREMIUM_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
    return False

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            try:
                process_info['log_file'].close()
            except:
                pass
        
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        try:
                            child.kill()
                        except:
                            pass
                gone, alive = psutil.wait_procs(children, timeout=1)
                for p in alive:
                    try:
                        p.kill()
                    except:
                        pass
                try:
                    parent.terminate()
                    parent.wait(timeout=1)
                except:
                    try:
                        parent.kill()
                    except:
                        pass
            except psutil.NoSuchProcess:
                pass
    except:
        pass

# --- Package Installation ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'requests': 'requests',
    'flask': 'Flask',
    'psutil': 'psutil',
    'asyncio': None,
    'json': None,
    'os': None,
    'sys': None,
    're': None,
    'time': None,
    'datetime': None,
    'logging': None,
    'threading': None,
    'subprocess': None,
    'sqlite3': None,
}

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return False
    try:
        bot.reply_to(message, f"Installing {package_name}...")
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            bot.reply_to(message, f"Installed {package_name}")
            return True
        else:
            bot.reply_to(message, f"Failed to install {package_name}")
            return False
    except Exception as e:
        bot.reply_to(message, f"Error installing {package_name}: {str(e)}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"Installing {module_name}...")
        command = ['npm', 'install', module_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=user_folder)
        if result.returncode == 0:
            bot.reply_to(message, f"Installed {module_name}")
            return True
        else:
            bot.reply_to(message, f"Failed to install {module_name}")
            return False
    except FileNotFoundError:
        bot.reply_to(message, "npm not found. Install Node.js")
        return False
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")
        return False

# --- Script Running ---
def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"Failed to run {file_name}")
        return

    script_key = f"{script_owner_id}_{file_name}"
    
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"File {file_name} not found")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip()
                        if attempt_install_pip(module_name, message_obj):
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)).start()
                            return
                        else:
                            return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
            except:
                pass

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        
        process = subprocess.Popen(
            [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
            stdin=subprocess.PIPE, encoding='utf-8', errors='ignore'
        )
        
        bot_scripts[script_key] = {
            'process': process, 'log_file': log_file, 'file_name': file_name,
            'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
            'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
        }
        bot.reply_to(message_obj, f"Started {file_name} (PID: {process.pid})")
    except Exception as e:
        bot.reply_to(message_obj, f"Error starting {file_name}: {str(e)}")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"Failed to run {file_name}")
        return

    script_key = f"{script_owner_id}_{file_name}"
    
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"File {file_name} not found")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_command = ['node', script_path]
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip()
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj):
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)).start()
                                return
                            else:
                                return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
            except FileNotFoundError:
                bot.reply_to(message_obj, "Node.js not found")
                return
            except:
                pass

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        
        process = subprocess.Popen(
            ['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
            stdin=subprocess.PIPE, encoding='utf-8', errors='ignore'
        )
        
        bot_scripts[script_key] = {
            'process': process, 'log_file': log_file, 'file_name': file_name,
            'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
            'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js', 'script_key': script_key
        }
        bot.reply_to(message_obj, f"Started {file_name} (PID: {process.pid})")
    except Exception as e:
        bot.reply_to(message_obj, f"Error starting {file_name}: {str(e)}")

# --- Database Operations ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                  (user_id, file_name, file_type))
        conn.commit()
        conn.close()
        if user_id not in user_files:
            user_files[user_id] = []
        user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
        user_files[user_id].append((file_name, file_type))

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()
        if user_id in user_files:
            user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
            if not user_files[user_id]:
                del user_files[user_id]

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', 
                  (user_id, expiry.isoformat()))
        conn.commit()
        conn.close()
        user_subscriptions[user_id] = {'expiry': expiry}

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        if user_id in user_subscriptions:
            del user_subscriptions[user_id]

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
        conn.commit()
        conn.close()
        admin_ids.add(admin_id)

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
        conn.commit()
        conn.close()
        admin_ids.discard(admin_id)
        return True

# --- Menus ---
def create_main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('Upload File', callback_data='upload'),
        types.InlineKeyboardButton('My Files', callback_data='check_files'),
        types.InlineKeyboardButton('Status', callback_data='speed'),
    ]
    
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('Stats', callback_data='stats'),
            types.InlineKeyboardButton('Lock Bot' if not bot_locked else 'Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('Run All Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5])
        markup.add(admin_buttons[4])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('Back to Main', callback_data='back_to_main'))
    return markup

# --- File Handling ---
def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None
    
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as f:
            f.write(downloaded_file_content)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        extracted_items = os.listdir(temp_dir)
        py_files = [f for f in extracted_items if f.endswith('.py')]
        js_files = [f for f in extracted_items if f.endswith('.js')]
        
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None
        pkg_json = 'package.json' if 'package.json' in extracted_items else None
        
        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            bot.reply_to(message, f"Installing Python dependencies...")
            try:
                command = [sys.executable, '-m', 'pip', 'install', '-r', req_path]
                subprocess.run(command, capture_output=True, text=True, check=True)
                bot.reply_to(message, "Python dependencies installed")
            except Exception as e:
                bot.reply_to(message, f"Failed to install dependencies: {str(e)}")
                return
        
        if pkg_json:
            bot.reply_to(message, "Installing Node dependencies...")
            try:
                command = ['npm', 'install']
                subprocess.run(command, capture_output=True, text=True, check=True, cwd=temp_dir)
                bot.reply_to(message, "Node dependencies installed")
            except Exception as e:
                bot.reply_to(message, f"Failed to install dependencies: {str(e)}")
                return
        
        main_script_name = None
        file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']
        preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']
        
        for p in preferred_py:
            if p in py_files:
                main_script_name = p
                file_type = 'py'
                break
        if not main_script_name:
            for p in preferred_js:
                if p in js_files:
                    main_script_name = p
                    file_type = 'js'
                    break
        if not main_script_name:
            if py_files:
                main_script_name = py_files[0]
                file_type = 'py'
            elif js_files:
                main_script_name = js_files[0]
                file_type = 'js'
        
        if not main_script_name:
            bot.reply_to(message, "No Python or JavaScript file found in archive")
            return
        
        for item_name in os.listdir(temp_dir):
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(user_folder, item_name)
            if os.path.isdir(dest_path):
                shutil.rmtree(dest_path)
            elif os.path.exists(dest_path):
                os.remove(dest_path)
            shutil.move(src_path, dest_path)
        
        save_user_file(user_id, main_script_name, file_type)
        main_script_path = os.path.join(user_folder, main_script_name)
        bot.reply_to(message, f"Starting {main_script_name}...")
        
        if file_type == 'py':
            threading.Thread(target=run_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
            
    except Exception as e:
        bot.reply_to(message, f"Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

def handle_js_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'js')
        threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"Error processing file: {str(e)}")

def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"Error processing file: {str(e)}")

# --- Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "Bot is locked. Try later.")
        return
    
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"New user: {user_name} (ID: {user_id})")
        except:
            pass
    
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = "Unlimited" if file_limit == float('inf') else str(file_limit)
    
    if user_id == OWNER_ID:
        user_status = "Owner"
    elif user_id in admin_ids:
        user_status = "Admin"
    elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        user_status = "Premium"
    else:
        user_status = "Free User (2 files limit)"
    
    welcome_text = (
        f"Welcome, {user_name}!\n\n"
        f"ID: {user_id}\n"
        f"Status: {user_status}\n"
        f"Files: {current_files}/{limit_str}\n\n"
        f"Upload Python (.py) or JavaScript (.js) files to host them 24/7.\n"
        f"Free users can upload only 2 files. Upgrade to Premium for more."
    )
    
    bot.send_message(chat_id, welcome_text, reply_markup=create_main_menu(user_id))

@bot.message_handler(commands=['stats'])
def show_stats(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(files) for files in user_files.values())
    running_bots = sum(1 for key, info in bot_scripts.items() if is_bot_running(info['script_owner_id'], info['file_name']))
    
    stats_text = (
        f"Statistics:\n\n"
        f"Total Users: {total_users}\n"
        f"Total Files: {total_files}\n"
        f"Running Bots: {running_bots}\n"
    )
    
    if user_id in admin_ids:
        stats_text += f"Bot Status: {'Locked' if bot_locked else 'Unlocked'}"
    
    bot.reply_to(message, stats_text)

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    
    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats']:
        bot.answer_callback_query(call.id, "Bot is locked")
        return
    
    try:
        if data == 'upload':
            handle_upload_callback(call)
        elif data == 'check_files':
            handle_check_files(call)
        elif data.startswith('file_'):
            handle_file_control(call)
        elif data.startswith('start_'):
            handle_start(call)
        elif data.startswith('stop_'):
            handle_stop(call)
        elif data.startswith('restart_'):
            handle_restart(call)
        elif data.startswith('delete_'):
            handle_delete(call)
        elif data.startswith('logs_'):
            handle_logs(call)
        elif data == 'speed':
            handle_speed(call)
        elif data == 'back_to_main':
            handle_back_to_main(call)
        elif data == 'subscription' and user_id in admin_ids:
            handle_subscription(call)
        elif data == 'stats':
            handle_stats_callback(call)
        elif data == 'lock_bot' and user_id in admin_ids:
            handle_lock(call)
        elif data == 'unlock_bot' and user_id in admin_ids:
            handle_unlock(call)
        elif data == 'broadcast' and user_id in admin_ids:
            handle_broadcast_init(call)
        elif data == 'run_all_scripts' and user_id in admin_ids:
            handle_run_all_scripts(call)
        elif data == 'admin_panel' and user_id in admin_ids:
            handle_admin_panel(call)
        elif data == 'add_admin' and user_id == OWNER_ID:
            handle_add_admin_init(call)
        elif data == 'remove_admin' and user_id == OWNER_ID:
            handle_remove_admin_init(call)
        elif data == 'list_admins' and user_id in admin_ids:
            handle_list_admins(call)
        elif data == 'add_subscription' and user_id in admin_ids:
            handle_add_subscription_init(call)
        elif data == 'remove_subscription' and user_id in admin_ids:
            handle_remove_subscription_init(call)
        elif data == 'check_subscription' and user_id in admin_ids:
            handle_check_subscription_init(call)
    except Exception as e:
        bot.answer_callback_query(call.id, "Error processing request")

def handle_upload_callback(call):
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    
    if current_files >= file_limit:
        limit_str = "Unlimited" if file_limit == float('inf') else str(file_limit)
        bot.answer_callback_query(call.id, f"File limit reached ({current_files}/{limit_str}). Delete files or upgrade to Premium.")
        return
    
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Send your Python (.py), JavaScript (.js), or ZIP (.zip) file.")

def handle_check_files(call):
    user_id = call.from_user.id
    user_files_list = user_files.get(user_id, [])
    
    if not user_files_list:
        bot.answer_callback_query(call.id, "No files uploaded")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Back to Main", callback_data='back_to_main'))
        bot.edit_message_text("Your files:\n\n(No files)", call.message.chat.id, call.message.message_id, reply_markup=markup)
        return
    
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "Running" if is_running else "Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    
    markup.add(types.InlineKeyboardButton("Back to Main", callback_data='back_to_main'))
    bot.edit_message_text("Your files:", call.message.chat.id, call.message.message_id, reply_markup=markup)

def handle_file_control(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "File not found")
            return
        
        bot.answer_callback_query(call.id)
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = 'Running' if is_running else 'Stopped'
        file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?')
        
        bot.edit_message_text(
            f"Controls for: {file_name} ({file_type})\nStatus: {status_text}",
            call.message.chat.id, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_running)
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "Error")

def handle_start(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id = call.message.chat.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        
        if not file_info:
            bot.answer_callback_query(call.id, "File not found")
            return
        
        file_type = file_info[1]
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, "File missing, re-upload")
            remove_user_file_db(script_owner_id, file_name)
            return
        
        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, "Already running")
            return
        
        bot.answer_callback_query(call.id, f"Starting {file_name}...")
        
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        
        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name)
        
        bot.edit_message_text(
            f"Controls for: {file_name} ({file_type})\nStatus: {'Running' if is_now_running else 'Starting'}",
            chat_id, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running)
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "Error starting")

def handle_stop(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        script_key = f"{script_owner_id}_{file_name}"
        
        if not is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, "Already stopped")
            return
        
        bot.answer_callback_query(call.id, f"Stopping {file_name}...")
        
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
        
        bot.edit_message_text(
            f"Controls for: {file_name}\nStatus: Stopped",
            call.message.chat.id, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, False)
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "Error stopping")

def handle_restart(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        
        if not file_info:
            bot.answer_callback_query(call.id, "File not found")
            return
        
        file_type = file_info[1]
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        script_key = f"{script_owner_id}_{file_name}"
        
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, "File missing")
            return
        
        bot.answer_callback_query(call.id, f"Restarting {file_name}...")
        
        if is_bot_running(script_owner_id, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info:
                kill_process_tree(process_info)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            time.sleep(1.5)
        
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        
        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name)
        
        bot.edit_message_text(
            f"Controls for: {file_name} ({file_type})\nStatus: {'Running' if is_now_running else 'Starting'}",
            call.message.chat.id, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running)
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "Error restarting")

def handle_delete(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        script_key = f"{script_owner_id}_{file_name}"
        
        if is_bot_running(script_owner_id, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info:
                kill_process_tree(process_info)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
        
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(log_path):
            os.remove(log_path)
        
        remove_user_file_db(script_owner_id, file_name)
        
        bot.edit_message_text(
            f"Deleted {file_name}",
            call.message.chat.id, call.message.message_id,
            reply_markup=None
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "Error deleting")

def handle_logs(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "Permission denied")
            return
        
        user_folder = get_user_folder(script_owner_id)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, "No logs found")
            return
        
        bot.answer_callback_query(call.id)
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()
        
        if len(log_content) > 4000:
            log_content = "...\n" + log_content[-4000:]
        
        bot.send_message(call.message.chat.id, f"Logs for {file_name}:\n```\n{log_content}\n```", parse_mode='Markdown')
    except Exception as e:
        bot.answer_callback_query(call.id, "Error reading logs")

def handle_speed(call):
    user_id = call.from_user.id
    start_time = time.time()
    
    try:
        bot.edit_message_text("Testing...", call.message.chat.id, call.message.message_id)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if user_id == OWNER_ID:
            user_level = "Owner"
        elif user_id in admin_ids:
            user_level = "Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
            user_level = "Premium"
        else:
            user_level = "Free User"
        
        speed_text = (
            f"Status:\n\n"
            f"Response Time: {response_time} ms\n"
            f"Bot Status: {'Locked' if bot_locked else 'Unlocked'}\n"
            f"Your Level: {user_level}"
        )
        
        bot.edit_message_text(speed_text, call.message.chat.id, call.message.message_id, reply_markup=create_main_menu(user_id))
    except Exception as e:
        bot.answer_callback_query(call.id, "Error")

def handle_back_to_main(call):
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = "Unlimited" if file_limit == float('inf') else str(file_limit)
    
    if user_id == OWNER_ID:
        user_status = "Owner"
    elif user_id in admin_ids:
        user_status = "Admin"
    elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        user_status = "Premium"
    else:
        user_status = "Free User (2 files limit)"
    
    main_text = (
        f"Welcome back, {call.from_user.first_name}!\n\n"
        f"ID: {user_id}\n"
        f"Status: {user_status}\n"
        f"Files: {current_files}/{limit_str}\n\n"
        f"Upload Python or JavaScript files to host them 24/7."
    )
    
    bot.edit_message_text(main_text, call.message.chat.id, call.message.message_id, reply_markup=create_main_menu(user_id))

def handle_subscription(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("Subscription Management:", call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())

def handle_stats_callback(call):
    bot.answer_callback_query(call.id)
    show_stats(call.message)

def handle_lock(call):
    global bot_locked
    bot_locked = True
    bot.answer_callback_query(call.id, "Bot locked")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu(call.from_user.id))

def handle_unlock(call):
    global bot_locked
    bot_locked = False
    bot.answer_callback_query(call.id, "Bot unlocked")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu(call.from_user.id))

# --- Broadcast to User Bots Feature ---
def handle_run_all_scripts(call):
    admin_user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Starting all user scripts...")
    
    started_count = 0
    
    for target_user_id, files_for_user in user_files.items():
        user_folder = get_user_folder(target_user_id)
        
        for file_name, file_type in files_for_user:
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                        started_count += 1
                        time.sleep(0.5)
                    except:
                        pass
    
    bot.send_message(call.message.chat.id, f"Started {started_count} scripts")

# --- NEW: Broadcast to User Hosted Bots ---
def handle_broadcast_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Send message to broadcast to ALL users and their hosted bots.\n\nFormat: [MESSAGE]\nOr send photo/video with caption.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids:
        bot.reply_to(message, "Not authorized")
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    
    # Store the broadcast message for confirmation
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("Confirm Broadcast", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("Cancel", callback_data="cancel_broadcast")
    )
    
    preview = message.text[:500] if message.text else "(Media message)"
    bot.reply_to(message, f"Preview:\n\n{preview}\n\nBroadcast to all users?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_broadcast_'))
def execute_broadcast_callback(call):
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "Not authorized")
        return
    
    try:
        original_msg = call.message.reply_to_message
        if not original_msg:
            bot.edit_message_text("Error: Original message not found", call.message.chat.id, call.message.message_id)
            return
        
        bot.answer_callback_query(call.id, "Broadcasting...")
        bot.edit_message_text("Broadcasting to all users...", call.message.chat.id, call.message.message_id)
        
        # Broadcast to all active users
        sent_count = 0
        failed_count = 0
        
        for user_id in list(active_users):
            try:
                if original_msg.text:
                    bot.send_message(user_id, f"📢 Broadcast from Admin:\n\n{original_msg.text}")
                elif original_msg.photo:
                    bot.send_photo(user_id, original_msg.photo[-1].file_id, caption=f"📢 Broadcast from Admin:\n\n{original_msg.caption or ''}")
                elif original_msg.video:
                    bot.send_video(user_id, original_msg.video.file_id, caption=f"📢 Broadcast from Admin:\n\n{original_msg.caption or ''}")
                sent_count += 1
                time.sleep(0.1)
            except Exception as e:
                failed_count += 1
        
        # Also try to broadcast to running bot scripts if they have chat_id
        bot_scripts_broadcast_count = 0
        for script_key, script_info in bot_scripts.items():
            try:
                chat_id = script_info.get('chat_id')
                if chat_id and chat_id not in active_users:
                    if original_msg.text:
                        bot.send_message(chat_id, f"📢 Broadcast from Admin:\n\n{original_msg.text}")
                    elif original_msg.photo:
                        bot.send_photo(chat_id, original_msg.photo[-1].file_id, caption=f"📢 Broadcast from Admin:\n\n{original_msg.caption or ''}")
                    elif original_msg.video:
                        bot.send_video(chat_id, original_msg.video.file_id, caption=f"📢 Broadcast from Admin:\n\n{original_msg.caption or ''}")
                    bot_scripts_broadcast_count += 1
            except:
                pass
        
        result = f"Broadcast complete!\n\nSent to users: {sent_count}\nFailed: {failed_count}\nAlso sent to {bot_scripts_broadcast_count} bot script chats"
        bot.send_message(call.message.chat.id, result)
        
    except Exception as e:
        bot.edit_message_text(f"Error: {str(e)}", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_broadcast')
def cancel_broadcast_callback(call):
    bot.answer_callback_query(call.id, "Cancelled")
    bot.delete_message(call.message.chat.id, call.message.message_id)

def handle_admin_panel(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("Admin Panel:", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())

def handle_add_admin_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Enter User ID to add as admin:\n/cancel to abort")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "Owner only")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id in admin_ids:
            bot.reply_to(message, "Already admin")
            return
        add_admin_db(new_admin_id)
        bot.reply_to(message, f"Added admin: {new_admin_id}")
        try:
            bot.send_message(new_admin_id, "You are now an Admin")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "Invalid ID. Send numbers only.")
        msg = bot.send_message(message.chat.id, "Enter User ID:\n/cancel to abort")
        bot.register_next_step_handler(msg, process_add_admin)

def handle_remove_admin_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Enter Admin ID to remove:\n/cancel to abort")
    bot.register_next_step_handler(msg, process_remove_admin)

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "Owner only")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    try:
        admin_id_remove = int(message.text.strip())
        if admin_id_remove == OWNER_ID:
            bot.reply_to(message, "Cannot remove owner")
            return
        if admin_id_remove not in admin_ids:
            bot.reply_to(message, "Not an admin")
            return
        remove_admin_db(admin_id_remove)
        bot.reply_to(message, f"Removed admin: {admin_id_remove}")
        try:
            bot.send_message(admin_id_remove, "You are no longer an Admin")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "Invalid ID")
        msg = bot.send_message(message.chat.id, "Enter Admin ID:\n/cancel to abort")
        bot.register_next_step_handler(msg, process_remove_admin)

def handle_list_admins(call):
    bot.answer_callback_query(call.id)
    admin_list = "\n".join([f"- {aid} {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(admin_ids)])
    bot.edit_message_text(f"Admins:\n\n{admin_list}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())

def handle_add_subscription_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Enter User ID and days (e.g., 12345678 30):\n/cancel to abort")
    bot.register_next_step_handler(msg, process_add_subscription)

def process_add_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "Not authorized")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError("Invalid format")
        user_id = int(parts[0])
        days = int(parts[1])
        
        current_expiry = user_subscriptions.get(user_id, {}).get('expiry')
        start_date = datetime.now()
        if current_expiry and current_expiry > start_date:
            start_date = current_expiry
        new_expiry = start_date + timedelta(days=days)
        
        save_subscription(user_id, new_expiry)
        bot.reply_to(message, f"Subscription added for {user_id}\nExpires: {new_expiry.strftime('%Y-%m-%d')}")
        try:
            bot.send_message(user_id, f"Your Premium subscription is active!\nExpires: {new_expiry.strftime('%Y-%m-%d')}")
        except:
            pass
    except ValueError as e:
        bot.reply_to(message, f"Error: {e}\nUse format: ID days")
        msg = bot.send_message(message.chat.id, "Enter User ID and days:\n/cancel to abort")
        bot.register_next_step_handler(msg, process_add_subscription)

def handle_remove_subscription_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Enter User ID to remove subscription:\n/cancel to abort")
    bot.register_next_step_handler(msg, process_remove_subscription)

def process_remove_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "Not authorized")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    try:
        user_id = int(message.text.strip())
        if user_id not in user_subscriptions:
            bot.reply_to(message, "No active subscription")
            return
        remove_subscription_db(user_id)
        bot.reply_to(message, f"Subscription removed for {user_id}")
        try:
            bot.send_message(user_id, "Your Premium subscription has been removed")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "Invalid ID")
        msg = bot.send_message(message.chat.id, "Enter User ID:\n/cancel to abort")
        bot.register_next_step_handler(msg, process_remove_subscription)

def handle_check_subscription_init(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Enter User ID to check subscription:\n/cancel to abort")
    bot.register_next_step_handler(msg, process_check_subscription)

def process_check_subscription(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "Not authorized")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled")
        return
    try:
        user_id = int(message.text.strip())
        if user_id in user_subscriptions:
            expiry = user_subscriptions[user_id]['expiry']
            if expiry > datetime.now():
                days_left = (expiry - datetime.now()).days
                bot.reply_to(message, f"User {user_id} has Premium\nExpires: {expiry.strftime('%Y-%m-%d')} ({days_left} days left)")
            else:
                bot.reply_to(message, f"User {user_id} subscription expired on {expiry.strftime('%Y-%m-%d')}")
                remove_subscription_db(user_id)
        else:
            bot.reply_to(message, f"User {user_id} has no subscription (Free User - 2 files limit)")
    except ValueError:
        bot.reply_to(message, "Invalid ID")
        msg = bot.send_message(message.chat.id, "Enter User ID:\n/cancel to abort")
        bot.register_next_step_handler(msg, process_check_subscription)

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    doc = message.document
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "Bot is locked")
        return
    
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    
    if current_files >= file_limit:
        limit_str = "Unlimited" if file_limit == float('inf') else str(file_limit)
        bot.reply_to(message, f"File limit reached ({current_files}/{limit_str}). Delete files or upgrade to Premium.")
        return
    
    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, "File has no name")
        return
    
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "Only .py, .js, .zip files allowed")
        return
    
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "File too large (Max: 20 MB)")
        return
    
    try:
        # Forward to owner
        try:
            bot.forward_message(OWNER_ID, message.chat.id, message.message_id)
        except:
            pass
        
        file_info = bot.get_file(doc.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        user_folder = get_user_folder(user_id)
        
        if file_ext == '.zip':
            handle_zip_file(downloaded_file, file_name, message)
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f:
                f.write(downloaded_file)
            
            if file_ext == '.js':
                handle_js_file(file_path, user_id, user_folder, file_name, message)
            elif file_ext == '.py':
                handle_py_file(file_path, user_id, user_folder, file_name, message)
                
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")

# --- Cleanup ---
def cleanup():
    for key in list(bot_scripts.keys()):
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])

atexit.register(cleanup)

# --- Main ---
if __name__ == '__main__':
    print("NJR HOSTING Bot Starting...")
    keep_alive()
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
