import asyncio
import sqlite3
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
import random
import re
import json

from telethon import TelegramClient, events, Button, types
from telethon.tl.functions.channels import (
    InviteToChannelRequest,
    GetParticipantsRequest,
    JoinChannelRequest
)
from telethon.tl.functions.messages import (
    AddChatUserRequest,
    GetHistoryRequest,
    SearchRequest
)
from telethon.tl.types import (
    InputPeerChannel,
    ChannelParticipantsRecent,
    Channel,
    User,
    PeerChannel,
    InputChannel,
    InputMessagesFilterEmpty
)
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, UserNotMutualContactError,
    UserChannelsTooMuchError, ChatAdminRequiredError, ChannelPrivateError,
    UsernameNotOccupiedError, InviteHashInvalidError
)
from telethon.tl.functions.auth import SendCodeRequest
from telethon.tl.types import CodeSettings

# ========== КОНФИГУРАЦИЯ ==========
API_ID = APIID
API_HASH = 'API_HASH'
BOT_TOKEN = 'TOKEN'
LOG_CHAT = '??'

# ========== ДОСТУП ==========
ACCESS_PASSWORD = ""  # Пароль для доступа
AUTHORIZED_USERS = set()  # Авторизованные пользователи (заполняется после ввода пароля)

# ========== ПАТИ ==========
BASE_DIR = Path.home() / 'Desktop' / 'SWILL_PARSER_PRO'
SESSIONS_DIR = BASE_DIR / 'sessions'
EXPORTS_DIR = BASE_DIR / 'exports'
LOGS_DIR = BASE_DIR / 'logs'
DB_FILE = BASE_DIR / 'swill_pro.db'
USER_LOGS_FILE = LOGS_DIR / 'user_actions.log'

for folder in [BASE_DIR, SESSIONS_DIR, EXPORTS_DIR, LOGS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# ========== ЛОГГИНГ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | SWILL PRO | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Аккаунты
    c.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            session_file TEXT NOT NULL,
            proxy TEXT,
            status TEXT DEFAULT 'active',
            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP,
            total_invites INTEGER DEFAULT 0,
            total_parses INTEGER DEFAULT 0,
            is_authorized INTEGER DEFAULT 0
        )
    ''')

    # Пользователи (парсинг по сообщениям)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            source_chat TEXT NOT NULL,
            source_msg_id INTEGER,
            parsed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            invited INTEGER DEFAULT 0,
            invite_method TEXT,
            last_attempt TIMESTAMP
        )
    ''')

    # Целевые чаты для инвайтов
    c.execute('''
        CREATE TABLE IF NOT EXISTS target_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE NOT NULL,
            title TEXT,
            type TEXT,
            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            invite_count INTEGER DEFAULT 0
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")


# ========== ЛОГИРОВАНИЕ ДЕЙСТВИЙ ПОЛЬЗОВАТЕЛЕЙ ==========
class UserLogger:
    @staticmethod
    def log_action(user_id: int, username: str, action: str, details: str = ""):
        """Логирование действий пользователей"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] USER_ID: {user_id} | USERNAME: @{username or 'None'} | ACTION: {action}"

        if details:
            log_entry += f" | DETAILS: {details}"

        log_entry += "\n"

        # Записываем в файл
        try:
            with open(USER_LOGS_FILE, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"Ошибка записи лога: {e}")

    @staticmethod
    def export_logs() -> str:
        """Экспорт логов в файл"""
        if not USER_LOGS_FILE.exists():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = EXPORTS_DIR / f"user_logs_{timestamp}.txt"

        try:
            # Копируем файл логов в exports
            with open(USER_LOGS_FILE, 'r', encoding='utf-8') as src:
                content = src.read()

            with open(export_path, 'w', encoding='utf-8') as dst:
                dst.write(f"=== SWILL PARSER PRO - USER ACTIVITY LOGS ===\n")
                dst.write(f"=== Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
                dst.write(content)

            return str(export_path)
        except Exception as e:
            logger.error(f"Ошибка экспорта логов: {e}")
            return None

    @staticmethod
    def get_stats() -> dict:
        """Статистика по логам"""
        if not USER_LOGS_FILE.exists():
            return {'total': 0, 'users': 0}

        try:
            with open(USER_LOGS_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            total = len(lines)
            users = set()

            for line in lines:
                if 'USER_ID:' in line:
                    try:
                        user_id = line.split('USER_ID:')[1].split('|')[0].strip()
                        users.add(user_id)
                    except:
                        pass

            return {'total': total, 'users': len(users)}
        except:
            return {'total': 0, 'users': 0}


# ========== МЕНЕДЖЕР АККАУНТОВ ==========
class AccountManager:
    @staticmethod
    def add(phone: str) -> bool:
        try:
            session_file = SESSIONS_DIR / f"{phone}.session"

            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO accounts 
                (phone, session_file, is_authorized)
                VALUES (?, ?, 0)
            ''', (phone, str(session_file)))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления аккаунта: {e}")
            return False

    @staticmethod
    def get_all():
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT * FROM accounts WHERE status='active'")
        rows = c.fetchall()
        conn.close()

        accounts = []
        for row in rows:
            session_file = row[2]
            proxy = row[3]  # Добавлено поле proxy
            is_authorized = os.path.exists(session_file)
            accounts.append({
                'id': row[0], 'phone': row[1], 'session_file': row[2],
                'proxy': proxy, 'status': row[4], 'added_date': row[5],
                'last_used': row[6], 'total_invites': row[7],
                'total_parses': row[8], 'is_authorized': is_authorized
            })
        return accounts

    @staticmethod
    def get_authorized():
        """Получить только авторизованные аккаунты"""
        accounts = AccountManager.get_all()
        return [acc for acc in accounts if os.path.exists(acc['session_file'])]

    @staticmethod
    def set_proxy(phone: str, proxy: str = None):
        """Установить прокси для аккаунта"""
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('UPDATE accounts SET proxy = ? WHERE phone = ?', (proxy, phone))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка установки прокси: {e}")
            return False

    @staticmethod
    def get_proxy(phone: str) -> str:
        """Получить прокси для аккаунта"""
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT proxy FROM accounts WHERE phone = ?', (phone,))
            result = c.fetchone()
            conn.close()
            return result[0] if result and result[0] else None
        except:
            return None

    @staticmethod
    def update_stats(phone: str, field: str, value: int = 1):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        if field == 'invites':
            c.execute('''
                UPDATE accounts SET 
                total_invites = total_invites + ?,
                last_used = ?
                WHERE phone = ?
            ''', (value, datetime.now(), phone))
        elif field == 'parses':
            c.execute('''
                UPDATE accounts SET 
                total_parses = total_parses + ?,
                last_used = ?
                WHERE phone = ?
            ''', (value, datetime.now(), phone))

        conn.commit()
        conn.close()

    @staticmethod
    def mark_authorized(phone: str, status: bool = True):
        # БОЛЬШЕ НИЧЕГО НЕ ДЕЛАЕМ
        # авторизация определяется по .session файлу
        return


