import os
from dotenv import load_dotenv

load_dotenv()

# Основные настройки
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', '2040'))
API_HASH = os.getenv('API_HASH', 'b18441a1ff607e10a989891a5462e627')

# Админы (через запятую)
ADMINS_STR = os.getenv('ADMINS', '')
ADMINS = [int(x.strip()) for x in ADMINS_STR.split(',') if x.strip()] if ADMINS_STR else []

# Настройки базы данных
DB_PATH = os.getenv('DB_PATH', 'users_data.db')

# Настройки вебхука (для Realway)
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
PORT = int(os.getenv('PORT', 8443))

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env файле")
