import logging
import asyncio
import sys
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, filters
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

class TelegramMonitor:
    def __init__(self, user_id, session_string, keywords, exceptions, bot_application):
        self.user_id = user_id
        self.session_string = session_string
        self.keywords = keywords
        self.exceptions = exceptions
        self.bot_application = bot_application
        self.client = None
        self.is_running = False
    
    async def start(self):
        """Запуск мониторинга"""
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon import events
            
            self.client = TelegramClient(
                StringSession(self.session_string),
                API_ID,
                API_HASH
            )
            
            await self.client.start()
            
            # Настраиваем обработчик сообщений
            @self.client.on(events.NewMessage)
            async def handler(event):
                await self.handle_message(event)
            
            self.is_running = True
            logger.info(f"✅ Мониторинг запущен для пользователя {self.user_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска мониторинга для {self.user_id}: {e}")
            try:
                await self.bot_application.bot.send_message(
                    self.user_id,
                    f"❌ Ошибка запуска мониторинга: {str(e)}"
                )
            except:
                pass
    
    async def handle_message(self, event):
        """Обработка сообщений"""
        try:
            message = event.message
            if not message.text:
                return
            
            text_lower = message.text.lower()
            keywords_lower = [k.lower() for k in self.keywords]
            exceptions_lower = [e.lower() for e in self.exceptions]
            
            # Проверяем ключевые слова
            keyword_found = any(keyword in text_lower for keyword in keywords_lower)
            if not keyword_found:
                return
            
            # Проверяем исключения
            exception_found = any(exception in text_lower for exception in exceptions_lower)
            if exception_found:
                return
            
            # Получаем информацию об отправителе
            sender = await event.get_sender()
            sender_username = f"@{sender.username}" if sender and sender.username else "Нет username"
            sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or "Неизвестно"
            sender_id = sender.id if sender else "Неизвестно"
            
            # Получаем информацию о чате
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', '') or getattr(chat, 'username', '') or "Личные сообщения"
            
            # Формируем полное сообщение для пересылки
            full_message = (
                f"🔔 **Найдено совпадение!**\n\n"
                f"👤 **От:** {sender_username} ({sender_name})\n"
                f"🆔 **ID:** `{sender_id}`\n"
                f"📋 **Чат:** {chat_title}\n"
                f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💬 **Сообщение:**\n{message.text}"
            )
            
            # Отправляем сообщение через бота
            try:
                await self.bot_application.bot.send_message(
                    self.user_id, 
                    full_message, 
                    parse_mode='Markdown'
                )
                logger.info(f"📨 Сообщение переслано пользователю {self.user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сообщения: {e}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}")
    
    async def stop(self):
        """Остановка мониторинга"""
        if self.client and self.is_running:
            try:
                await self.client.disconnect()
                self.is_running = False
                logger.info(f"🛑 Мониторинг остановлен для пользователя {self.user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка остановки мониторинга: {e}")