# ========== МЕНЕДЖЕР ПОЛЬЗОВАТЕЛЕЙ ==========
class UserManager:
    @staticmethod
    def add_from_message(user_data: dict):
        """Добавить пользователя из сообщения"""
        try:

            file_index = 1
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()

            # Проверяем нет ли уже такого пользователя из этого чата
            c.execute('''
                SELECT id FROM users 
                WHERE user_id = ? AND source_chat = ?
            ''', (user_data['user_id'], user_data['source_chat']))

            if c.fetchone() is None:
                c.execute('''
                    INSERT INTO users 
                    (user_id, username, first_name, last_name, source_chat, source_msg_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_data['user_id'], user_data['username'],
                    user_data['first_name'], user_data['last_name'],
                    user_data['source_chat'], user_data.get('source_msg_id')
                ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            return False

    @staticmethod
    def get_for_invite(limit: int = 100):
        """Получить пользователей для инвайта"""
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            SELECT user_id, username, first_name, last_name 
            FROM users 
            WHERE invited = 0 
            GROUP BY user_id 
            LIMIT ?
        ''', (limit,))
        rows = c.fetchall()
        conn.close()

        users = []
        for row in rows:
            users.append({
                'user_id': row[0], 'username': row[1],
                'first_name': row[2], 'last_name': row[3]
            })
        return users

    @staticmethod
    def export_to_file_unique(source_chat: str = None) -> str:
        """Экспорт пользователей в файл с уникальным именем (временная метка)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if source_chat:
            # Очищаем имя чата от спецсимволов для имени файла
            safe_chat = re.sub(r'[^\w\s-]', '', source_chat)[:30]
            filename = f"parsed_{safe_chat}_{timestamp}.txt"
        else:
            filename = f"parsed_all_{timestamp}.txt"

        filepath = EXPORTS_DIR / filename

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        if source_chat:
            c.execute('''
                SELECT DISTINCT username, first_name, last_name, user_id
                FROM users 
                WHERE source_chat = ? AND username IS NOT NULL
                ORDER BY parsed_date DESC
            ''', (source_chat,))
        else:
            c.execute('''
                SELECT DISTINCT username, first_name, last_name, user_id
                FROM users 
                WHERE username IS NOT NULL
                ORDER BY parsed_date DESC
            ''')

        rows = c.fetchall()
        conn.close()

        with open(filepath, 'w', encoding='utf-8') as f:
            for row in rows:
                username = row[0] if row[0] else None
                user_id = row[3]

                if username:
                    # Если есть username - пишем @username и ID
                    f.write(f"@{username} | ID: {user_id}\n")
                else:
                    # Если нет username - только ID
                    f.write(f"ID: {user_id}\n")

        return str(filepath)

    @staticmethod
    def mark_invited(user_id: int, method: str = 'direct'):
        """Отметить как приглашенного"""
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            UPDATE users SET 
            invited = 1,
            invite_method = ?,
            last_attempt = ?
            WHERE user_id = ?
        ''', (method, datetime.now(), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def export_to_txt(filename: str = None):
        """Экспорт в TXT файл"""
        users = UserManager.get_for_invite(10000)

        if not users:
            return None

        if not filename:
            filename = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            filepath = EXPORTS_DIR / filename
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"SWILL PARSER PRO - User Database\n")
            f.write(f"Export date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total users: {len(users)}\n")
            f.write("=" * 60 + "\n\n")

            for i, user in enumerate(users, 1):
                f.write(f"[{i}] USER ID: {user['user_id']}\n")
                if user['username']:
                    f.write(f"    Username: @{user['username']}\n")
                if user['first_name'] or user['last_name']:
                    name = f"{user['first_name'] or ''} {user['last_name'] or ''}".strip()
                    f.write(f"    Name: {name}\n")
                f.write("\n")

        return str(filepath)

    @staticmethod
    def stats():
        """Статистика пользователей"""
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM users WHERE invited = 1")
        invited = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT source_chat) FROM users")
        chats = c.fetchone()[0]

        conn.close()

        return {'total': total, 'invited': invited, 'chats': chats}


# ========== МЕНЕДЖЕР ЧАТОВ ==========
class ChatManager:
    @staticmethod
    def add_target(link: str, title: str = None, chat_type: str = 'channel'):
        """Добавить целевой чат"""
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('''
                INSERT OR IGNORE INTO target_chats 
                (link, title, type, added_date)
                VALUES (?, ?, ?, ?)
            ''', (link, title, chat_type, datetime.now()))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    @staticmethod
    def get_targets():
        """Получить все целевые чаты"""
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT * FROM target_chats")
        rows = c.fetchall()
        conn.close()

        chats = []
        for row in rows:
            chats.append({
                'id': row[0], 'link': row[1], 'title': row[2],
                'type': row[3], 'added_date': row[4], 'invite_count': row[5]
            })
        return chats

    @staticmethod
    def update_invite_count(link: str, count: int = 1):
        """Обновить счетчик инвайтов"""
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            UPDATE target_chats SET 
            invite_count = invite_count + ?
            WHERE link = ?
        ''', (count, link))
        conn.commit()
        conn.close()


# ========== СИСТЕМА АВТОРИЗАЦИИ ==========
class AuthManager:
    """Менеджер авторизации с правильной обработкой 2FA и прокси"""

    def __init__(self, phone: str, proxy: str = None):
        self.phone = phone
        self.session_file = SESSIONS_DIR / f"{phone}.session"
        self.proxy = self._parse_proxy(proxy) if proxy else None
        self.client = None
        self.phone_code_hash = None

    def _parse_proxy(self, proxy_str: str):
        """Парсинг прокси строки в формат Telethon"""
        try:
            # Формат: socks5://user:pass@ip:port или http://ip:port
            import re
            from telethon import connection

            # Парсим URL
            match = re.match(r'(socks5|socks4|http)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)', proxy_str)
            if not match:
                return None

            proxy_type_str, username, password, addr, port = match.groups()

            # Определяем тип прокси (Telethon использует строки)
            proxy_type = proxy_type_str.upper()  # 'SOCKS5', 'SOCKS4', 'HTTP'

            return {
                'proxy_type': proxy_type,
                'addr': addr,
                'port': int(port),
                'username': username,
                'password': password,
                'rdns': True
            }
        except:
            return None

    async def connect(self):
        """Подключение к клиенту с прокси"""
        self.client = TelegramClient(
            str(self.session_file),
            API_ID,
            API_HASH,
            proxy=self.proxy
        )
        await self.client.connect()
        return self.client

    async def send_code(self):
        """Отправить код на телефон"""
        if not self.client:
            await self.connect()

        try:
            result = await self.client.send_code_request(self.phone)
            self.phone_code_hash = result.phone_code_hash
            return True, "Код отправлен"
        except Exception as e:
            return False, f"Ошибка отправки кода: {str(e)}"

    async def sign_in(self, code: str):
        """Войти с кодом"""
        try:
            await self.client.sign_in(
                phone=self.phone,
                code=code,
                phone_code_hash=self.phone_code_hash
            )
            return True, "Успешная авторизация"
        except Exception as e:
            error_msg = str(e).lower()
            if any(x in error_msg for x in ['password', 'two-step', '2fa']):
                return 'NEED_PASSWORD', "Требуется пароль 2FA"
            else:
                return False, f"Ошибка входа: {str(e)}"

    async def sign_in_with_password(self, password: str):
        """Войти с паролем 2FA"""
        try:
            await self.client.sign_in(password=password)
            return True, "Успешная авторизация с 2FA"
        except Exception as e:
            return False, f"Ошибка 2FA: {str(e)}"

    async def disconnect(self):
        """Отключиться"""
        if self.client:
            await self.client.disconnect()


# ========== ОСНОВНОЙ БОТ ==========
class SwillParserBot:
    def __init__(self):
        self.bot = None
        self.user_states = {}  # Состояния пользователей
        self.user_data = {}  # Данные пользователей
        self.auth_sessions = {}  # Сессии авторизации
        self.active_tasks = {}  # Активные задачи {user_id: {'type': 'parse'/'invite', 'task': Task}}
        self.task_lock = {}  # Блокировки для предотвращения двойного запуска

    def check_access(self, user_id: int) -> bool:
        """Проверка доступа пользователя"""
        return user_id in AUTHORIZED_USERS

    def has_active_task(self, user_id: int) -> bool:
        """Проверка есть ли активная задача у пользователя"""
        if user_id in self.active_tasks:
            task = self.active_tasks[user_id]['task']
            return not task.done()
        return False

    def get_active_task_info(self, user_id: int) -> str:
        """Получить информацию об активной задаче"""
        if user_id in self.active_tasks:
            task_type = self.active_tasks[user_id]['type']
            if task_type == 'parse':
                return "📊 Парсинг"
            elif task_type == 'invite':
                return "📨 Инвайтинг"
        return None

    async def log_user_action(self, event, action: str, details: str = ""):
        """Логирование действия пользователя"""
        try:
            user = await event.get_sender()
            username = user.username if hasattr(user, 'username') else None
            UserLogger.log_action(event.sender_id, username, action, details)
        except Exception as e:
            logger.error(f"Ошибка логирования: {e}")

    async def start(self):
        """Запуск бота"""
        init_db()

        self.bot = TelegramClient('swill_parser_bot', API_ID, API_HASH)
        await self.bot.start(bot_token=BOT_TOKEN)

        # Регистрация обработчиков
        @self.bot.on(events.NewMessage(pattern='^/(start|menu)$'))
        async def handler(event):
            await self.cmd_start(event)

        @self.bot.on(events.NewMessage(pattern='/add_account'))
        async def handler(event):
            await self.cmd_add_account(event)

        @self.bot.on(events.NewMessage(pattern='/accounts'))
        async def handler(event):
            await self.cmd_accounts(event)

        @self.bot.on(events.NewMessage(pattern='/auth'))
        async def handler(event):
            await self.cmd_auth(event)

        @self.bot.on(events.NewMessage(pattern='/parse'))
        async def handler(event):
            await self.cmd_parse(event)

        @self.bot.on(events.NewMessage(pattern='/invite'))
        async def handler(event):
            await self.cmd_invite(event)

        @self.bot.on(events.NewMessage(pattern='/export'))
        async def handler(event):
            await self.cmd_export(event)

        @self.bot.on(events.NewMessage(pattern='/stats'))
        async def handler(event):
            await self.cmd_stats(event)

        @self.bot.on(events.NewMessage(pattern='/add_chat'))
        async def handler(event):
            await self.cmd_add_chat(event)

        @self.bot.on(events.CallbackQuery)
        async def handler(event):
            await self.handle_callback(event)

        @self.bot.on(events.NewMessage)
        async def handler(event):
            await self.handle_message(event)

        @self.bot.on(events.NewMessage(pattern='^/help$'))
        async def handler(event):
            await event.respond(
                "🆘 **Помощь**\n\n"
                "Команды:\n"
                "• /start — запуск бота\n"
                "• /menu — главное меню\n"
                "• /help — помощь\n\n"
                "Остальное управление — через кнопки 👇"
            )

        logger.info(f"🤖 SWILL Parser Bot запущен! @{(await self.bot.get_me()).username}")
        await self.send_log("🚀 SWILL Parser PRO активирован")

        await self.bot.run_until_disconnected()

    async def send_log(self, message: str):
        """Отправить лог в чат"""
        try:
            await self.bot.send_message(LOG_CHAT, f"📊 {message}")
        except:
            pass

    # ========== КОМАНДЫ ==========
    async def cmd_start(self, event):
        """Главное меню"""
        # Проверка авторизации
        if not self.check_access(event.sender_id):
            # Запрашиваем пароль
            self.user_states[event.sender_id] = 'waiting_password'
            await event.respond(
                "🔐 **Введите пароль для доступа к боту:**"
            )
            return

        await self.log_user_action(event, "START", "Открыл главное меню")

        buttons = [
            [Button.inline("📱 Управление аккаунтами", b"accounts_menu")],
            [Button.inline("🔍 Парсинг по сообщениям", b"parse_menu")],
            [Button.inline("📨 Массовый инвайтинг", b"invite_menu")],
            [Button.inline("📤 Экспорт базы", b"export_menu")],
            [Button.inline("📊 Статистика", b"stats_menu")]
        ]

        await event.respond(
            "🚀 **SWILL PARSER PRO 2026**\n\n"
            "**Функционал:**\n"
            "• Парсинг пользователей по сообщениям\n"
            "• Массовый инвайтинг\n"
            "• Авторизация аккаунтов через бота\n"
            "• Экспорт в TXT\n\n"
            "Выберите действие:",
            buttons=buttons
        )

    async def cmd_add_account(self, event):
        """Добавить аккаунт"""
        self.user_states[event.sender_id] = 'add_account'
        await event.respond(
            "📱 **Добавление аккаунта**\n\n"
            "Введите номер телефона:\n"
            "Формат: +79991234567\n\n"
            "Для отмены: /cancel"
        )

    async def cmd_accounts(self, event):
        """Список аккаунтов"""
        accounts = AccountManager.get_all()

        if not accounts:
            await event.respond("❌ Нет добавленных аккаунтов")
            return

        text = "📋 **Аккаунты:**\n\n"
        for acc in accounts:
            auth_status = "✅" if acc['is_authorized'] else "❌"
            proxy_status = f"🌐 {acc.get('proxy', 'нет')[:20]}..." if acc.get('proxy') else "⚪️ без прокси"
            text += f"{auth_status} **{acc['phone']}** {proxy_status}\n"
            text += f"   📨 {acc['total_invites']} | 🔍 {acc['total_parses']}\n\n"

        buttons = [
            [Button.inline("➕ Добавить аккаунт", b"add_account"),
             Button.inline("🔐 Авторизовать", b"auth_menu")],
            [Button.inline("🌐 Добавить прокси", b"proxy_menu")]
        ]

        await event.respond(text, buttons=buttons, parse_mode='markdown')

    async def cmd_auth(self, event):
        """Авторизация аккаунта"""
        accounts = AccountManager.get_all()

        # Только неавторизованные
        unauth_accounts = [acc for acc in accounts if not acc['is_authorized']]

        if not unauth_accounts:
            await event.respond("✅ Все аккаунты авторизованы")
            return

        buttons = []
        for acc in unauth_accounts[:5]:
            buttons.append([Button.inline(f"🔐 {acc['phone']}", f"auth_start:{acc['phone']}")])

        buttons.append([Button.inline("◀️ Назад", b"accounts_menu")])

        await event.respond(
            "🔐 **Авторизация аккаунта**\n\n"
            "Выберите аккаунт для авторизации:",
            buttons=buttons
        )

    async def cmd_parse(self, event):
        """Парсинг по сообщениям"""
        accounts = AccountManager.get_authorized()

        if not accounts:
            await event.respond("❌ Нет авторизованных аккаунтов\nИспользуйте /auth")
            return

        self.user_states[event.sender_id] = 'parse_source'
        await event.respond(
            "🔍 **Парсинг по сообщениям**\n\n"
            "Введите ссылку на чат/канал/пост:\n"
            "Примеры:\n"
            "• @durov (канал)\n"
            "• https://t.me/durov/123 (конкретный пост)\n"
            "• @telegram (комментарии)\n\n"
            "Для отмены: /cancel"
        )

    async def cmd_invite(self, event):
        """Инвайтинг из файла"""
        accounts = AccountManager.get_authorized()

        if not accounts:
            await event.respond("❌ Нет авторизованных аккаунтов")
            return

        # Если только один аккаунт - используем его сразу
        if len(accounts) == 1:
            self.user_states[event.sender_id] = 'invite_file'
            self.user_data[event.sender_id] = {'account_phone': accounts[0]['phone']}
            await event.respond(
                "📨 **Массовый инвайтинг**\n\n"
                "Отправьте файл с пользователями (.txt)\n"
                "Формат файла:\n"
                "```\n"
                "@username1 | ID: 123456789\n"
                "@username2 | ID: 987654321\n"
                "ID: 555555555\n"
                "```\n\n"
                "Для отмены: /cancel",
                parse_mode='markdown'
            )
            return

        # Если несколько - выбираем аккаунт
        self.user_states[event.sender_id] = 'select_account_for_invite'
        self.user_data[event.sender_id] = {}

        buttons = []
        for acc in accounts:
            button_text = f"📱 {acc['phone']}"
            buttons.append([Button.inline(button_text, f"invite_acc_{acc['phone']}".encode())])

        buttons.append([Button.inline("◀️ Назад", b"menu")])

        await event.respond(
            "📱 **Выберите аккаунт для инвайтинга:**",
            buttons=buttons,
            parse_mode='markdown'
        )

    async def cmd_export(self, event):
        """Экспорт базы с выбором источника"""
        # Получаем список уникальных source_chat
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            SELECT DISTINCT source_chat, COUNT(*) as cnt, MAX(parsed_date) as last_parse
            FROM users 
            GROUP BY source_chat
            ORDER BY last_parse DESC
        ''')
        sources = c.fetchall()
        conn.close()

        if not sources:
            await event.respond("❌ База пользователей пуста")
            return

        # Кнопки для выбора источника
        buttons = []
        buttons.append([Button.inline("📦 Экспорт всех пользователей", b"export_all")])

        for source_chat, count, last_parse in sources[:5]:  # Показываем последние 5
            label = f"{source_chat[:25]}... ({count} юз.)"
            data = f"export_source:{source_chat}"
            buttons.append([Button.inline(label, data.encode())])

        await event.respond(
            "📤 **Выберите что экспортировать:**\n\n"
            "Каждый экспорт создаст новый файл с временной меткой",
            buttons=buttons
        )

    async def cmd_stats(self, event):
        """Статистика"""
        user_stats = UserManager.stats()
        accounts = AccountManager.get_all()
        auth_accounts = AccountManager.get_authorized()
        chats = ChatManager.get_targets()

        # Подсчет активных задач
        active_tasks_count = sum(1 for uid, task_data in self.active_tasks.items() if not task_data['task'].done())
        active_users = len([uid for uid, task_data in self.active_tasks.items() if not task_data['task'].done()])

        text = (
            f"📊 **Статистика SWILL PARSER**\n\n"
            f"**👥 Пользователи:**\n"
            f"• Всего в базе: {user_stats['total']}\n"
            f"• Приглашено: {user_stats['invited']}\n"
            f"• Уникальных чатов: {user_stats['chats']}\n\n"
            f"**📱 Аккаунты:**\n"
            f"• Всего: {len(accounts)}\n"
            f"• Авторизовано: {len(auth_accounts)}\n\n"
            f"**🎯 Целевые чаты:**\n"
            f"• Всего: {len(chats)}\n\n"
            f"**⚡️ Активные задачи:**\n"
            f"• Пользователей работают: {active_users}\n"
            f"• Задач выполняется: {active_tasks_count}"
        )

        # Показываем активную задачу текущего пользователя
        if self.has_active_task(event.sender_id):
            task_info = self.get_active_task_info(event.sender_id)
            text += f"\n\n**🔄 Ваша задача:** {task_info}"

        await event.respond(text, parse_mode='markdown')

    async def cmd_add_chat(self, event):
        """Добавить целевой чат"""
        self.user_states[event.sender_id] = 'add_target_chat'
        await event.respond(
            "🎯 **Добавление целевого чата**\n\n"
            "Введите ссылку на чат для инвайтов:\n"
            "Пример: @my_chat или https://t.me/my_chat\n\n"
            "Для отмены: /cancel"
        )

    # ========== CALLBACK ОБРАБОТЧИКИ ==========
    async def handle_callback(self, event):
        data = event.data.decode('utf-8')

        # Проверка доступа
        if not self.check_access(event.sender_id):
            await event.answer("❌ Нет доступа", alert=True)
            return

        if data == 'menu':
            await self.cmd_start(event)
        elif data == 'accounts_menu':
            await self.log_user_action(event, "ACCOUNTS_MENU", "Открыл меню аккаунтов")
            await self.cmd_accounts(event)
        elif data == 'parse_menu':
            await self.log_user_action(event, "PARSE_MENU", "Открыл меню парсинга")
            await self.cmd_parse(event)
        elif data == 'invite_menu':
            await self.log_user_action(event, "INVITE_MENU", "Открыл меню инвайтинга")
            await self.cmd_invite(event)
        elif data == 'export_menu':
            await self.log_user_action(event, "EXPORT_MENU", "Открыл меню экспорта")
            await self.cmd_export(event)
        elif data == 'stats_menu':
            await self.log_user_action(event, "STATS_MENU", "Открыл статистику")
            await self.cmd_stats(event)
        elif data == 'add_account':
            await self.log_user_action(event, "ADD_ACCOUNT", "Начал добавление аккаунта")
            await self.cmd_add_account(event)
        elif data == 'auth_menu':
            await self.log_user_action(event, "AUTH_MENU", "Открыл меню авторизации")
            await self.cmd_auth(event)

        elif data == 'proxy_menu':
            await self.log_user_action(event, "PROXY_MENU", "Открыл меню прокси")
            # Показываем список аккаунтов для выбора
            accounts = AccountManager.get_all()

            if not accounts:
                await event.respond("❌ Нет аккаунтов")
                return

            buttons = []
            for acc in accounts:
                proxy_info = "🌐" if acc.get('proxy') else "⚪️"
                buttons.append([Button.inline(f"{proxy_info} {acc['phone']}", f"proxy_select:{acc['phone']}".encode())])

            buttons.append([Button.inline("◀️ Назад", b"accounts_menu")])

            await event.respond(
                "🌐 **Управление прокси**\n\n"
                "Выберите аккаунт:\n"
                "🌐 - прокси установлен\n"
                "⚪️ - без прокси",
                buttons=buttons,
                parse_mode='markdown'
            )

        # Обработка экспорта
        elif data == 'export_all':
            await self.log_user_action(event, "EXPORT_ALL", "Экспорт всех пользователей")
            await event.respond("📦 Экспортирую всех пользователей...")
            filepath = UserManager.export_to_file_unique()
            if filepath:
                try:
                    await event.respond(
                        f"✅ Экспорт завершён!\n\n"
                        f"Файл: {Path(filepath).name}",
                        file=filepath
                    )
                    await self.send_log(f"📤 Экспорт всех: {Path(filepath).name}")
                except Exception as e:
                    await event.respond(f"❌ Ошибка отправки: {e}")
            else:
                await event.respond("❌ База пуста")

        elif data.startswith('export_source:'):
            source_chat = data.replace('export_source:', '')
            await self.log_user_action(event, "EXPORT_SOURCE", f"Экспорт из {source_chat[:30]}")
            await event.respond(f"📦 Экспортирую из {source_chat[:30]}...")
            filepath = UserManager.export_to_file_unique(source_chat)
            if filepath:
                try:
                    await event.respond(
                        f"✅ Экспорт завершён!\n\n"
                        f"Источник: {source_chat[:40]}\n"
                        f"Файл: {Path(filepath).name}",
                        file=filepath
                    )
                    await self.send_log(f"📤 Экспорт из {source_chat[:20]}: {Path(filepath).name}")
                except Exception as e:
                    await event.respond(f"❌ Ошибка отправки: {e}")
            else:
                await event.respond("❌ Нет данных для экспорта")

        elif data.startswith('auth_start:'):
            phone = data.split(':')[1]
            await self.log_user_action(event, "AUTH_START", f"Начал авторизацию {phone}")

            # Сразу отправляем код БЕЗ запроса прокси
            self.user_states[event.sender_id] = f'auth_code:{phone}'
            self.auth_sessions[event.sender_id] = {'phone': phone}

            await event.respond(
                f"🔐 **Авторизация:** {phone}\n\n"
                f"Отправляю код... Подождите.",
                parse_mode='markdown'
            )

            # Запускаем отправку кода
            asyncio.create_task(self.process_auth_start(event.sender_id, phone, proxy=None))

        # Пропуск прокси (старый обработчик - оставляем на всякий случай)
        elif data == 'skip_proxy':
            if event.sender_id in self.auth_sessions:
                phone = self.auth_sessions[event.sender_id]['phone']
                self.user_states[event.sender_id] = f'auth_code:{phone}'

                await event.respond("✅ Прокси пропущен, отправляю код...")

                # Запускаем отправку кода без прокси
                asyncio.create_task(self.process_auth_start(event.sender_id, phone, proxy=None))

        # Обработчики прокси ПОСЛЕ авторизации
        elif data.startswith('add_proxy:'):
            phone = data.replace('add_proxy:', '')
            self.user_states[event.sender_id] = f'add_proxy_after:{phone}'
            self.auth_sessions[event.sender_id] = {'phone': phone}

            await event.respond(
                f"🌐 **Добавление прокси для {phone}**\n\n"
                f"Введите прокси:\n\n"
                f"Формат:\n"
                f"• `socks5://user:pass@ip:port`\n"
                f"• `http://user:pass@ip:port`\n"
                f"• `socks5://ip:port`\n\n"
                f"Для отмены: /cancel",
                parse_mode='markdown'
            )

        elif data.startswith('skip_proxy_after:'):
            phone = data.replace('skip_proxy_after:', '')
            await event.respond(
                f"✅ **Аккаунт {phone} готов к работе!**\n\n"
                f"Прокси не добавлен. Можно начинать парсинг и инвайтинг.",
                parse_mode='markdown'
            )
            # Очистка
            if event.sender_id in self.user_states:
                del self.user_states[event.sender_id]
            if event.sender_id in self.auth_sessions:
                del self.auth_sessions[event.sender_id]

        # Выбор аккаунта для прокси из меню
        elif data.startswith('proxy_select:'):
            phone = data.replace('proxy_select:', '')
            # Показываем выбор типа прокси
            buttons = [
                [Button.inline("🔵 SOCKS5", f"proxy_type:socks5:{phone}".encode())],
                [Button.inline("🔴 SOCKS4", f"proxy_type:socks4:{phone}".encode())],
                [Button.inline("🟢 HTTP", f"proxy_type:http:{phone}".encode())],
                [Button.inline("🟡 HTTPS", f"proxy_type:https:{phone}".encode())],
                [Button.inline("❌ Удалить прокси", f"proxy_delete:{phone}".encode())],
                [Button.inline("◀️ Назад", b"proxy_menu")]
            ]

            current_proxy = AccountManager.get_proxy(phone)
            proxy_info = f"\n🌐 Текущий: `{current_proxy[:40]}...`" if current_proxy else "\n⚪️ Прокси не установлен"

            await event.respond(
                f"🌐 **Прокси для {phone}**{proxy_info}\n\n"
                f"Выберите тип прокси:",
                buttons=buttons,
                parse_mode='markdown'
            )

        # Выбор типа прокси
        elif data.startswith('proxy_type:'):
            parts = data.replace('proxy_type:', '').split(':')
            proxy_type = parts[0]  # socks5, socks4, http, https
            phone = parts[1]

            # Сохраняем в сессию
            self.user_states[event.sender_id] = f'proxy_input:{proxy_type}:{phone}'

            # Определяем порт по умолчанию
            default_port = {
                'socks5': '1080',
                'socks4': '1080',
                'http': '8080',
                'https': '443'
            }.get(proxy_type, '1080')

            await event.respond(
                f"🌐 **Тип: {proxy_type.upper()}**\n\n"
                f"Введите данные прокси в одном из форматов:\n\n"
                f"**С авторизацией:**\n"
                f"`{proxy_type}://user:pass@ip:port`\n"
                f"Пример: `{proxy_type}://myuser:mypass@192.168.1.1:{default_port}`\n\n"
                f"**Без авторизации:**\n"
                f"`{proxy_type}://ip:port`\n"
                f"Пример: `{proxy_type}://192.168.1.1:{default_port}`\n\n"
                f"Или просто IP и порт:\n"
                f"`192.168.1.1:{default_port}`\n\n"
                f"Для отмены: /cancel",
                parse_mode='markdown'
            )

        # Удаление прокси
        elif data.startswith('proxy_delete:'):
            phone = data.replace('proxy_delete:', '')
            if AccountManager.set_proxy(phone, None):
                await event.respond(
                    f"✅ **Прокси удален для {phone}**\n\n"
                    f"Аккаунт теперь работает без прокси.",
                    parse_mode='markdown'
                )
            else:
                await event.respond("❌ Ошибка удаления прокси")

        # Обработчики режимов парсинга
        elif data == 'parse_comments':
            # Парсинг комментариев под постом
            if event.sender_id in self.user_data and 'message_id' in self.user_data[event.sender_id]:
                self.user_data[event.sender_id]['parse_mode'] = 'comments'
                self.user_states[event.sender_id] = 'parse_limit'

                await event.respond(
                    f"💬 **Режим: Парсинг комментариев**\n\n"
                    f"Сколько комментариев обработать?\n"
                    f"Рекомендуется: 500-2000\n"
                    f"Допустимо: 50-10000\n\n"
                    f"Для отмены: /cancel",
                    parse_mode='markdown'
                )

        elif data == 'parse_from_msg':
            # Парсинг с конкретного сообщения
            if event.sender_id in self.user_data and 'message_id' in self.user_data[event.sender_id]:
                self.user_data[event.sender_id]['parse_mode'] = 'from_message'
                self.user_states[event.sender_id] = 'parse_limit'

                await event.respond(
                    f"📨 **Режим: С конкретного сообщения**\n\n"
                    f"Сколько сообщений обработать начиная с указанного?\n"
                    f"Рекомендуется: 500-2000\n"
                    f"Допустимо: 50-10000\n\n"
                    f"Для отмены: /cancel",
                    parse_mode='markdown'
                )

        # Обработчик выбора аккаунта для инвайтинга
        elif data.startswith('invite_acc_'):
            phone = data.replace('invite_acc_', '')
            await self.log_user_action(event, "SELECT_INVITE_ACCOUNT", f"Выбран аккаунт {phone}")
            self.user_states[event.sender_id] = 'invite_file'
            if event.sender_id not in self.user_data:
                self.user_data[event.sender_id] = {}
            self.user_data[event.sender_id]['account_phone'] = phone
            await event.respond(
                f"✅ Выбран аккаунт: {phone}\n\n"
                "📨 **Массовый инвайтинг**\n\n"
                "Отправьте файл с пользователями (.txt)\n"
                "Формат файла:\n"
                "```\n"
                "@username1 | ID: 123456789\n"
                "@username2 | ID: 987654321\n"
                "ID: 555555555\n"
                "```\n\n"
                "Для отмены: /cancel",
                parse_mode='markdown'
            )

        await event.answer()

    # ========== АВТОРИЗАЦИЯ ==========
    async def process_auth_start(self, user_id: int, phone: str, proxy: str = None):
        """Начало авторизации с поддержкой прокси"""
        try:
            auth = AuthManager(phone, proxy=proxy)
            await auth.connect()

            success, message = await auth.send_code()

            if success:
                self.auth_sessions[user_id]['auth_manager'] = auth
                self.auth_sessions[user_id]['phone_code_hash'] = auth.phone_code_hash

                proxy_info = f"\n🌐 Прокси: {proxy[:30]}..." if proxy else "\n🌐 Прокси: не используется"

                await self.bot.send_message(
                    user_id,
                    f"✅ Код отправлен на {phone}\n"
                    f"{proxy_info}\n\n"
                    f"Проверьте Telegram и введите код:"
                )
            else:
                await self.bot.send_message(user_id, f"❌ {message}")
                if user_id in self.user_states:
                    del self.user_states[user_id]
                if user_id in self.auth_sessions:
                    del self.auth_sessions[user_id]

        except Exception as e:
            await self.bot.send_message(user_id, f"❌ Ошибка: {str(e)}")
            if user_id in self.user_states:
                del self.user_states[user_id]
            if user_id in self.auth_sessions:
                del self.auth_sessions[user_id]

    async def process_auth_code(self, user_id: int, code: str):
        """Обработка кода авторизации"""
        if user_id not in self.auth_sessions:
            await self.bot.send_message(user_id, "❌ Сессия авторизации утеряна")
            return

        data = self.auth_sessions[user_id]
        phone = data['phone']
        auth = data.get('auth_manager')

        if not auth:
            await self.bot.send_message(user_id, "❌ Ошибка: менеджер авторизации не найден")
            return

        try:
            result, message = await auth.sign_in(code)

            if result == True:
                # Успешная авторизация
                AccountManager.mark_authorized(phone, True)

                # Показываем кнопки для прокси
                buttons = [
                    [Button.inline("🌐 Добавить прокси", f"add_proxy:{phone}".encode())],
                    [Button.inline("⏭ Не добавлять прокси", f"skip_proxy_after:{phone}".encode())]
                ]

                await self.bot.send_message(
                    user_id,
                    f"✅ **Аккаунт {phone} авторизован!**\n\n"
                    f"Хотите добавить прокси для этого аккаунта?",
                    buttons=buttons,
                    parse_mode='markdown'
                )
                await self.send_log(f"✅ Авторизован: {phone}")

                # НЕ очищаем состояние - ждем выбора по прокси
                return

            elif result == 'NEED_PASSWORD':
                # Нужен пароль 2FA
                self.user_states[user_id] = f'auth_password:{phone}'
                self.auth_sessions[user_id]['code'] = code
                await self.bot.send_message(
                    user_id,
                    "🔐 **Требуется пароль 2FA**\n\n"
                    "Введите пароль от аккаунта:"
                )
                return
            else:
                # Ошибка
                await self.bot.send_message(user_id, f"❌ {message}")

        except Exception as e:
            await self.bot.send_message(user_id, f"❌ Ошибка: {str(e)}")

        finally:
            # Очистка только при ошибке
            if auth:
                await auth.disconnect()
            # Оставляем states для прокси если авторизация успешна
            if user_id in self.user_states and not self.user_states[user_id].startswith('auth_'):
                if user_id in self.user_states:
                    del self.user_states[user_id]
                if user_id in self.auth_sessions:
                    del self.auth_sessions[user_id]

    async def process_auth_password(self, user_id: int, password: str):
        """Обработка пароля 2FA"""
        if user_id not in self.auth_sessions:
            await self.bot.send_message(user_id, "❌ Сессия авторизации утеряна")
            return

        data = self.auth_sessions[user_id]
        phone = data['phone']
        code = data.get('code')

        # Создаем нового менеджера
        auth = AuthManager(phone)
        await auth.connect()

        try:
            # Сначала пытаемся войти с кодом, чтобы получить хэш
            auth_result = await auth.send_code()
            if not auth_result[0]:
                await self.bot.send_message(user_id, f"❌ Ошибка: {auth_result[1]}")
                return

            # Теперь пробуем войти с паролем
            success, message = await auth.sign_in_with_password(password)

            if success:
                AccountManager.mark_authorized(phone, True)

                # Показываем кнопки для прокси
                buttons = [
                    [Button.inline("🌐 Добавить прокси", f"add_proxy:{phone}".encode())],
                    [Button.inline("⏭ Не добавлять прокси", f"skip_proxy_after:{phone}".encode())]
                ]

                await self.bot.send_message(
                    user_id,
                    f"✅ **Аккаунт {phone} авторизован с 2FA!**\n\n"
                    f"Хотите добавить прокси для этого аккаунта?",
                    buttons=buttons,
                    parse_mode='markdown'
                )
                await self.send_log(f"✅ Авторизован с 2FA: {phone}")
                return
            else:
                await self.bot.send_message(user_id, f"❌ {message}")

        except Exception as e:
            await self.bot.send_message(user_id, f"❌ Ошибка 2FA: {str(e)}")

        finally:
            if auth:
                await auth.disconnect()
            if user_id in self.user_states:
                del self.user_states[user_id]
            if user_id in self.auth_sessions:
                del self.auth_sessions[user_id]

    # ========== ОБРАБОТКА СООБЩЕНИЙ ==========
    async def handle_message(self, event):
        if event.text and event.text.startswith('/'):
            return

        user_id = event.sender_id
        state = self.user_states.get(user_id)

        if event.text and event.text.lower() == '/cancel':
            await self.cancel_action(user_id, event)
            return

        # АВТОРИЗАЦИЯ ПО ПАРОЛЮ
        if state == 'waiting_password':
            password = event.text.strip()

            if password == ACCESS_PASSWORD:
                AUTHORIZED_USERS.add(user_id)
                del self.user_states[user_id]

                await event.respond(
                    "✅ **Авторизация успешна!**\n\n"
                    "Добро пожаловать в SWILL PARSER PRO"
                )

                # Показываем главное меню
                await self.cmd_start(event)
            else:
                await event.respond(
                    "❌ **Неверный пароль!**\n\n"
                    "Попробуйте еще раз или введите /start для повторной попытки"
                )
            return

        # ДОБАВЛЕНИЕ ПРОКСИ ПОСЛЕ АВТОРИЗАЦИИ
        elif state and state.startswith('add_proxy_after:'):
            phone = state.replace('add_proxy_after:', '')
            proxy_text = event.text.strip()

            # Сохраняем прокси
            if AccountManager.set_proxy(phone, proxy_text):
                await event.respond(
                    f"✅ **Прокси добавлен для {phone}!**\n\n"
                    f"Аккаунт готов к работе.\n"
                    f"Прокси: {proxy_text[:30]}...",
                    parse_mode='markdown'
                )
            else:
                await event.respond("❌ Ошибка сохранения прокси")

            # Очистка
            if user_id in self.user_states:
                del self.user_states[user_id]
            if user_id in self.auth_sessions:
                del self.auth_sessions[user_id]
            return

        # ДОБАВЛЕНИЕ ПРОКСИ ИЗ МЕНЮ
        elif state and state.startswith('proxy_input:'):
            parts = state.replace('proxy_input:', '').split(':')
            proxy_type = parts[0]  # socks5, socks4, http, https
            phone = parts[1]
            proxy_input = event.text.strip()

            # Форматируем прокси
            if '://' not in proxy_input:
                # Пользователь ввел только IP:PORT или IP:PORT:USER:PASS
                if proxy_input.count(':') == 1:
                    # Формат: IP:PORT
                    proxy_text = f"{proxy_type}://{proxy_input}"
                elif proxy_input.count(':') >= 3:
                    # Формат: IP:PORT:USER:PASS
                    parts_input = proxy_input.split(':')
                    ip = parts_input[0]
                    port = parts_input[1]
                    user = parts_input[2]
                    password = ':'.join(parts_input[3:])  # На случай если : в пароле
                    proxy_text = f"{proxy_type}://{user}:{password}@{ip}:{port}"
                else:
                    await event.respond("❌ Неверный формат. Попробуйте еще раз или /cancel")
                    return
            else:
                # Пользователь ввел полный URL
                proxy_text = proxy_input

            # Сохраняем прокси
            if AccountManager.set_proxy(phone, proxy_text):
                await event.respond(
                    f"✅ **Прокси добавлен для {phone}!**\n\n"
                    f"Тип: {proxy_type.upper()}\n"
                    f"Прокси: `{proxy_text}`\n\n"
                    f"Аккаунт готов к работе!",
                    parse_mode='markdown'
                )
            else:
                await event.respond("❌ Ошибка сохранения прокси")

            # Очистка
            if user_id in self.user_states:
                del self.user_states[user_id]
            return

        # ИНВАЙТ: ЗАГРУЗКА ФАЙЛА
        if state == 'invite_file':
            if event.document:
                # Скачиваем файл
                file_path = await event.download_media(file=EXPORTS_DIR)

                if not file_path:
                    await event.respond("❌ Ошибка загрузки файла")
                    return

                await self.log_user_action(event, "INVITE_FILE_UPLOAD", f"Загрузил файл: {Path(file_path).name}")

                # Подсчитываем количество пользователей в файле
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = [line.strip() for line in f if line.strip()]
                    total_users = len(lines)
                except:
                    total_users = 0

                if total_users == 0:
                    await event.respond("❌ Файл пустой или некорректный")
                    return

                self.user_data[user_id]['file_path'] = file_path
                self.user_data[user_id]['total_in_file'] = total_users
                self.user_states[user_id] = 'invite_count'

                await event.respond(
                    f"✅ Файл загружен!\n\n"
                    f"📊 **В файле найдено:** {total_users} пользователей\n\n"
                    f"💬 **Сколько пользователей заинвайтить?**\n"
                    f"Введите число от 1 до {total_users}\n\n"
                    f"Или введите `all` чтобы заинвайтить всех\n\n"
                    f"Для отмены: /cancel",
                    parse_mode='markdown'
                )
            else:
                await event.respond("❌ Отправьте файл .txt с пользователями")
            return

        # ИНВАЙТ: КОЛИЧЕСТВО ПОЛЬЗОВАТЕЛЕЙ
        elif state == 'invite_count':
            count_text = event.text.strip().lower()
            total_in_file = self.user_data[user_id].get('total_in_file', 0)

            if count_text == 'all':
                invite_count = total_in_file
            else:
                try:
                    invite_count = int(count_text)
                    if invite_count < 1 or invite_count > total_in_file:
                        await event.respond(
                            f"❌ Число должно быть от 1 до {total_in_file}\n"
                            f"Попробуйте еще раз:"
                        )
                        return
                except ValueError:
                    await event.respond("❌ Введите число или 'all'")
                    return

            self.user_data[user_id]['invite_count'] = invite_count
            self.user_states[user_id] = 'invite_target'

            await event.respond(
                f"✅ Будет заинвайчено: **{invite_count}** из {total_in_file}\n\n"
                f"Теперь введите ссылку на чат, куда инвайтить:\n"
                f"Примеры:\n"
                f"• @chatusername\n"
                f"• https://t.me/chatusername\n"
                f"• https://t.me/+InviteHash\n\n"
                f"Для отмены: /cancel",
                parse_mode='markdown'
            )
            return

        # ДОБАВЛЕНИЕ АККАУНТА
        if state == 'add_account':
            phone = event.text.strip()

            # Проверка формата номера без regex
            if not phone.startswith('+'):
                phone = '+' + phone

            # Убираем все кроме цифр и +
            clean_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

            # Проверка: должен начинаться с + и иметь 10-15 цифр
            if not clean_phone.startswith('+') or len(clean_phone) < 11 or len(clean_phone) > 16:
                await event.respond("❌ Неверный формат номера. Используйте: +79991234567")
                return

            phone = clean_phone

            if AccountManager.add(phone):
                await self.log_user_action(event, "ACCOUNT_ADDED", f"Добавил аккаунт: {phone}")
                await event.respond(
                    f"✅ Аккаунт {phone} добавлен!\n\n"
                    f"Теперь авторизуйте его через:\n"
                    f"/auth → выберите аккаунт → введите код"
                )
                await self.send_log(f"📱 Добавлен аккаунт: {phone}")
            else:
                await event.respond("❌ Ошибка добавления. Возможно уже существует.")

            del self.user_states[user_id]

        # АВТОРИЗАЦИЯ: КОД
        elif state and state.startswith('auth_code:'):
            code = event.text.strip()

            if len(code) != 5 or not code.isdigit():
                await event.respond("❌ Код должен быть 5 цифр. Попробуйте еще раз:")
                return

            await self.process_auth_code(user_id, code)

        # АВТОРИЗАЦИЯ: ПАРОЛЬ 2FA
        elif state and state.startswith('auth_password:'):
            password = event.text.strip()
            await self.process_auth_password(user_id, password)

        # ДОБАВЛЕНИЕ ЧАТА
        elif state == 'add_target_chat':
            chat_link = event.text.strip()

            if ChatManager.add_target(chat_link):
                await event.respond(f"✅ Чат добавлен: {chat_link}")
                await self.send_log(f"🎯 Добавлен чат: {chat_link}")
            else:
                await event.respond("❌ Ошибка добавления")

            del self.user_states[user_id]

        # ПАРСИНГ: ИСТОЧНИК
        elif state == 'parse_source':
            source_link = event.text.strip()

            # Анализируем ссылку - есть ли в ней номер сообщения
            import re
            message_id_match = re.search(r'/(\d+)/?$', source_link)

            if message_id_match:
                # Это ссылка на конкретное сообщение
                message_id = int(message_id_match.group(1))

                # Сохраняем данные
                self.user_data[user_id] = {
                    'source': source_link,
                    'message_id': message_id
                }
                self.user_states[user_id] = 'parse_mode'

                # Предлагаем выбрать режим
                buttons = [
                    [Button.inline("💬 Парсить комментарии под постом", b"parse_comments")],
                    [Button.inline("📨 Парсить с этого сообщения далее", b"parse_from_msg")],
                    [Button.inline("◀️ Назад", b"menu")]
                ]

                await event.respond(
                    f"🔗 **Обнаружена ссылка на сообщение!**\n\n"
                    f"Источник: {source_link}\n"
                    f"ID сообщения: {message_id}\n\n"
                    f"**Выберите режим парсинга:**\n"
                    f"• 💬 Комментарии - парсит только комментарии под этим постом\n"
                    f"• 📨 С этого сообщения - парсит начиная с этого сообщения и далее",
                    buttons=buttons,
                    parse_mode='markdown'
                )
            else:
                # Обычная ссылка на канал/чат
                self.user_states[user_id] = 'parse_limit'
                self.user_data[user_id] = {'source': source_link}

                await event.respond(
                    f"🔗 Источник: {source_link}\n\n"
                    f"Сколько сообщений обработать?\n"
                    f"Рекомендуется: 500-2000\n"
                    f"Допустимо: 50-10000\n\n"
                    f"Для отмены: /cancel"
                )

        # ПАРСИНГ: ЛИМИТ
        elif state == 'parse_limit':
            try:
                limit = int(event.text.strip())

                # Получаем режим парсинга
                parse_mode = self.user_data[user_id].get('parse_mode')

                # Минимум 50 для всех режимов
                min_limit = 50

                if limit < min_limit:
                    await event.respond(f"❌ Минимум {min_limit} сообщений")
                    return

                if limit > 10000:
                    await event.respond("❌ Максимум 10000 сообщений")
                    return

                source_link = self.user_data[user_id]['source']
                selected_phone = self.user_data[user_id].get('account_phone')
                message_id = self.user_data[user_id].get('message_id')

                # Проверяем нет ли активной задачи
                if self.has_active_task(user_id):
                    await event.respond(
                        f"⚠️ У вас уже запущена задача: {self.get_active_task_info(user_id)}\n\n"
                        f"Дождитесь её завершения или используйте /cancel для отмены"
                    )
                    return

                await event.respond(f"⏳ Запускаю парсинг из {source_link}...")
                await self.log_user_action(event, "PARSE_START", f"Парсинг {limit} сообщений из {source_link}")

                # Выбираем аккаунт
                accounts = AccountManager.get_authorized()
                if not accounts:
                    await event.respond("❌ Нет авторизованных аккаунтов")
                    return

                # Используем выбранный аккаунт или первый доступный
                if selected_phone:
                    account = next((acc for acc in accounts if acc['phone'] == selected_phone), accounts[0])
                else:
                    account = accounts[0]

                # Регистрируем и запускаем парсинг в фоне
                task = asyncio.create_task(
                    self.run_message_parse(user_id, account, source_link, limit, parse_mode, message_id)
                )
                self.active_tasks[user_id] = {'type': 'parse', 'task': task}

            except ValueError:
                await event.respond("❌ Введите число!")
            finally:
                if user_id in self.user_states:
                    del self.user_states[user_id]
                if user_id in self.user_data:
                    del self.user_data[user_id]

        # ИНВАЙТЫ: ЦЕЛЕВОЙ ЧАТ
        elif state == 'invite_target':
            target_chat = event.text.strip()
            self.user_data[user_id]['target'] = target_chat
            self.user_states[user_id] = 'invite_delay'

            await self.log_user_action(event, "INVITE_TARGET", f"Целевой чат: {target_chat}")

            await event.respond(
                f"🎯 Целевой чат: {target_chat}\n\n"
                f"⏱ Укажите задержку между инвайтами:\n\n"
                f"Формат:\n"
                f"• `30` — фиксированная 30 секунд\n"
                f"• `15-60` — случайная от 15 до 60 секунд\n\n"
                f"⚠️ **Важно:**\n"
                f"• Минимум 15 секунд (рекомендуется 20-40)\n"
                f"• Максимум 120 секунд (2 минуты)\n"
                f"• При флуде задержка увеличится автоматически\n\n"
                f"Для отмены: /cancel",
                parse_mode='markdown'
            )

        # ИНВАЙТ: ЗАДЕРЖКА
        elif state == 'invite_delay':
            delay_text = event.text.strip()

            try:
                if '-' in delay_text:
                    # Диапазон
                    parts = delay_text.split('-')
                    delay_min = int(parts[0])
                    delay_max = int(parts[1])

                    if delay_min < 15 or delay_max > 120 or delay_min >= delay_max:
                        await event.respond("❌ Неверный диапазон. Используйте: 15-120 секунд")
                        return

                else:
                    # Фиксированная
                    delay_min = delay_max = int(delay_text)

                    if delay_min < 15 or delay_min > 120:
                        await event.respond("❌ Задержка должна быть от 15 до 120 секунд (2 минуты)")
                        return

                # Получаем данные
                target_chat = self.user_data[user_id]['target']
                file_path = self.user_data[user_id]['file_path']
                selected_phone = self.user_data[user_id].get('account_phone')
                invite_count = self.user_data[user_id].get('invite_count')  # Количество для инвайта

                # Проверяем нет ли активной задачи
                if self.has_active_task(user_id):
                    await event.respond(
                        f"⚠️ У вас уже запущена задача: {self.get_active_task_info(user_id)}\n\n"
                        f"Дождитесь её завершения или используйте /cancel для отмены"
                    )
                    return

                # Выбираем аккаунт
                accounts = AccountManager.get_authorized()
                if not accounts:
                    await event.respond("❌ Нет авторизованных аккаунтов")
                    return

                # Используем выбранный аккаунт или первый доступный
                if selected_phone:
                    account = next((acc for acc in accounts if acc['phone'] == selected_phone), accounts[0])
                else:
                    account = accounts[0]

                await self.log_user_action(
                    event,
                    "INVITE_START",
                    f"Чат: {target_chat}, Задержка: {delay_min}-{delay_max}, Файл: {Path(file_path).name}"
                )

                await event.respond(
                    f"🚀 Запускаю инвайтинг...\n\n"
                    f"Чат: {target_chat}\n"
                    f"Количество: {invite_count if invite_count else 'все'}\n"
                    f"Задержка: {delay_min}-{delay_max} сек\n"
                    f"Аккаунт: {account['phone']}"
                )

                # Регистрируем и запускаем инвайтинг в фоне
                task = asyncio.create_task(
                    self.run_mass_invite(user_id, account, target_chat, file_path, delay_min, delay_max, invite_count)
                )
                self.active_tasks[user_id] = {'type': 'invite', 'task': task}

                # Очищаем состояние
                del self.user_states[user_id]
                del self.user_data[user_id]

            except ValueError:
                await event.respond("❌ Неверный формат. Используйте число или диапазон (например: 10 или 5-30)")

    async def cancel_action(self, user_id: int, event):
        """Отмена действия или активной задачи"""
        cancelled_something = False

        # Отменяем состояния
        if user_id in self.user_states:
            del self.user_states[user_id]
            cancelled_something = True
        if user_id in self.user_data:
            del self.user_data[user_id]
            cancelled_something = True
        if user_id in self.auth_sessions:
            del self.auth_sessions[user_id]
            cancelled_something = True

        # Проверяем активную задачу
        if user_id in self.active_tasks:
            task_info = self.get_active_task_info(user_id)
            task = self.active_tasks[user_id]['task']

            if not task.done():
                task.cancel()
                await event.respond(
                    f"⚠️ **Отменяю активную задачу:** {task_info}\n\n"
                    f"Задача будет остановлена в ближайшее время..."
                )

            del self.active_tasks[user_id]
            cancelled_something = True

        if cancelled_something:
            await event.respond("✅ Действие отменено")
        else:
            await event.respond("ℹ️ Нечего отменять")

    # ========== ФОНОВЫЕ ЗАДАЧИ ==========
    async def run_message_parse(self, user_id: int, account: dict, source_link: str, limit: int,
                                parse_mode: str = None, message_id: int = None):
        """Парсинг пользователей по сообщениям с поддержкой комментариев"""
        seen_users = set()
        messages_processed = 0
        users_found = 0
        unique_users = 0
        bots_skipped = 0
        duplicates_skipped = 0
        start_time = datetime.now()

        try:
            # Получаем прокси для аккаунта
            proxy_str = account.get('proxy')
            proxy = None
            if proxy_str:
                auth_mgr = AuthManager(account['phone'], proxy=proxy_str)
                proxy = auth_mgr.proxy

            client = TelegramClient(account['session_file'], API_ID, API_HASH, proxy=proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await self.bot.send_message(user_id, "❌ Аккаунт не авторизован")
                await client.disconnect()
                return

            # Получаем entity чата/поста
            try:
                # ВАЖНО: Если есть номер сообщения в ссылке - убираем его для get_entity
                clean_link = source_link
                if message_id and parse_mode in ['comments', 'from_message']:
                    # Убираем /4392 из конца ссылки
                    import re
                    clean_link = re.sub(r'/\d+/?$', '', source_link)
                    logger.info(f"Очищена ссылка: {source_link} -> {clean_link}")

                logger.info(f"Получаем entity для: {clean_link}")
                entity = await client.get_entity(clean_link)
                chat_title = entity.title if hasattr(entity, 'title') else clean_link
                logger.info(f"Entity получен: {chat_title}")
            except Exception as e:
                error_msg = f"❌ Не удалось найти источник: {e}"
                logger.error(
                    f"Ошибка get_entity: {e}, ссылка: {source_link}, clean: {clean_link if 'clean_link' in locals() else 'N/A'}")
                await self.bot.send_message(user_id, error_msg)
                await client.disconnect()
                return

            # Определяем режим парсинга
            mode_text = ""
            if parse_mode == 'comments' and message_id:
                mode_text = f"💬 Режим: Комментарии под постом #{message_id}"
            elif parse_mode == 'from_message' and message_id:
                mode_text = f"📨 Режим: С сообщения #{message_id}"
            else:
                mode_text = "📊 Режим: Обычный парсинг"

            await self.bot.send_message(
                user_id,
                f"🔍 **Парсинг запущен**\n\n"
                f"📂 Источник: {chat_title}\n"
                f"{mode_text}\n"
                f"📊 Лимит: {limit} сообщений\n"
                f"⏳ Начинаю обработку...",
                parse_mode='markdown'
            )
            await self.send_log(f"🔍 Парсинг: {chat_title} ({limit} msg, mode: {parse_mode or 'normal'})")

            # Получаем сообщения в зависимости от режима
            last_progress_update = 0

            if parse_mode == 'comments' and message_id:
                # ПАРСИНГ КОММЕНТАРИЕВ
                try:
                    # Получаем пост
                    post = await client.get_messages(entity, ids=message_id)
                    if not post or not post.replies:
                        await self.bot.send_message(user_id, "❌ У этого поста нет комментариев или доступа к ним")
                        await client.disconnect()
                        return

                    # Парсим комментарии
                    async for message in client.iter_messages(entity, reply_to=message_id, limit=limit):
                        messages_processed += 1

                        # Прогресс
                        progress_step = max(50, limit // 10)
                        if messages_processed - last_progress_update >= progress_step:
                            elapsed = (datetime.now() - start_time).total_seconds()
                            speed = messages_processed / elapsed if elapsed > 0 else 0
                            remaining = (limit - messages_processed) / speed if speed > 0 else 0

                            progress_percent = int((messages_processed / limit) * 100)
                            progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)

                            await self.bot.send_message(
                                user_id,
                                f"📊 **Прогресс парсинга комментариев**\n\n"
                                f"{progress_bar} {progress_percent}%\n\n"
                                f"💬 Обработано: {messages_processed}/{limit}\n"
                                f"👥 Найдено уникальных: {unique_users}\n"
                                f"⚡️ Скорость: {speed:.1f} комм/сек\n"
                                f"⏱ Осталось: ~{int(remaining)}с"
                            )
                            last_progress_update = messages_processed

                        if not message.from_id:
                            continue

                        try:
                            user = await client.get_entity(message.from_id)

                            if hasattr(user, 'bot') and user.bot:
                                bots_skipped += 1
                                continue

                            if user.id in seen_users:
                                duplicates_skipped += 1
                                continue

                            seen_users.add(user.id)
                            unique_users += 1

                            if not hasattr(user, 'id'):
                                continue

                            user_data = {
                                'user_id': user.id,
                                'username': user.username if hasattr(user, 'username') else None,
                                'first_name': user.first_name if hasattr(user, 'first_name') else None,
                                'last_name': user.last_name if hasattr(user, 'last_name') else None,
                                'source_chat': source_link,
                                'source_msg_id': message.id
                            }

                            UserManager.add_from_message(user_data)
                            users_found += 1

                        except Exception as e:
                            logger.debug(f"Ошибка обработки пользователя: {e}")
                            continue

                        await asyncio.sleep(0.005)

                except Exception as e:
                    await self.bot.send_message(user_id, f"❌ Ошибка парсинга комментариев: {e}")
                    await client.disconnect()
                    return

            elif parse_mode == 'from_message' and message_id:
                # ПАРСИНГ С КОНКРЕТНОГО СООБЩЕНИЯ
                async for message in client.iter_messages(entity, limit=limit, offset_id=message_id - 1, reverse=True):
                    messages_processed += 1

                    # Прогресс
                    progress_step = max(50, limit // 10)
                    if messages_processed - last_progress_update >= progress_step:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        speed = messages_processed / elapsed if elapsed > 0 else 0
                        remaining = (limit - messages_processed) / speed if speed > 0 else 0

                        progress_percent = int((messages_processed / limit) * 100)
                        progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)

                        await self.bot.send_message(
                            user_id,
                            f"📊 **Прогресс парсинга**\n\n"
                            f"{progress_bar} {progress_percent}%\n\n"
                            f"📨 Обработано: {messages_processed}/{limit}\n"
                            f"👥 Найдено уникальных: {unique_users}\n"
                            f"⚡️ Скорость: {speed:.1f} сообщ/сек\n"
                            f"⏱ Осталось: ~{int(remaining)}с"
                        )
                        last_progress_update = messages_processed

                    if not message.from_id:
                        continue

                    try:
                        user = await client.get_entity(message.from_id)

                        if hasattr(user, 'bot') and user.bot:
                            bots_skipped += 1
                            continue

                        if user.id in seen_users:
                            duplicates_skipped += 1
                            continue

                        seen_users.add(user.id)
                        unique_users += 1

                        if not hasattr(user, 'id'):
                            continue

                        user_data = {
                            'user_id': user.id,
                            'username': user.username if hasattr(user, 'username') else None,
                            'first_name': user.first_name if hasattr(user, 'first_name') else None,
                            'last_name': user.last_name if hasattr(user, 'last_name') else None,
                            'source_chat': source_link,
                            'source_msg_id': message.id
                        }

                        UserManager.add_from_message(user_data)
                        users_found += 1

                    except Exception as e:
                        logger.debug(f"Ошибка обработки пользователя: {e}")
                        continue

                    await asyncio.sleep(0.005)

            else:
                # ОБЫЧНЫЙ ПАРСИНГ
                async for message in client.iter_messages(entity, limit=limit):
                    messages_processed += 1

                    # Прогресс каждые 50 сообщений или 10% от лимита
                    progress_step = max(50, limit // 10)
                    if messages_processed - last_progress_update >= progress_step:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        speed = messages_processed / elapsed if elapsed > 0 else 0
                        remaining = (limit - messages_processed) / speed if speed > 0 else 0

                        progress_percent = int((messages_processed / limit) * 100)
                        progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)

                        await self.bot.send_message(
                            user_id,
                            f"📊 **Прогресс парсинга**\n\n"
                            f"{progress_bar} {progress_percent}%\n\n"
                            f"📨 Обработано: {messages_processed}/{limit}\n"
                            f"👥 Найдено уникальных: {unique_users}\n"
                            f"⚡️ Скорость: {speed:.1f} сообщ/сек\n"
                            f"⏱ Осталось: ~{int(remaining)}с"
                        )
                        last_progress_update = messages_processed

                    if not message.from_id:
                        continue

                    try:
                        user = await client.get_entity(message.from_id)

                        if hasattr(user, 'bot') and user.bot:
                            bots_skipped += 1
                            continue

                        if user.id in seen_users:
                            duplicates_skipped += 1
                            continue

                        seen_users.add(user.id)
                        unique_users += 1

                        if not hasattr(user, 'id'):
                            continue

                        user_data = {
                            'user_id': user.id,
                            'username': user.username if hasattr(user, 'username') else None,
                            'first_name': user.first_name if hasattr(user, 'first_name') else None,
                            'last_name': user.last_name if hasattr(user, 'last_name') else None,
                            'source_chat': source_link,
                            'source_msg_id': message.id
                        }

                        UserManager.add_from_message(user_data)
                        users_found += 1

                    except Exception as e:
                        logger.debug(f"Ошибка обработки пользователя: {e}")
                        continue

                    await asyncio.sleep(0.005)

            await client.disconnect()

            # Подсчет финальной статистики
            elapsed_time = (datetime.now() - start_time).total_seconds()
            avg_speed = messages_processed / elapsed_time if elapsed_time > 0 else 0

            # Процент уникальности
            uniqueness_rate = (unique_users / messages_processed * 100) if messages_processed > 0 else 0

            AccountManager.update_stats(account['phone'], 'parses', users_found)

            # Кнопка для экспорта
            export_button = [[Button.inline("📤 Экспортировать этот парсинг", f"export_source:{source_link}".encode())]]

            await self.bot.send_message(
                user_id,
                f"✅ **ПАРСИНГ ЗАВЕРШЁН!**\n\n"
                f"📊 **Статистика:**\n"
                f"• Обработано сообщений: {messages_processed}\n"
                f"• 👥 Уникальных пользователей: **{unique_users}**\n"
                f"• ✅ Сохранено в базу: {users_found}\n"
                f"• 🤖 Ботов пропущено: {bots_skipped}\n"
                f"• 🔄 Дубликатов пропущено: {duplicates_skipped}\n\n"
                f"⚡️ **Производительность:**\n"
                f"• Скорость: {avg_speed:.1f} сообщ/сек\n"
                f"• Время: {int(elapsed_time)}с ({elapsed_time / 60:.1f} мин)\n"
                f"• Уникальность: {uniqueness_rate:.1f}%\n\n"
                f"📂 Источник: {chat_title}\n\n"
                f"Нажмите кнопку ниже для экспорта:",
                buttons=export_button,
                parse_mode='markdown'
            )

            await self.send_log(
                f"✅ Парсинг: {unique_users} уникальных из {messages_processed} сообщений ({chat_title[:30]})"
            )

        except Exception as e:
            await self.bot.send_message(user_id, f"❌ Критическая ошибка парсинга: {str(e)}")
            logger.error(f"Ошибка парсинга: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # Очищаем активную задачу
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

    async def run_mass_invite(self, user_id: int, account: dict, target_chat: str, filepath: str, delay_min: int,
                              delay_max: int, invite_limit: int = None):
        """Массовый инвайтинг из файла с лимитом"""
        success = 0
        failed = 0
        total = 0
        start_time = datetime.now()

        try:
            # Получаем прокси для аккаунта
            proxy_str = account.get('proxy')
            proxy = None
            if proxy_str:
                # Парсим прокси
                auth_mgr = AuthManager(account['phone'], proxy=proxy_str)
                proxy = auth_mgr.proxy

            client = TelegramClient(account['session_file'], API_ID, API_HASH, proxy=proxy)
            await client.connect()

            if not await client.is_user_authorized():
                await self.bot.send_message(user_id, "❌ Аккаунт не авторизован")
                await client.disconnect()
                return

            # Получаем entity целевого чата
            try:
                target = await client.get_entity(target_chat)
                target_title = target.title if hasattr(target, 'title') else target_chat
            except Exception as e:
                await self.bot.send_message(user_id, f"❌ Не удалось найти чат: {e}")
                await client.disconnect()
                return

            # Читаем файл с пользователями
            users_to_invite = []
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # Парсим строку: "@username | ID: 123456" или "ID: 123456"
                    if '|' in line:
                        # Есть username и ID
                        parts = line.split('|')
                        username = parts[0].strip().replace('@', '')
                        users_to_invite.append(username)
                    elif line.startswith('ID:'):
                        # Только ID
                        user_id_str = line.replace('ID:', '').strip()
                        try:
                            users_to_invite.append(int(user_id_str))
                        except:
                            continue
                    elif line.startswith('@'):
                        # Только username
                        users_to_invite.append(line.replace('@', '').strip())

                    # Ограничиваем количество если задан лимит
                    if invite_limit and len(users_to_invite) >= invite_limit:
                        break

            total = len(users_to_invite)

            if total == 0:
                await self.bot.send_message(user_id, "❌ В файле нет пользователей для инвайта")
                await client.disconnect()
                return

            # Информация о прокси
            proxy_info = f"\n🌐 Прокси: {proxy_str[:30]}..." if proxy_str else ""

            await self.bot.send_message(
                user_id,
                f"🚀 **ИНВАЙТИНГ ЗАПУЩЕН**\n\n"
                f"📊 Всего пользователей: {total}\n"
                f"🎯 Целевой чат: {target_title}\n"
                f"📱 Аккаунт: {account['phone']}{proxy_info}\n"
                f"⏱ Задержка: {delay_min}-{delay_max} сек\n\n"
                f"Начинаю обработку...",
                parse_mode='markdown'
            )

            # Инвайтим пользователей
            skipped = 0  # Пропущено (боты, уже в чате и т.д.)
            flood_errors = 0  # Счетчик флуд-ошибок
            consecutive_errors = 0  # Подряд идущие ошибки
            already_members = 0  # Уже в чате
            privacy_restricted = 0  # Настройки приватности
            last_progress_update = 0

            for idx, user_identifier in enumerate(users_to_invite, 1):
                try:
                    # Получаем пользователя
                    try:
                        user = await client.get_entity(user_identifier)
                    except Exception as e:
                        failed += 1
                        consecutive_errors += 1
                        logger.error(f"Не удалось получить пользователя {user_identifier}: {e}")

                        # Если много ошибок подряд - увеличиваем задержку
                        if consecutive_errors >= 3:
                            await asyncio.sleep(5)
                        continue

                    # Проверяем что не бот
                    if hasattr(user, 'bot') and user.bot:
                        skipped += 1
                        continue

                    # Инвайтим с проверкой результата
                    try:
                        result = await client(InviteToChannelRequest(target, [user]))
                        # Проверяем результат - успешно ли добавлен
                        if result:
                            success += 1
                            consecutive_errors = 0
                            logger.info(f"Успешно добавлен: {user_identifier}")
                        else:
                            failed += 1
                            logger.warning(f"Не удалось добавить (пустой результат): {user_identifier}")
                    except Exception as invite_error:
                        # Обработка специфичных ошибок инвайта
                        error_str = str(invite_error).lower()

                        if 'already' in error_str or 'participant' in error_str:
                            # Уже в чате
                            skipped += 1
                            already_members += 1
                            logger.info(f"Уже в чате: {user_identifier}")
                        elif 'privacy' in error_str or 'restricted' in error_str:
                            # Настройки приватности
                            failed += 1
                            privacy_restricted += 1
                            logger.info(f"Настройки приватности: {user_identifier}")
                        else:
                            # Другая ошибка
                            failed += 1
                            logger.error(f"Ошибка инвайта {user_identifier}: {invite_error}")

                        consecutive_errors += 1
                        continue

                    # Прогресс каждые 5 пользователей
                    if idx - last_progress_update >= 5:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        processed = success + failed + skipped
                        speed = processed / elapsed if elapsed > 0 else 0
                        remaining_users = total - idx
                        eta = remaining_users / speed if speed > 0 else 0

                        # Прогресс-бар
                        progress_percent = int((idx / total) * 100)
                        progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)

                        # Процент успеха
                        success_rate = (success / processed * 100) if processed > 0 else 0

                        await self.bot.send_message(
                            user_id,
                            f"📊 **ПРОГРЕСС ИНВАЙТИНГА**\n\n"
                            f"{progress_bar} {progress_percent}%\n\n"
                            f"👥 Обработано: {idx}/{total}\n"
                            f"✅ Успешно: {success}\n"
                            f"❌ Ошибок: {failed}\n"
                            f"⏭ Пропущено: {skipped}\n"
                            f"📈 Успешность: {success_rate:.1f}%\n\n"
                            f"⚡️ Скорость: {speed * 60:.1f} чел/мин\n"
                            f"⏱ Осталось: ~{int(eta / 60)}м {int(eta % 60)}с\n"
                            f"⏳ Задержка: {delay_min:.0f}-{delay_max:.0f}с",
                            parse_mode='markdown'
                        )
                        last_progress_update = idx

                    # Умная задержка
                    delay = random.uniform(delay_min, delay_max)
                    await asyncio.sleep(delay)

                except FloodWaitError as e:
                    # Флуд - ждем указанное время + запас
                    flood_errors += 1
                    wait_time = e.seconds + random.randint(10, 30)

                    await self.bot.send_message(
                        user_id,
                        f"⚠️ **ФЛУД-КОНТРОЛЬ** #{flood_errors}\n\n"
                        f"⏳ Ожидание: {wait_time} секунд ({wait_time // 60}м {wait_time % 60}с)\n"
                        f"📊 Прогресс: {idx}/{total}\n\n"
                        f"После ожидания задержка будет увеличена автоматически",
                        parse_mode='markdown'
                    )
                    await asyncio.sleep(wait_time)

                    # Агрессивное увеличение задержки после флуда
                    delay_min = min(delay_min * 2, 120)
                    delay_max = min(delay_max * 2, 180)

                except UserPrivacyRestrictedError:
                    failed += 1
                    privacy_restricted += 1
                    logger.info(f"Privacy restricted: {user_identifier}")

                except UserNotMutualContactError:
                    failed += 1
                    logger.info(f"Not mutual contact: {user_identifier}")

                except UserChannelsTooMuchError:
                    failed += 1
                    logger.info(f"User in too many channels: {user_identifier}")

                except ChatAdminRequiredError:
                    await self.bot.send_message(
                        user_id,
                        f"❌ **КРИТИЧЕСКАЯ ОШИБКА**\n\n"
                        f"Нет прав администратора в {target_title}\n"
                        f"Инвайтинг остановлен",
                        parse_mode='markdown'
                    )
                    break

                except Exception as e:
                    failed += 1
                    consecutive_errors += 1
                    error_msg = str(e).lower()

                    # Обработка "Too many requests"
                    if 'too many requests' in error_msg or 'flood' in error_msg:
                        flood_errors += 1
                        wait_time = 60 + random.randint(30, 60)  # 1.5-2 минуты

                        await self.bot.send_message(
                            user_id,
                            f"⚠️ **СЛИШКОМ МНОГО ЗАПРОСОВ** (#{flood_errors})\n\n"
                            f"⏸ Пауза: {wait_time} секунд ({wait_time // 60}м {wait_time % 60}с)\n"
                            f"📊 Прогресс: {idx}/{total}\n\n"
                            f"💡 Рекомендация: увеличьте задержку между инвайтами",
                            parse_mode='markdown'
                        )
                        await asyncio.sleep(wait_time)

                        # Сильно увеличиваем задержку
                        delay_min = min(delay_min * 3, 120)
                        delay_max = min(delay_max * 3, 180)

                    elif 'already' in error_msg or 'participant' in error_msg:
                        skipped += 1
                        already_members += 1
                        logger.info(f"User already in chat: {user_identifier}")
                    else:
                        logger.error(f"Ошибка инвайта {user_identifier}: {e}")

                    # Если много ошибок подряд - делаем паузу
                    if consecutive_errors >= 5:
                        await self.bot.send_message(
                            user_id,
                            f"⚠️ **{consecutive_errors} ОШИБОК ПОДРЯД**\n\n"
                            f"⏸ Пауза 30 секунд для стабилизации...",
                            parse_mode='markdown'
                        )
                        await asyncio.sleep(30)
                        consecutive_errors = 0

            await client.disconnect()

            # Итоговая статистика
            elapsed_time = (datetime.now() - start_time).total_seconds()
            success_rate = round(success / total * 100 if total > 0 else 0, 1)
            avg_speed = (success + failed + skipped) / elapsed_time * 60 if elapsed_time > 0 else 0

            await self.bot.send_message(
                user_id,
                f"✅ **ИНВАЙТИНГ ЗАВЕРШЁН!**\n\n"
                f"📊 **ИТОГОВАЯ СТАТИСТИКА:**\n"
                f"• Всего обработано: {total}\n"
                f"• ✅ Успешно добавлено: **{success}**\n"
                f"• ❌ Ошибок: {failed}\n"
                f"• ⏭ Пропущено: {skipped}\n"
                f"  └ Уже в чате: {already_members}\n"
                f"  └ Настройки приватности: {privacy_restricted}\n"
                f"• 📈 Успешность: **{success_rate}%**\n\n"
                f"⚡️ **ПРОИЗВОДИТЕЛЬНОСТЬ:**\n"
                f"• Средняя скорость: {avg_speed:.1f} чел/мин\n"
                f"• Время работы: {int(elapsed_time // 60)}м {int(elapsed_time % 60)}с\n"
                f"• Флуд-ошибок: {flood_errors}\n\n"
                f"🎯 Целевой чат: {target_title}\n"
                f"📱 Аккаунт: {account['phone']}",
                parse_mode='markdown'
            )

            await self.send_log(
                f"✅ Инвайтинг: {success}/{total} ({success_rate}%) в {target_title[:30]}"
            )

            # Логируем результат
            user_entity = await self.bot.get_entity(user_id)
            username = user_entity.username if hasattr(user_entity, 'username') else None
            UserLogger.log_action(
                user_id,
                username,
                "INVITE_COMPLETED",
                f"Успешно: {success}/{total}, Чат: {target_chat}"
            )

            # Обновляем статистику аккаунта
            AccountManager.update_stats(account['phone'], 'invites', success)

        except Exception as e:
            await self.bot.send_message(
                user_id,
                f"❌ **КРИТИЧЕСКАЯ ОШИБКА**\n\n{str(e)}",
                parse_mode='markdown'
            )
            logger.error(f"Критическая ошибка run_mass_invite: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # Очищаем активную задачу
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

        """РАЗДЕЛЬНО ПАРСЕР И ИНВАЙТЕР"""


# ========== ЗАПУСК ==========
async def main():
    print("=" * 70)
    print("🚀 SWILL PARSER PRO 2026 - Парсинг по сообщениям")
    print("=" * 70)
    print(f"📁 Папка: {BASE_DIR}")
    print(f"📨 Логи: @{LOG_CHAT}")
    print("=" * 70)

    bot = SwillParserBot()

    try:
        await bot.start()
    except KeyboardInterrupt:
        print("\n👋 Завершение работы...")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")


if __name__ == "__main__":
    # Проверка зависимостей
    try:
        import telethon
    except ImportError:
        print("❌ Установите: pip install telethon")
        sys.exit(1)

    asyncio.run(main())

# ===================== ACCOUNT STATUS FIX =====================
# Correct account authorization check via session + is_user_authorized

from telethon import TelegramClient


async def check_account_status(phone: str) -> bool:
    session_path = SESSIONS_DIR / f"{phone}.session"
    if not session_path.exists():
        return False

    client = TelegramClient(str(session_path), API_ID, API_HASH)
    try:
        await client.connect()
        return await client.is_user_authorized()
    except Exception:
        return False
    finally:
        await client.disconnect()


# =============================================================


# ===================== FINAL FIX: USE REAL AUTH CHECK =====================
# Override cmd_accounts to compute real authorization status from session

async def _cmd_accounts_fixed(self, event):
    accounts = AccountManager.get_all()

    if not accounts:
        await event.respond("❌ Нет добавленных аккаунтов")
        return

    text = "📋 **Аккаунты:**\n\n"
    for acc in accounts:
        real_auth = await check_account_status(acc['phone'])
        AccountManager.mark_authorized(acc['phone'], real_auth)

        status = "🟢" if real_auth else "❌"
        text += f"{status} **{acc['phone']}**\n"
        text += f"   📨 {acc['total_invites']} | 🔍 {acc['total_parses']}\n\n"

    buttons = [
        [Button.inline("➕ Добавить аккаунт", b"add_account"),
         Button.inline("🔐 Авторизовать", b"auth_menu")]
    ]

    await event.respond(text, buttons=buttons, parse_mode='markdown')


# Monkey-patch
SwillParserBot.cmd_accounts = _cmd_accounts_fixed
# ===================== END FINAL FIX =====================
