import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import events
import json

class SessionManager:
    def __init__(self, api_id, api_hash, database, bot):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = database
        self.bot = bot
        self.active_clients = {}
        
    async def start_all_sessions(self):
        """Запуск всех сессий"""
        users = self.db.get_all_active_users()
        for user_id, session_string, keywords_json, exceptions_json in users:
            await self.start_session(user_id, session_string)
    
    async def start_session(self, user_id, session_string):
        """Запуск одной сессии"""
        try:
            if user_id in self.active_clients:
                await self.active_clients[user_id].disconnect()
            
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash
            )
            
            await client.start()
            
            # Получаем настройки пользователя
            keywords, exceptions = self.db.get_user_settings(user_id)
            
            # Настраиваем обработчик сообщений
            @client.on(events.NewMessage)
            async def handler(event):
                await self.handle_message(user_id, event, keywords, exceptions)
            
            self.active_clients[user_id] = client
            logging.info(f"Сессия для пользователя {user_id} запущена")
            
        except Exception as e:
            logging.error(f"Ошибка запуска сессии для {user_id}: {e}")
            await self.bot.send_message(
                user_id, 
                f"❌ Ошибка запуска сессии: {str(e)}"
            )
    
    async def handle_message(self, user_id, event, keywords, exceptions):
        """Обработка сообщений"""
        try:
            message = event.message
            if not message.text:
                return
            
            text_lower = message.text.lower()
            
            # Проверка ключевых слов
            keyword_found = any(keyword.lower() in text_lower for keyword in keywords)
            if not keyword_found:
                return
            
            # Проверка исключений
            exception_found = any(exception.lower() in text_lower for exception in exceptions)
            if exception_found:
                return
            
            # Формируем информацию о сообщении
            sender = await event.get_sender()
            sender_username = f"@{sender.username}" if sender.username else "Нет username"
            sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or "Неизвестно"
            sender_id = sender.id
            
            # Форматируем сообщение для бота
            alert_message = (
                f"🔔 **Найдено совпадение!**\n\n"
                f"👤 **Пользователь:** {sender_username}\n"
                f"📛 **Ник:** {sender_name}\n"
                f"🆔 **ID:** `{sender_id}`\n"
                f"💬 **Текст:** {message.text}\n"
                f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Отправляем уведомление пользователю
            await self.bot.send_message(user_id, alert_message)
            
        except Exception as e:
            logging.error(f"Ошибка обработки сообщения: {e}")
    
    async def stop_session(self, user_id):
        """Остановка сессии"""
        if user_id in self.active_clients:
            await self.active_clients[user_id].disconnect()
            del self.active_clients[user_id]
    
    async def restart_session(self, user_id):
        """Перезапуск сессии"""
        session_string = self.db.get_user_session(user_id)
        if session_string:
            await self.start_session(user_id, session_string)
