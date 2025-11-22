import logging
import asyncio
import sys
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, Filters
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
ADMINS = [int(x.strip()) for x in os.getenv('ADMINS', '').split(',') if x.strip()]

# Realway автоматически устанавливает PORT
PORT = int(os.getenv('PORT', 8443))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения Realway")

logger.info("Конфигурация загружена успешно")

# Остальной код остается без изменений...
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
            conn.commit()
    
    # ... остальные методы Database без изменений

class SessionManager:
    def __init__(self, api_id, api_hash, database, bot):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = database
        self.bot = bot
        self.active_clients = {}
    
    # ... методы SessionManager без изменений

class MonitorBot:
    def __init__(self):
        self.db = Database()
        self.application = None
        self.session_manager = None
    
    async def start(self):
        try:
            logger.info("Запуск бота на Realway...")
            
            self.application = Application.builder().token(BOT_TOKEN).build()
            self.session_manager = SessionManager(API_ID, API_HASH, self.db, self.application.bot)
            
            self.setup_handlers()
            await self.session_manager.start_all_sessions()
            
            # Для Realway используем polling (более стабильно)
            logger.info("Запуск в режиме polling...")
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
    
    # ... остальные методы без изменений

async def main():
    bot = MonitorBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
