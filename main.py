import logging
import asyncio
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, Filters
)
import json
import os

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

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', '2040'))
API_HASH = os.getenv('API_HASH', 'b18441a1ff607e10a989891a5462e627')
ADMINS = [int(x.strip()) for x in os.getenv('ADMINS', '').split(',') if x.strip()]
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
PORT = int(os.getenv('PORT', 8443))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

class Database:
    def __init__(self, db_path="users_data.db"):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_db(self):
        import sqlite3
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
            conn.commit()
    
    def is_user_allowed(self, user_id):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM allowed_users WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    
    def add_allowed_user(self, user_id, username, admin_id):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO allowed_users (user_id, username, added_by) 
                VALUES (?, ?, ?)
            ''', (user_id, username, admin_id))
            conn.commit()
    
    def remove_allowed_user(self, user_id):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM allowed_users WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def get_allowed_users(self):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username, added_at FROM allowed_users')
            return cursor.fetchall()
    
    def save_session(self, user_id, username, session_string):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, session_string) 
                VALUES (?, ?, ?)
            ''', (user_id, username, session_string))
            conn.commit()
    
    def get_user_session(self, user_id):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT session_string FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def save_keywords(self, user_id, keywords, exceptions):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET keywords = ?, exceptions = ? 
                WHERE user_id = ?
            ''', (json.dumps(keywords), json.dumps(exceptions), user_id))
            conn.commit()
    
    def get_user_settings(self, user_id):
        import sqlite3
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT keywords, exceptions FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0]), json.loads(result[1])
            return [], []
    
    def get_all_active_users(self):
        import sqlite3
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
        
    async def start_all_sessions(self):
        try:
            users = self.db.get_all_active_users()
            logger.info(f"Найдено {len(users)} пользователей для запуска")
            
            for user_id, session_string, keywords_json, exceptions_json in users:
                await self.start_session(user_id, session_string)
                
        except Exception as e:
            logger.error(f"Ошибка запуска сессий: {e}")
    
    async def start_session(self, user_id, session_string):
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
            if user_id in self.active_clients:
                try:
                    await self.active_clients[user_id].disconnect()
                except:
                    pass
            
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash
            )
            
            await client.start()
            
            keywords, exceptions = self.db.get_user_settings(user_id)
            
            @client.on(events.NewMessage)
            async def handler(event):
                await self.handle_message(user_id, event, keywords, exceptions)
            
            self.active_clients[user_id] = client
            logger.info(f"Сессия для пользователя {user_id} запущена")
            
        except Exception as e:
            logger.error(f"Ошибка запуска сессии для {user_id}: {e}")
            try:
                await self.bot.send_message(
                    user_id, 
                    f"❌ Ошибка запуска сессии: {str(e)}"
                )
            except:
                pass
    
    async def handle_message(self, user_id, event, keywords, exceptions):
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
            
            alert_message = (
                f"🔔 **Найдено совпадение!**\n\n"
                f"👤 **Пользователь:** {sender_username}\n"
                f"📛 **Ник:** {sender_name}\n"
                f"🆔 **ID:** `{sender_id}`\n"
                f"💬 **Текст:** {message.text[:500]}\n"
                f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            await self.bot.send_message(user_id, alert_message)
            logger.info(f"Отправлено уведомление пользователю {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
    
    async def stop_session(self, user_id):
        if user_id in self.active_clients:
            try:
                await self.active_clients[user_id].disconnect()
                del self.active_clients[user_id]
                logger.info(f"Сессия пользователя {user_id} остановлена")
            except Exception as e:
                logger.error(f"Ошибка остановки сессии {user_id}: {e}")
    
    async def restart_session(self, user_id):
        session_string = self.db.get_user_session(user_id)
        if session_string:
            await self.start_session(user_id, session_string)

class MonitorBot:
    def __init__(self):
        self.db = Database()
        self.application = None
        self.session_manager = None
    
    async def start(self):
        try:
            logger.info("Запуск бота...")
            
            self.application = Application.builder().token(BOT_TOKEN).build()
            self.session_manager = SessionManager(API_ID, API_HASH, self.db, self.application.bot)
            
            self.setup_handlers()
            await self.session_manager.start_all_sessions()
            
            if WEBHOOK_URL:
                await self.start_webhook()
            else:
                await self.application.run_polling()
                
        except Exception as e:
            logger.error(f"Критическая ошибка при запуске: {e}")
            raise
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        self.application.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_error_handler(self.error_handler)
    
    async def start_webhook(self):
        await self.application.bot.set_webhook(
            url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
        
        await self.application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.db.is_user_allowed(user_id):
            await update.message.reply_text(
                "❌ Доступ запрещен.\nОбратитесь к администратору для получения доступа."
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📤 Загрузить сессию", callback_data="upload_session")],
            [InlineKeyboardButton("⚙️ Настройки фильтров", callback_data="settings")],
            [InlineKeyboardButton("📊 Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👋 Добро пожаловать в мониторинг Telegram!\n\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMINS:
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        keyboard = [
            [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("🔄 Перезапуск сессий", callback_data="admin_restart")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🛠️ **Админ панель**\n\nВыберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        
        if not self.db.is_user_allowed(user_id):
            return
        
        user_state = context.user_data.get('state')
        
        if user_state == 'waiting_session':
            await self.save_session(update, text)
            context.user_data['state'] = None
            
        elif user_state == 'waiting_keywords':
            await self.save_keywords(update, text)
            context.user_data['state'] = None
            
        elif user_state == 'waiting_exceptions':
            await self.save_exceptions(update, text)
            context.user_data['state'] = None
            
        elif user_state == 'admin_waiting_user':
            await self.admin_add_user(update, text)
            context.user_data['state'] = None
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data == "upload_session":
            await self.upload_session(query, context)
        elif data == "settings":
            await self.show_settings(query)
        elif data == "status":
            await self.show_status(query)
        elif data == "set_keywords":
            await self.set_keywords(query, context)
        elif data == "set_exceptions":
            await self.set_exceptions(query, context)
        elif data == "back_to_main":
            await self.start_command(query, context)
        elif data == "admin_users":
            await self.admin_users(query)
        elif data == "admin_stats":
            await self.admin_stats(query)
        elif data == "admin_restart":
            await self.admin_restart(query)
        elif data == "admin_back":
            await self.admin_command(query, context)
        elif data == "admin_add_user":
            await self.admin_add_user_dialog(query, context)
        elif data.startswith("admin_remove_user:"):
            target_user_id = int(data.split(":")[1])
            await self.admin_remove_user(query, target_user_id)
    
    async def upload_session(self, query, context):
        context.user_data['state'] = 'waiting_session'
        await query.edit_message_text(
            "📤 **Загрузка сессии**\n\nОтправьте строку сессии в следующем сообщении.\n⚠️ *Внимание:* При повторной отправке старая сессия будет заменена.",
            parse_mode='Markdown'
        )
    
    async def save_session(self, update, session_string):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
            client = TelegramClient(
                StringSession(session_string),
                API_ID,
                API_HASH
            )
            
            await client.start()
            me = await client.get_me()
            await client.disconnect()
            
            self.db.save_session(user_id, username, session_string)
            await self.session_manager.start_session(user_id, session_string)
            
            await update.message.reply_text(
                f"✅ **Сессия успешно сохранена!**\n\n👤 Аккаунт: {me.first_name or ''} (@{me.username or 'нет'})\n🆔 ID: `{me.id}`\n\nТеперь настройте фильтры для мониторинга.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ **Ошибка сохранения сессии:**\n{str(e)}")
    
    async def show_settings(self, query):
        user_id = query.from_user.id
        keywords, exceptions = self.db.get_user_settings(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🔍 Ключевые слова", callback_data="set_keywords")],
            [InlineKeyboardButton("🚫 Исключения", callback_data="set_exceptions")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "⚙️ **Настройки фильтров**\n\n"
            f"🔍 **Ключевые слова:** {', '.join(keywords) if keywords else 'не заданы'}\n"
            f"🚫 **Исключения:** {', '.join(exceptions) if exceptions else 'не заданы'}\n\n"
            "Выберите что хотите изменить:"
        )
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def set_keywords(self, query, context):
        context.user_data['state'] = 'waiting_keywords'
        await query.edit_message_text(
            "🔍 **Настройка ключевых слов**\n\nОтправьте список ключевых слов через запятую:\nПример: *Москва, работа, дом, машина*\n\n⚠️ *Сообщения будут проверяться без учета регистра*",
            parse_mode='Markdown'
        )
    
    async def save_keywords(self, update, text):
        user_id = update.effective_user.id
        keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
        
        _, exceptions = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        await self.session_manager.restart_session(user_id)
        
        await update.message.reply_text(f"✅ **Ключевые слова сохранены!**\n\nСписок: {', '.join(keywords)}\n\nВсего слов: {len(keywords)}")
    
    async def set_exceptions(self, query, context):
        context.user_data['state'] = 'waiting_exceptions'
        await query.edit_message_text(
            "🚫 **Настройка исключений**\n\nОтправьте список слов-исключений через запятую:\nПример: *Москве, работе, дома*\n\n⚠️ *Если в сообщении есть слово из исключений - оно будет проигнорировано*",
            parse_mode='Markdown'
        )
    
    async def save_exceptions(self, update, text):
        user_id = update.effective_user.id
        exceptions = [ex.strip() for ex in text.split(',') if ex.strip()]
        
        keywords, _ = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        await self.session_manager.restart_session(user_id)
        
        await update.message.reply_text(f"✅ **Исключения сохранены!**\n\nСписок: {', '.join(exceptions) if exceptions else 'нет исключений'}\n\nВсего исключений: {len(exceptions)}")
    
    async def show_status(self, query):
        user_id = query.from_user.id
        session_string = self.db.get_user_session(user_id)
        keywords, exceptions = self.db.get_user_settings(user_id)
        
        status = "🟢 Активен" if session_string else "🔴 Неактивен"
        
        text = (
            "📊 **Статус мониторинга**\n\n"
            f"🔄 Статус: {status}\n"
            f"🔍 Ключевых слов: {len(keywords)}\n"
            f"🚫 Исключений: {len(exceptions)}\n\n"
            f"*Сессия: {'✅ Загружена' if session_string else '❌ Отсутствует'}*"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_users(self, query):
        users = self.db.get_allowed_users()
        
        text = "👥 **Управление пользователями**\n\n"
        
        if not users:
            text += "Нет разрешенных пользователей."
        else:
            for user_id, username, added_at in users:
                text += f"🆔 {user_id} | @{username or 'нет'}\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить пользователя", callback_data="admin_add_user")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
        ]
        
        for user_id, username, _ in users:
            if user_id != query.from_user.id:
                keyboard.append([InlineKeyboardButton(f"❌ Удалить {user_id}", callback_data=f"admin_remove_user:{user_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_add_user_dialog(self, query, context):
        context.user_data['state'] = 'admin_waiting_user'
        await query.edit_message_text("➕ **Добавление пользователя**\n\nОтправьте user_id пользователя, которого хотите добавить:")
    
    async def admin_add_user(self, update, text):
        try:
            target_user_id = int(text.strip())
            admin_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            
            self.db.add_allowed_user(target_user_id, username, admin_id)
            await update.message.reply_text(f"✅ Пользователь {target_user_id} добавлен!")
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат user_id!")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    
    async def admin_remove_user(self, query, target_user_id):
        self.db.remove_allowed_user(target_user_id)
        await self.session_manager.stop_session(target_user_id)
        await query.edit_message_text(f"✅ Пользователь {target_user_id} удален!")
    
    async def admin_stats(self, query):
        users = self.db.get_allowed_users()
        active_sessions = len(self.session_manager.active_clients)
        
        text = (
            "📊 **Статистика системы**\n\n"
            f"👥 Всего пользователей: {len(users)}\n"
            f"🔄 Активных сессий: {active_sessions}\n"
            f"👑 Админов: {len(ADMINS)}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_restart(self, query):
        await self.session_manager.start_all_sessions()
        await query.edit_message_text("✅ Все сессии перезапущены!")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)

async def main():
    bot = MonitorBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
