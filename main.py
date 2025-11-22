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
        first_name = update.effective_user.first_name or "Нет имени"
        
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
            f"📛 **Имя:** {first_name}\n"
            f"👑 **Статус:** {status}\n"
            f"🔐 **В белом списке:** {'✅ ДА' if is_allowed else '❌ НЕТ'}\n"
            f"📋 **Админы:** {ADMINS}"
        )
        update.message.reply_text(debug_info, parse_mode='Markdown')
    
    def start_command(self, update: Update, context: CallbackContext):
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
            self.start_command(query, context)
        elif data == "admin_users":
            self.admin_users(query)
        elif data == "admin_stats":
            self.admin_stats(query)
        elif data == "admin_restart":
            self.admin_restart(query)
        elif data == "admin_back":
            self.admin_command(query, context)
        elif data == "admin_add_user":
            self.admin_add_user_dialog(query, context)
        elif data.startswith("admin_remove_user:"):
            target_user_id = int(data.split(":")[1])
            self.admin_remove_user(query, target_user_id)
    
    def upload_session(self, query, context):
        context.user_data['state'] = 'waiting_session'
        query.edit_message_text(
            "📤 **Загрузка сессии**\n\nОтправьте строку сессии в следующем сообщении.\n⚠️ При повторной отправке старая сессия будет заменена.",
            parse_mode='Markdown'
        )
    
    def save_session(self, update, session_string):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
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
            
            self.db.save_session(user_id, username, session_string)
            
            import threading
            def start_monitoring():
                try:
                    self.session_manager.start_session(user_id, session_string)
                except Exception as e:
                    logger.error(f"Ошибка запуска мониторинга: {e}")
            
            threading.Thread(target=start_monitoring, daemon=True).start()
            
            update.message.reply_text(
                f"✅ **Сессия сохранена!**\n\n"
                f"👤 Аккаунт: {me.first_name or ''}\n"
                f"📱 Username: @{me.username or 'нет'}\n"
                f"🆔 ID: `{me.id}`\n\n"
                f"Мониторинг запускается...\n"
                f"Теперь настройте фильтры.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сессии: {e}")
            update.message.reply_text(f"❌ **Ошибка:**\n`{str(e)}`", parse_mode='Markdown')
    
    def show_settings(self, query):
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
        context.user_data['state'] = 'waiting_keywords'
        query.edit_message_text(
            "🔍 **Настройка ключевых слов**\n\nОтправьте список слов через запятую:\nПример: Москва, работа, дом\n\nСообщения проверяются без учета регистра.",
            parse_mode='Markdown'
        )
    
    def save_keywords(self, update, text):
        user_id = update.effective_user.id
        keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
        
        _, exceptions = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        import threading
        def restart_monitoring():
            try:
                self.session_manager.restart_session(user_id)
            except Exception as e:
                logger.error(f"Ошибка перезапуска: {e}")
        
        threading.Thread(target=restart_monitoring, daemon=True).start()
        
        update.message.reply_text(f"✅ **Ключевые слова сохранены!**\n\nСписок: {', '.join(keywords)}\n\nВсего: {len(keywords)}")
    
    def set_exceptions(self, query, context):
        context.user_data['state'] = 'waiting_exceptions'
        query.edit_message_text(
            "🚫 **Настройка исключений**\n\nОтправьте список слов-исключений через запятую:\nПример: Москве, работе, дома\n\nЕсли в сообщении есть слово из исключений - оно будет проигнорировано.",
            parse_mode='Markdown'
        )
    
    def save_exceptions(self, update, text):
        user_id = update.effective_user.id
        exceptions = [ex.strip() for ex in text.split(',') if ex.strip()]
        
        keywords, _ = self.db.get_user_settings(user_id)
        self.db.save_keywords(user_id, keywords, exceptions)
        
        import threading
        def restart_monitoring():
            try:
                self.session_manager.restart_session(user_id)
            except Exception as e:
                logger.error(f"Ошибка перезапуска: {e}")
        
        threading.Thread(target=restart_monitoring, daemon=True).start()
        
        update.message.reply_text(f"✅ **Исключения сохранены!**\n\nСписок: {', '.join(exceptions) if exceptions else 'нет'}\n\nВсего: {len(exceptions)}")
    
    def show_status(self, query):
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
        context.user_data['state'] = 'admin_waiting_user'
        query.edit_message_text("➕ **Добавление пользователя**\n\nОтправьте user_id пользователя:")
    
    def admin_add_user(self, update, text):
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
        self.db.remove_allowed_user(target_user_id)
        self.session_manager.stop_session(target_user_id)
        query.edit_message_text(f"✅ Пользователь {target_user_id} удален!")
    
    def admin_stats(self, query):
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
        self.session_manager.start_all_sessions()
        query.edit_message_text("✅ Все сессии перезапущены!")
    
    def error_handler(self, update: Update, context: CallbackContext):
        logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)