class MonitorBot:
    def __init__(self):
        self.db = Database()
        self.application = None
        self.monitors = {}  # user_id -> TelegramMonitor
    
    async def start(self):
        """Запуск бота"""
        try:
            logger.info("🚀 Запуск бота...")
            
            # Создаем приложение
            self.application = Application.builder().token(BOT_TOKEN).build()
            
            # Настраиваем обработчики
            self.setup_handlers()
            
            # Запускаем существующие сессии
            await self.start_all_sessions()
            
            # Запускаем бота
            logger.info("🤖 Бот запущен")
            await self.application.run_polling()
                
        except Exception as e:
            logger.error(f"💥 Критическая ошибка при запуске: {e}")
            raise
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        self.application.add_handler(CommandHandler("debug", self.debug_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_error_handler(self.error_handler)
    
    async def start_all_sessions(self):
        """Запуск всех сессий"""
        users = self.db.get_all_active_users()
        logger.info(f"🔄 Найдено {len(users)} пользователей для запуска")
        
        for user_id, session_string, keywords_json, exceptions_json in users:
            keywords = json.loads(keywords_json) if keywords_json else []
            exceptions = json.loads(exceptions_json) if exceptions_json else []
            await self.start_session(user_id, session_string, keywords, exceptions)
    
    async def start_session(self, user_id, session_string, keywords, exceptions):
        """Запуск одной сессии"""
        try:
            # Останавливаем существующую сессию
            if user_id in self.monitors:
                await self.monitors[user_id].stop()
                del self.monitors[user_id]
            
            # Создаем новый монитор
            monitor = TelegramMonitor(user_id, session_string, keywords, exceptions, self.application)
            await monitor.start()
            self.monitors[user_id] = monitor
            
            logger.info(f"✅ Сессия запущена для {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сессии для {user_id}: {e}")
    
    async def stop_session(self, user_id):
        """Остановка сессии"""
        if user_id in self.monitors:
            await self.monitors[user_id].stop()
            del self.monitors[user_id]
            logger.info(f"🛑 Сессия остановлена для {user_id}")
    
    async def debug_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для отладки"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Нет username"
        first_name = update.effective_user.first_name or "Нет имени"
        
        if user_id in ADMINS:
            self.db.add_allowed_user(user_id, username, user_id)
            status = "✅ АДМИН"
        else:
            status = "❌ НЕ АДМИН"
        
        is_allowed = self.db.is_user_allowed(user_id)
        monitor_status = "🟢 Запущен" if user_id in self.monitors else "🔴 Остановлен"
        
        debug_info = (
            f"🔧 **Отладка:**\n\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"👤 **Username:** @{username}\n"
            f"📛 **Имя:** {first_name}\n"
            f"👑 **Статус:** {status}\n"
            f"🔐 **В белом списке:** {'✅ ДА' if is_allowed else '❌ НЕТ'}\n"
            f"📡 **Мониторинг:** {monitor_status}\n"
            f"📋 **Админы:** {ADMINS}"
        )
        await update.message.reply_text(debug_info, parse_mode='Markdown')
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"📩 /start от {user_id}")
        
        if user_id in ADMINS:
            self.db.add_allowed_user(user_id, username, user_id)
        
        if not self.db.is_user_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещен. Используйте /debug")
            return
        
        keyboard = [
            [InlineKeyboardButton("📤 Загрузить сессию", callback_data="upload_session")],
            [InlineKeyboardButton("⚙️ Настройки фильтров", callback_data="settings")],
            [InlineKeyboardButton("📊 Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 **Добро пожаловать в мониторинг Telegram!**\n\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /admin"""
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
        """Обработчик текстовых сообщений"""
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
        """Обработчик callback запросов"""
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
            await self.start_command(update, context)
        elif data == "admin_users":
            await self.admin_users(query)
        elif data == "admin_stats":
            await self.admin_stats(query)
        elif data == "admin_restart":
            await self.admin_restart(query)
        elif data == "admin_back":
            await self.admin_command(update, context)
        elif data == "admin_add_user":
            await self.admin_add_user_dialog(query, context)
        elif data.startswith("admin_remove_user:"):
            target_user_id = int(data.split(":")[1])
            await self.admin_remove_user(query, target_user_id)
    
    async def upload_session(self, query, context):
        """Загрузка сессии"""
        context.user_data['state'] = 'waiting_session'
        await query.edit_message_text(
            "📤 **Загрузка сессии**\n\nОтправьте строку сессии в следующем сообщении.\n⚠️ При повторной отправке старая сессия будет заменена.",
            parse_mode='Markdown'
        )
    
    async def save_session(self, update, session_string):
        """Сохранение сессии"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
            # Проверяем сессию
            async def test_session():
                client = TelegramClient(
                    StringSession(session_string),
                    API_ID,
                    API_HASH
                )
                await client.start()
                me = await client.get_me()
                await client.disconnect()
                return me
            
            me = await test_session()
            
            # Сохраняем в базу
            self.db.save_session(user_id, username, session_string)
            
            # Получаем настройки и запускаем мониторинг
            keywords, exceptions = self.db.get_user_settings(user_id)
            await self.start_session(user_id, session_string, keywords, exceptions)
            
            await update.message.reply_text(
                f"✅ **Сессия сохранена!**\n\n"
                f"👤 Аккаунт: {me.first_name or ''}\n"
                f"📱 Username: @{me.username or 'нет'}\n"
                f"🆔 ID: `{me.id}`\n\n"
                f"Мониторинг запущен!\n"
                f"Теперь настройте фильтры.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сессии: {e}")
            await update.message.reply_text(f"❌ **Ошибка:**\n`{str(e)}`", parse_mode='Markdown')
    
    async def show_settings(self, query):
        """Показать настройки"""
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
            "Выберите что изменить:"
        )
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def set_keywords(self, query, context):
        """Установка ключевых слов"""
        context.user_data['state'] = 'waiting_keywords'
        await query.edit_message_text(
            "🔍 **Настройка ключевых слов**\n\nОтправьте список слов через запятую:\nПример: Москва, работа, дом\n\nСообщения проверяются без учета регистра.",
            parse_mode='Markdown'
        )
    
    async def save_keywords(self, update, text):
        """Сохранение ключевых слов"""
        user_id = update.effective_user.id
        keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
        
        _, exceptions = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        # Перезапускаем сессию с новыми настройками
        session_string = self.db.get_user_session(user_id)
        if session_string:
            await self.start_session(user_id, session_string, keywords, exceptions)
        
        await update.message.reply_text(f"✅ **Ключевые слова сохранены!**\n\nСписок: {', '.join(keywords)}\n\nВсего: {len(keywords)}")
    
    async def set_exceptions(self, query, context):
        """Установка исключений"""
        context.user_data['state'] = 'waiting_exceptions'
        await query.edit_message_text(
            "🚫 **Настройка исключений**\n\nОтправьте список слов-исключений через запятую:\nПример: Москве, работе, дома\n\nЕсли в сообщении есть слово из исключений - оно будет проигнорировано.",
            parse_mode='Markdown'
        )
    
    async def save_exceptions(self, update, text):
        """Сохранение исключений"""
        user_id = update.effective_user.id
        exceptions = [ex.strip() for ex in text.split(',') if ex.strip()]
        
        keywords, _ = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        # Перезапускаем сессию с новыми настройками
        session_string = self.db.get_user_session(user_id)
        if session_string:
            await self.start_session(user_id, session_string, keywords, exceptions)
        
        await update.message.reply_text(f"✅ **Исключения сохранены!**\n\nСписок: {', '.join(exceptions) if exceptions else 'нет'}\n\nВсего: {len(exceptions)}")
    
    async def show_status(self, query):
        """Показать статус"""
        user_id = query.from_user.id
        session_string = self.db.get_user_session(user_id)
        keywords, exceptions = self.db.get_user_settings(user_id)
        
        status = "🟢 Активен" if session_string else "🔴 Неактивен"
        monitoring = "🟢 Запущен" if user_id in self.monitors else "🔴 Не запущен"
        
        text = (
            "📊 **Статус мониторинга**\n\n"
            f"🔄 Статус: {status}\n"
            f"📡 Мониторинг: {monitoring}\n"
            f"🔍 Ключевых слов: {len(keywords)}\n"
            f"🚫 Исключений: {len(exceptions)}\n\n"
            f"Сессия: {'✅ Загружена' if session_string else '❌ Отсутствует'}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_users(self, query):
        """Управление пользователями"""
        users = self.db.get_allowed_users()
        
        text = "👥 **Управление пользователями**\n\n"
        if not users:
            text += "Нет пользователей."
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
        """Диалог добавления пользователя"""
        context.user_data['state'] = 'admin_waiting_user'
        await query.edit_message_text("➕ **Добавление пользователя**\n\nОтправьте user_id пользователя:")
    
    async def admin_add_user(self, update, text):
        """Добавление пользователя"""
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
        """Удаление пользователя"""
        self.db.remove_allowed_user(target_user_id)
        await self.stop_session(target_user_id)
        await query.edit_message_text(f"✅ Пользователь {target_user_id} удален!")
    
    async def admin_stats(self, query):
        """Статистика системы"""
        users = self.db.get_allowed_users()
        active_sessions = len(self.monitors)
        
        text = (
            "📊 **Статистика системы**\n\n"
            f"👥 Пользователей: {len(users)}\n"
            f"🔄 Активных сессий: {active_sessions}\n"
            f"👑 Админов: {len(ADMINS)}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_restart(self, query):
        """Перезапуск всех сессий"""
        await self.start_all_sessions()
        await query.edit_message_text("✅ Все сессии перезапущены!")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)

async def main():
    """Основная функция"""
    bot = MonitorBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
