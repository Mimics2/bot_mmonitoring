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
        """Запуск всех сессий"""
        try:
            users = self.db.get_all_active_users()
            logger.info(f"🔄 Найдено {len(users)} пользователей для запуска")
            
            for user_id, session_string, keywords_json, exceptions_json in users:
                self.start_session(user_id, session_string)
                
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сессий: {e}")
    
    def start_session(self, user_id, session_string):
        """Запуск одной сессии"""
        try:
            # Останавливаем существующую сессию если есть
            if user_id in self.active_clients:
                self.stop_session(user_id)
            
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon import events
            
            # Создаем отдельный loop для этой сессии
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash,
                loop=loop
            )
            
            # Запускаем клиента
            loop.run_until_complete(client.start())
            
            # Получаем настройки пользователя
            keywords, exceptions = self.db.get_user_settings(user_id)
            
            # Настраиваем обработчик
            @client.on(events.NewMessage)
            async def handler(event):
                await self.handle_message(user_id, event, keywords, exceptions)
            
            self.active_clients[user_id] = {
                'client': client,
                'loop': loop
            }
            
            logger.info(f"✅ Сессия для {user_id} запущена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сессии для {user_id}: {e}")
    
    async def handle_message(self, user_id, event, keywords, exceptions):
        """Обработка сообщений"""
        try:
            message = event.message
            if not message.text:
                return
            
            text_lower = message.text.lower()
            keywords_lower = [k.lower() for k in keywords]
            exceptions_lower = [e.lower() for e in exceptions]
            
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
                self.bot.send_message(user_id, full_message, parse_mode='Markdown')
                logger.info(f"📨 Сообщение переслано пользователю {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сообщения: {e}")
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
        """Остановка сессии"""
        if user_id in self.active_clients:
            try:
                client_data = self.active_clients[user_id]
                client = client_data['client']
                loop = client_data['loop']
                
                # Останавливаем клиента
                if client.is_connected():
                    loop.run_until_complete(client.disconnect())
                
                # Закрываем loop
                if not loop.is_closed():
                    loop.stop()
                    loop.close()
                
                del self.active_clients[user_id]
                logger.info(f"🛑 Сессия {user_id} остановлена")
                
            except Exception as e:
                logger.error(f"❌ Ошибка остановки сессии {user_id}: {e}")
                # Принудительно удаляем даже при ошибке
                if user_id in self.active_clients:
                    del self.active_clients[user_id]
    
    def restart_session(self, user_id):
        """Перезапуск сессии"""
        session_string = self.db.get_user_session(user_id)
        if session_string:
            self.start_session(user_id, session_string)

