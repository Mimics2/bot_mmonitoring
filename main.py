import logging
import asyncio
import sys
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, CallbackQueryHandler, 
    CallbackContext, Filters
)
import json
import sqlite3

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация для Realway
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', '2040'))
API_HASH = os.getenv('API_HASH', 'b18441a1ff607e10a989891a5462e627')
ADMINS_STR = os.getenv('ADMINS', '')
ADMINS = [int(x.strip()) for x in ADMINS_STR.split(',') if x.strip()] if ADMINS_STR else []

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения Realway")

logger.info(f"Конфигурация загружена успешно. Админы: {ADMINS}")

class Database:
    def __init__(self, db_path="users_data.db"):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    session_string TEXT,
                    keywords TEXT DEFAULT '[]',
                    exceptions TEXT DEFAULT '[]',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS allowed_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            for admin_id in ADMINS:
                cursor.execute('''
                    INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) 
                    VALUES (?, ?, ?)
                ''', (admin_id, f"admin_{admin_id}", 0))
            
            conn.commit()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username FROM allowed_users')
            users = cursor.fetchall()
            logger.info(f"Пользователи в белом списке: {users}")
    
    def is_user_allowed(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result is not None
    
    def add_allowed_user(self, user_id, username, admin_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO allowed_users (user_id, username, added_by) 
                VALUES (?, ?, ?)
            ''', (user_id, username, admin_id))
            conn.commit()
        logger.info(f"✅ Пользователь {user_id} добавлен")
    
    def remove_allowed_user(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM allowed_users WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            conn.commit()
        logger.info(f"❌ Пользователь {user_id} удален")
    
    def get_allowed_users(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username, added_at FROM allowed_users')
            return cursor.fetchall()
    
    def save_session(self, user_id, username, session_string):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, session_string) 
                VALUES (?, ?, ?)
            ''', (user_id, username, session_string))
            conn.commit()
        logger.info(f"💾 Сессия сохранена для {user_id}")
    
    def get_user_session(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT session_string FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def save_keywords(self, user_id, keywords, exceptions):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET keywords = ?, exceptions = ? 
                WHERE user_id = ?
            ''', (json.dumps(keywords), json.dumps(exceptions), user_id))
            conn.commit()
        logger.info(f"⚙️ Фильтры обновлены для {user_id}")
    
    def get_user_settings(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT keywords, exceptions FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0]), json.loads(result[1])
            return [], []
    
    def get_all_active_users(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, session_string, keywords, exceptions 
                FROM users 
                WHERE session_string IS NOT NULL AND is_active = 1
            ''')
            return cursor.fetchall()

class SessionManager:
    def __init__(self, api_id, api_hash, database, bot):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = database
        self.bot = bot
        self.active_clients = {}
        
    def start_all_sessions(self):
        try:
            users = self.db.get_all_active_users()
            logger.info(f"🔄 Найдено {len(users)} пользователей для запуска")
            
            for user_id, session_string, keywords_json, exceptions_json in users:
                self.start_session(user_id, session_string)
                
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сессий: {e}")
    
    def start_session(self, user_id, session_string):
        try:
            if user_id in self.active_clients:
                try:
                    self.stop_session(user_id)
                except:
                    pass
            
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon import events
            
            async def start_client():
                client = TelegramClient(
                    StringSession(session_string),
                    self.api_id,
                    self.api_hash
                )
                await client.start()
                return client
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = loop.run_until_complete(start_client())
            
            keywords, exceptions = self.db.get_user_settings(user_id)
            
            @client.on(events.NewMessage)
            async def handler(event):
                await self.handle_message(user_id, event, keywords, exceptions)
            
            self.active_clients[user_id] = client
            logger.info(f"✅ Сессия для {user_id} запущена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сессии для {user_id}: {e}")
    
    async def handle_message(self, user_id, event, keywords, exceptions):
        """Обработка сообщений с ПЕРЕСЫЛКОЙ"""
        try:
            from telethon import events
            
            message = event.message
            if not message.text:
                return
            
            text_lower = message.text.lower()
            keywords_lower = [k.lower() for k in keywords]
            exceptions_lower = [e.lower() for e in exceptions]
            
            keyword_found = any(keyword in text_lower for keyword in keywords_lower)
            if not keyword_found:
                return
            
            exception_found = any(exception in text_lower for exception in exceptions_lower)
            if exception_found:
                return
            
            sender = await event.get_sender()
            sender_username = f"@{sender.username}" if sender and sender.username else "Нет username"
            sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or "Неизвестно"
            sender_id = sender.id if sender else "Неизвестно"
            
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', '') or getattr(chat, 'username', '') or "Личные сообщения"
            
            # ФОРМАТИРУЕМ ПОЛНОЕ СООБЩЕНИЕ ДЛЯ ПЕРЕСЫЛКИ
            full_message = (
                f"🔔 **Найдено совпадение!**\n\n"
                f"👤 **От:** {sender_username} ({sender_name})\n"
                f"🆔 **ID:** `{sender_id}`\n"
                f"📋 **Чат:** {chat_title}\n"
                f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💬 **Сообщение:**\n{message.text}"
            )
            
            # ОТПРАВЛЯЕМ ПОЛНОЕ СООБЩЕНИЕ
            try:
                self.bot.send_message(user_id, full_message, parse_mode='Markdown')
                logger.info(f"📨 Сообщение переслано пользователю {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки: {e}")
                # Если сообщение слишком длинное, разбиваем на части
                if len(full_message) > 4096:
                    info_part = (
                        f"🔔 **Найдено совпадение!**\n\n"
                        f"👤 **От:** {sender_username} ({sender_name})\n"
                        f"🆔 **ID:** `{sender_id}`\n"
                        f"📋 **Чат:** {chat_title}\n"
                        f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.bot.send_message(user_id, info_part, parse_mode='Markdown')
                    
                    # Отправляем текст сообщения частями
                    message_text = message.text
                    for i in range(0, len(message_text), 4000):
                        chunk = message_text[i:i + 4000]
                        self.bot.send_message(user_id, f"📝 **Текст:**\n{chunk}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}")
    
    def stop_session(self, user_id):
        if user_id in self.active_clients:
            try:
                async def disconnect_client():
                    await self.active_clients[user_id].disconnect()
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(disconnect_client())
                loop.close()
                
                del self.active_clients[user_id]
                logger.info(f"🛑 Сессия {user_id} остановлена")
            except Exception as e:
                logger.error(f"❌ Ошибка остановки сессии {user_id}: {e}")
    
    def restart_session(self, user_id):
        session_string = self.db.get_user_session(user_id)
        if session_string:
            self.start_session(user_id, session_string)

# Остальной код MonitorBot остается без изменений...
class MonitorBot:
    def __init__(self):
        self.db = Database()
        self.updater = None
        self.session_manager = None
    
    def start(self):
        try:
            logger.info("🚀 Запуск бота...")
            self.updater = Updater(BOT_TOKEN, use_context=True)
            self.session_manager = SessionManager(API_ID, API_HASH, self.db, self.updater.bot)
            self.setup_handlers()
            self.session_manager.start_all_sessions()
            logger.info("🤖 Бот запущен")
            self.updater.start_polling()
            self.updater.idle()
        except Exception as e:
            logger.error(f"💥 Ошибка: {e}")
            raise
    
    def setup_handlers(self):
        dp = self.updater.dispatcher
        dp.add_handler(CommandHandler("start", self.start_command))
        dp.add_handler(CommandHandler("admin", self.admin_command))
        dp.add_handler(CommandHandler("debug", self.debug_command))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        dp.add_handler(CallbackQueryHandler(self.handle_callback))
        dp.add_error_handler(self.error_handler)
    
    def debug_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Нет username"
        
        if user_id in ADMINS:
            self.db.add_allowed_user(user_id, username, user_id)
            status = "✅ АДМИН"
        else:
            status = "❌ НЕ АДМИН"
        
        is_allowed = self.db.is_user_allowed(user_id)
        debug_info = (
            f"🔧 **Отладка:**\n\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"👤 **Username:** @{username}\n"
            f"👑 **Статус:** {status}\n"
            f"🔐 **В белом списке:** {'✅ ДА' if is_allowed else '❌ НЕТ'}\n"
        )
        update.message.reply_text(debug_info, parse_mode='Markdown')
    
    def start_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        if user_id in ADMINS:
            self.db.add_allowed_user(user_id, username, user_id)
        
        if not self.db.is_user_allowed(user_id):
            update.message.reply_text("❌ Доступ запрещен. Используйте /debug")
            return
        
        keyboard = [
            [InlineKeyboardButton("📤 Загрузить сессию", callback_data="upload_session")],
            [InlineKeyboardButton("⚙️ Настройки фильтров", callback_data="settings")],
            [InlineKeyboardButton("📊 Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("👋 Добро пожаловать!", reply_markup=reply_markup)

    # ... остальные методы MonitorBot без изменений

def main():
    bot = MonitorBot()
    bot.start()

if __name__ == "__main__":
    main()
