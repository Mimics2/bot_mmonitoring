import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import events
import json

logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self, api_id, api_hash, database, bot):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = database
        self.bot = bot
        self.active_clients = {}
        
    async def start_all_sessions(self):
        """Запуск всех сессий"""
        try:
            users = self.db.get_all_active_users()
            logger.info(f"Найдено {len(users)} пользователей для запуска")
            
            for user_id, session_string, keywords_json, exceptions_json in users:
                await self.start_session(user_id, session_string)
                
        except Exception as e:
            logger.error(f"Ошибка запуска сессий: {e}")
    
    async def start_session(self, user_id, session_string):
        """Запуск одной сессии"""
        try:
            # Останавливаем существующую сессию
            if user_id in self.active_clients:
                try:
                    await self.active_clients[user_id].disconnect()
                except:
                    pass
            
            # Создаем нового клиента
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash
            )
            
            # Запускаем клиента
            await client.start()
            
            # Получаем настройки пользователя
            keywords, exceptions = self.db.get_user_settings(user_id)
            
            # Настраиваем обработчик сообщений
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
        """Обработка сообщений"""
        try:
            message = event.message
            if not message.text:
                return
            
            text_lower = message.text.lower()
            keywords_lower = [k.lower() for k in keywords]
            exceptions_lower = [e.lower() for e in exceptions]
            
            # Проверка ключевых слов
            keyword_found = any(keyword in text_lower for keyword in keywords_lower)
            if not keyword_found:
                return
            
            # Проверка исключений
            exception_found = any(exception in text_lower for exception in exceptions_lower)
            if exception_found:
                return
            
            # Формируем информацию о сообщении
            sender = await event.get_sender()
            sender_username = f"@{sender.username}" if sender and sender.username else "Нет username"
            sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or "Неизвестно"
            sender_id = sender.id if sender else "Неизвестно"
            
            # Форматируем сообщение для бота
            alert_message = (
                f"🔔 **Найдено совпадение!**\n\n"
                f"👤 **Пользователь:** {sender_username}\n"
                f"📛 **Ник:** {sender_name}\n"
                f"🆔 **ID:** `{sender_id}`\n"
                f"💬 **Текст:** {message.text[:500]}\n"
                f"📅 **Время:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Отправляем уведомление пользователю
            await self.bot.send_message(user_id, alert_message)
            logger.info(f"Отправлено уведомление пользователю {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
    
    async def stop_session(self, user_id):
        """Остановка сессии"""
        if user_id in self.active_clients:
            try:
                await self.active_clients[user_id].disconnect()
                del self.active_clients[user_id]
                logger.info(f"Сессия пользователя {user_id} остановлена")
            except Exception as e:
                logger.error(f"Ошибка остановки сессии {user_id}: {e}")
    
    async def restart_session(self, user_id):
        """Перезапуск сессии"""
        session_string = self.db.get_user_session(user_id)
        if session_string:
            await self.start_session(user_id, session_string)