class MonitorBot:
    def __init__(self):
        self.db = Database()
        self.updater = None
        self.session_manager = None
    
    def start(self):
        """Запуск бота"""
        try:
            logger.info("🚀 Запуск бота...")
            
            # Создаем Updater
            self.updater = Updater(BOT_TOKEN, use_context=True)
            self.session_manager = SessionManager(API_ID, API_HASH, self.db, self.updater.bot)
            
            # Настраиваем обработчики
            self.setup_handlers()
            
            # Запускаем существующие сессии
            self.session_manager.start_all_sessions()
            
            # Запускаем бота
            logger.info("🤖 Бот запущен")
            self.updater.start_polling()
            self.updater.idle()
                
        except Exception as e:
            logger.error(f"💥 Критическая ошибка при запуске: {e}")
            raise
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        dp = self.updater.dispatcher
        
        dp.add_handler(CommandHandler("start", self.start_command))
        dp.add_handler(CommandHandler("admin", self.admin_command))
        dp.add_handler(CommandHandler("debug", self.debug_command))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        dp.add_handler(CallbackQueryHandler(self.handle_callback))
        dp.add_error_handler(self.error_handler)
    
    def debug_command(self, update: Update, context: CallbackContext):
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
        monitor_status = "🟢 Запущен" if user_id in self.session_manager.active_clients else "🔴 Остановлен"
        
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
        update.message.reply_text(debug_info, parse_mode='Markdown')
    
    def start_command(self, update: Update, context: CallbackContext):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"📩 /start от {user_id}")
        
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
        update.message.reply_text(
            "👋 **Добро пожаловать в мониторинг Telegram!**\n\nВыберите действие:",
            reply_markup=reply_markup
        )
    
    def admin_command(self, update: Update, context: CallbackContext):
        """Обработчик команды /admin"""
        user_id = update.effective_user.id
        
        if user_id not in ADMINS:
            update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        keyboard = [
            [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("🔄 Перезапуск сессий", callback_data="admin_restart")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            "🛠️ **Админ панель**\n\nВыберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    def handle_message(self, update: Update, context: CallbackContext):
        """Обработчик текстовых сообщений"""
        user_id = update.effective_user.id
        text = update.message.text
        
        if not self.db.is_user_allowed(user_id):
            return
        
        user_state = context.user_data.get('state')
        
        if user_state == 'waiting_session':
            self.save_session(update, text)
            context.user_data['state'] = None
        elif user_state == 'waiting_keywords':
            self.save_keywords(update, text)
            context.user_data['state'] = None
        elif user_state == 'waiting_exceptions':
            self.save_exceptions(update, text)
            context.user_data['state'] = None
        elif user_state == 'admin_waiting_user':
            self.admin_add_user(update, text)
            context.user_data['state'] = None
    
    def handle_callback(self, update: Update, context: CallbackContext):
        """Обработчик callback запросов"""
        query = update.callback_query
        query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data == "upload_session":
            self.upload_session(query, context)
        elif data == "settings":
            self.show_settings(query)
        elif data == "status":
            self.show_status(query)
        elif data == "set_keywords":
            self.set_keywords(query, context)
        elif data == "set_exceptions":
            self.set_exceptions(query, context)
        elif data == "back_to_main":
            self.start_command(update, context)
        elif data == "admin_users":
            self.admin_users(query)
        elif data == "admin_stats":
            self.admin_stats(query)
        elif data == "admin_restart":
            self.admin_restart(query)
        elif data == "admin_back":
            self.admin_command(update, context)
        elif data == "admin_add_user":
            self.admin_add_user_dialog(query, context)
        elif data.startswith("admin_remove_user:"):
            target_user_id = int(data.split(":")[1])
            self.admin_remove_user(query, target_user_id)
    
    def upload_session(self, query, context):
        """Загрузка сессии"""
        context.user_data['state'] = 'waiting_session'
        query.edit_message_text(
            "📤 **Загрузка сессии**\n\nОтправьте строку сессии в следующем сообщении.\n⚠️ При повторной отправке старая сессия будет заменена.",
            parse_mode='Markdown'
        )
    
    def save_session(self, update, session_string):
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
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            me = loop.run_until_complete(test_session())
            loop.close()
            
            # Сохраняем в базу
            self.db.save_session(user_id, username, session_string)
            
            # Запускаем мониторинг
            self.session_manager.start_session(user_id, session_string)
            
            update.message.reply_text(
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
            update.message.reply_text(f"❌ **Ошибка:**\n`{str(e)}`", parse_mode='Markdown')
    
    def show_settings(self, query):
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
        
        query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    def set_keywords(self, query, context):
        """Установка ключевых слов"""
        context.user_data['state'] = 'waiting_keywords'
        query.edit_message_text(
            "🔍 **Настройка ключевых слов**\n\nОтправьте список слов через запятую:\nПример: Москва, работа, дом\n\nСообщения проверяются без учета регистра.",
            parse_mode='Markdown'
        )
    
    def save_keywords(self, update, text):
        """Сохранение ключевых слов"""
        user_id = update.effective_user.id
        keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
        
        _, exceptions = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        # Перезапускаем сессию с новыми настройками
        self.session_manager.restart_session(user_id)
        
        update.message.reply_text(f"✅ **Ключевые слова сохранены!**\n\nСписок: {', '.join(keywords)}\n\nВсего: {len(keywords)}")
    
    def set_exceptions(self, query, context):
        """Установка исключений"""
        context.user_data['state'] = 'waiting_exceptions'
        query.edit_message_text(
            "🚫 **Настройка исключений**\n\nОтправьте список слов-исключений через запятую:\nПример: Москве, работе, дома\n\nЕсли в сообщении есть слово из исключений - оно будет проигнорировано.",
            parse_mode='Markdown'
        )
    
    def save_exceptions(self, update, text):
        """Сохранение исключений"""
        user_id = update.effective_user.id
        exceptions = [ex.strip() for ex in text.split(',') if ex.strip()]
        
        keywords, _ = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        # Перезапускаем сессию с новыми настройками
        self.session_manager.restart_session(user_id)
        
        update.message.reply_text(f"✅ **Исключения сохранены!**\n\nСписок: {', '.join(exceptions) if exceptions else 'нет'}\n\nВсего: {len(exceptions)}")
    
    def show_status(self, query):
        """Показать статус"""
        user_id = query.from_user.id
        session_string = self.db.get_user_session(user_id)
        keywords, exceptions = self.db.get_user_settings(user_id)
        
        status = "🟢 Активен" if session_string else "🔴 Неактивен"
        monitoring = "🟢 Запущен" if user_id in self.session_manager.active_clients else "🔴 Не запущен"
        
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
        query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    def admin_users(self, query):
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
        query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    def admin_add_user_dialog(self, query, context):
        """Диалог добавления пользователя"""
        context.user_data['state'] = 'admin_waiting_user'
        query.edit_message_text("➕ **Добавление пользователя**\n\nОтправьте user_id пользователя:")
    
    def admin_add_user(self, update, text):
        """Добавление пользователя"""
        try:
            target_user_id = int(text.strip())
            admin_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            
            self.db.add_allowed_user(target_user_id, username, admin_id)
            update.message.reply_text(f"✅ Пользователь {target_user_id} добавлен!")
        except ValueError:
            update.message.reply_text("❌ Неверный формат user_id!")
        except Exception as e:
            update.message.reply_text(f"❌ Ошибка: {str(e)}")
    
    def admin_remove_user(self, query, target_user_id):
        """Удаление пользователя"""
        self.db.remove_allowed_user(target_user_id)
        self.session_manager.stop_session(target_user_id)
        query.edit_message_text(f"✅ Пользователь {target_user_id} удален!")
    
    def admin_stats(self, query):
        """Статистика системы"""
        users = self.db.get_allowed_users()
        active_sessions = len(self.session_manager.active_clients)
        
        text = (
            "📊 **Статистика системы**\n\n"
            f"👥 Пользователей: {len(users)}\n"
            f"🔄 Активных сессий: {active_sessions}\n"
            f"👑 Админов: {len(ADMINS)}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    def admin_restart(self, query):
        """Перезапуск всех сессий"""
        self.session_manager.start_all_sessions()
        query.edit_message_text("✅ Все сессии перезапущены!")
    
    def error_handler(self, update: Update, context: CallbackContext):
        """Обработчик ошибок"""
        logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)

def main():
    """Основная функция"""
    bot = MonitorBot()
    bot.start()

if __name__ == "__main__":
    main()
