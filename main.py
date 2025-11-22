import logging
import asyncio
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, filters
)
import json

from config import BOT_TOKEN, API_ID, API_HASH, ADMINS, WEBHOOK_URL, PORT
from database import Database
from session_manager import SessionManager

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

class MonitorBot:
    def __init__(self):
        self.db = Database("users_data.db")
        self.application = None
        self.session_manager = None
    
    async def start(self):
        """Запуск бота"""
        try:
            logger.info("Запуск бота...")
            
            # Создаем приложение
            self.application = Application.builder().token(BOT_TOKEN).build()
            self.session_manager = SessionManager(API_ID, API_HASH, self.db, self.application.bot)
            
            # Добавляем обработчики
            self.setup_handlers()
            
            # Запускаем сессии
            await self.session_manager.start_all_sessions()
            
            # Запускаем бота
            if WEBHOOK_URL:
                await self.start_webhook()
            else:
                await self.application.run_polling()
                
        except Exception as e:
            logger.error(f"Критическая ошибка при запуске: {e}")
            raise
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        
        # Сообщения
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Обработка ошибок
        self.application.add_error_handler(self.error_handler)
    
    async def start_webhook(self):
        """Запуск вебхука"""
        await self.application.bot.set_webhook(
            url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
        
        # Для Realway обычно используется порт из переменной окружения
        await self.application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user_id = update.effective_user.id
        
        if not self.db.is_user_allowed(user_id):
            await update.message.reply_text(
                "❌ Доступ запрещен.\n"
                "Обратитесь к администратору для получения доступа."
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📤 Загрузить сессию", callback_data="upload_session")],
            [InlineKeyboardButton("⚙️ Настройки фильтров", callback_data="settings")],
            [InlineKeyboardButton("📊 Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👋 Добро пожаловать в мониторинг Telegram!\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
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
        """Обработка текстовых сообщений"""
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
        """Обработка callback запросов"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        # Основные команды
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
            
        # Админ команды
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
        """Загрузка сессии"""
        context.user_data['state'] = 'waiting_session'
        await query.edit_message_text(
            "📤 **Загрузка сессии**\n\n"
            "Отправьте строку сессии в следующем сообщении.\n"
            "⚠️ *Внимание:* При повторной отправке старая сессия будет заменена.",
            parse_mode='Markdown'
        )
    
    async def save_session(self, update, session_string):
        """Сохранение сессии"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        try:
            # Проверяем валидность сессии
            from telethon.sessions import StringSession
            from telethon import TelegramClient
            
            client = TelegramClient(
                StringSession(session_string),
                API_ID,
                API_HASH
            )
            
            await client.start()
            me = await client.get_me()
            await client.disconnect()
            
            # Сохраняем сессию
            self.db.save_session(user_id, username, session_string)
            
            # Запускаем мониторинг
            await self.session_manager.start_session(user_id, session_string)
            
            await update.message.reply_text(
                f"✅ **Сессия успешно сохранена!**\n\n"
                f"👤 Аккаунт: {me.first_name or ''} (@{me.username or 'нет'})\n"
                f"🆔 ID: `{me.id}`\n\n"
                f"Теперь настройте фильтры для мониторинга.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await update.message.reply_text(
                f"❌ **Ошибка сохранения сессии:**\n{str(e)}"
            )
    
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
            "Выберите что хотите изменить:"
        )
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def set_keywords(self, query, context):
        """Установка ключевых слов"""
        context.user_data['state'] = 'waiting_keywords'
        await query.edit_message_text(
            "🔍 **Настройка ключевых слов**\n\n"
            "Отправьте список ключевых слов через запятую:\n"
            "Пример: *Москва, работа, дом, машина*\n\n"
            "⚠️ *Сообщения будут проверяться без учета регистра*",
            parse_mode='Markdown'
        )
    
    async def save_keywords(self, update, text):
        """Сохранение ключевых слов"""
        user_id = update.effective_user.id
        keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
        
        _, exceptions = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        await self.session_manager.restart_session(user_id)
        
        await update.message.reply_text(
            f"✅ **Ключевые слова сохранены!**\n\n"
            f"Список: {', '.join(keywords)}\n\n"
            f"Всего слов: {len(keywords)}"
        )
    
    async def set_exceptions(self, query, context):
        """Установка исключений"""
        context.user_data['state'] = 'waiting_exceptions'
        await query.edit_message_text(
            "🚫 **Настройка исключений**\n\n"
            "Отправьте список слов-исключений через запятую:\n"
            "Пример: *Москве, работе, дома*\n\n"
            "⚠️ *Если в сообщении есть слово из исключений - оно будет проигнорировано*",
            parse_mode='Markdown'
        )
    
    async def save_exceptions(self, update, text):
        """Сохранение исключений"""
        user_id = update.effective_user.id
        exceptions = [ex.strip() for ex in text.split(',') if ex.strip()]
        
        keywords, _ = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        await self.session_manager.restart_session(user_id)
        
        await update.message.reply_text(
            f"✅ **Исключения сохранены!**\n\n"
            f"Список: {', '.join(exceptions) if exceptions else 'нет исключений'}\n\n"
            f"Всего исключений: {len(exceptions)}"
        )
    
    async def show_status(self, query):
        """Показать статус"""
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
    
    # Админ функции
    async def admin_users(self, query):
        """Управление пользователями"""
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
                keyboard.append([InlineKeyboardButton(
                    f"❌ Удалить {user_id}", 
                    callback_data=f"admin_remove_user:{user_id}"
                )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_add_user_dialog(self, query, context):
        """Диалог добавления пользователя"""
        context.user_data['state'] = 'admin_waiting_user'
        await query.edit_message_text(
            "➕ **Добавление пользователя**\n\n"
            "Отправьте user_id пользователя, которого хотите добавить:"
        )
    
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
        await self.session_manager.stop_session(target_user_id)
        await query.edit_message_text(f"✅ Пользователь {target_user_id} удален!")
    
    async def admin_stats(self, query):
        """Статистика"""
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
        """Перезапуск всех сессий"""
        await self.session_manager.start_all_sessions()
        await query.edit_message_text("✅ Все сессии перезапущены!")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)

async def main():
    """Основная функция"""
    bot = MonitorBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
