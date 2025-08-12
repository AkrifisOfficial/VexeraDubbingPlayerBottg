import os
import logging
import sys
import asyncio
from aiohttp import web
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
required_vars = ['BOT_TOKEN', 'ADMIN_CHAT_IDS', 'DATABASE_URL']
missing_vars = [var for var in required_vars if var not in os.environ]
if missing_vars:
    logger.critical(f"Missing environment variables: {', '.join(missing_vars)}")
    sys.exit(1)

BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_CHAT_IDS = [int(id.strip()) for id in os.environ['ADMIN_CHAT_IDS'].split(',')]
DATABASE_URL = os.environ['DATABASE_URL']

logger.info("Environment variables loaded successfully")

# Глобальные переменные
app_queue = asyncio.Queue()
app_lock = asyncio.Lock()

# Инициализация базы данных
async def init_db():
    try:
        pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        logger.info("Database connection pool created")
        
        # Создание таблиц, если их нет
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    id VARCHAR(20) PRIMARY KEY,
                    status VARCHAR(10) NOT NULL CHECK (status IN ('received', 'pending', 'approved', 'rejected')),
                    application_data TEXT NOT NULL,
                    telegram_username VARCHAR(100),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    application_id VARCHAR(20) REFERENCES applications(id) ON DELETE CASCADE,
                    chat_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (application_id, chat_id)
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS counters (
                    name VARCHAR(50) PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                )
            ''')
            
            # Инициализация счетчика
            await conn.execute('''
                INSERT INTO counters (name, value)
                VALUES ('application_counter', 0)
                ON CONFLICT (name) DO NOTHING
            ''')
            
            logger.info("Database tables verified/created")
        
        return pool
    except Exception as e:
        logger.critical(f"Database initialization failed: {str(e)}")
        sys.exit(1)

# Проверка прав администратора
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_CHAT_IDS

async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE, func) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        logger.warning(f"Unauthorized access attempt by user: {user_id}")
        await update.message.reply_text("🚫 У вас нет прав для выполнения этой команды")
        return
    await func(update, context)

# Генерация ID заявки
async def get_next_application_id(pool) -> str:
    async with pool.acquire() as conn:
        counter = await conn.fetchval('''
            UPDATE counters 
            SET value = value + 1 
            WHERE name = 'application_counter'
            RETURNING value
        ''')
        return f"APP-{counter:04d}"

# Сохранение заявки в БД
async def save_application(pool, app_id, status, data, telegram):
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO applications (id, status, application_data, telegram_username)
            VALUES ($1, $2, $3, $4)
        ''', app_id, status, data, telegram)

# Сохранение сообщения в БД
async def save_message(pool, app_id, chat_id, message_id):
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO messages (application_id, chat_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (application_id, chat_id) DO UPDATE
            SET message_id = $3
        ''', app_id, chat_id, message_id)

# Получение данных заявки
async def get_application(pool, app_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow('''
            SELECT * FROM applications WHERE id = $1
        ''', app_id)

# Получение сообщений заявки
async def get_application_messages(pool, app_id):
    async with pool.acquire() as conn:
        return await conn.fetch('''
            SELECT chat_id, message_id FROM messages WHERE application_id = $1
        ''', app_id)

# Обновление статуса заявки
async def update_application_status(pool, app_id, status):
    async with pool.acquire() as conn:
        await conn.execute('''
            UPDATE applications 
            SET status = $1, updated_at = NOW()
            WHERE id = $2
        ''', status, app_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"Command /start from user: {user.id}")
    
    if is_admin(user.id):
        response = (
            f"👋 Привет, администратор {user.first_name}!\n\n"
            "🔧 Доступные команды:\n"
            "/list - список заявок\n"
            "/review [ID] - просмотреть заявку\n"
            "/approve [ID] - принять заявку\n"
            "/reject [ID] - отклонить заявку\n"
            "/help - справка по командам"
        )
    else:
        response = (
            f"👋 Привет, {user.first_name}!\n"
            "Это служебный бот для управления заявками VexeraDubbing.\n\n"
            "❌ У вас нет прав для использования этого бота.\n"
            "Обратитесь к администратору для получения доступа."
        )
    
    await update.message.reply_text(response)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_only(update, context, _help_command)

async def _help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠️ Команды администратора:\n\n"
        "/list - показать активные заявки\n"
        "/review [ID] - просмотреть заявку\n"
        "/approve [ID] - принять заявку\n"
        "/reject [ID] - отклонить заявку\n\n"
        "Заявки автоматически поступают с сайта проекта!"
    )
    await update.message.reply_text(help_text)

async def http_application_handler(request: web.Request) -> web.Response:
    pool = request.app['pool']
    try:
        data = await request.json()
        logger.info(f"Received application from website")
        
        # Генерация и сохранение заявки
        async with app_lock:
            app_id = await get_next_application_id(pool)
            await save_application(
                pool,
                app_id,
                "received",
                data.get('application_data', ''),
                data.get('telegram', 'N/A')
            )
        
        # Помещаем в очередь для обработки
        await app_queue.put((app_id, "website"))
        
        return web.json_response({
            "status": "success",
            "application_id": app_id,
            "message": "Application received and will be processed shortly"
        })
        
    except Exception as e:
        logger.error(f"Error in HTTP handler: {str(e)}", exc_info=True)
        return web.json_response({
            "status": "error",
            "message": f"Internal server error: {str(e)}"
        }, status=500)

async def process_http_applications(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting application processor")
    pool = context.bot_data['db_pool']
    
    while True:
        try:
            app_id, source = await app_queue.get()
            logger.info(f"Processing application: {app_id} from {source}")
            
            app = await get_application(pool, app_id)
            if not app:
                logger.warning(f"Application not found in DB: {app_id}")
                continue
                
            keyboard = [
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f"approve_{app_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}")
                ],
                [
                    InlineKeyboardButton("👀 Подробнее", callback_data=f"view_{app_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            source_prefix = "🌐" if source == "website" else "📬"
            
            # Отправка уведомлений администраторам
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    message = await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"{source_prefix} *Новая заявка* `{app_id}`\n_Используйте кнопки для управления_",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                    # Сохраняем ID сообщения
                    await save_message(pool, app_id, admin_id, message.message_id)
                except Exception as e:
                    logger.error(f"Error sending to admin {admin_id}: {str(e)}")
            
            # Обновляем статус заявки
            await update_application_status(pool, app_id, "pending")
            logger.info(f"Application processed: {app_id}")
            
        except Exception as e:
            logger.error(f"Error in application processor: {str(e)}", exc_info=True)

async def handle_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data['db_pool']
    
    try:
        logger.info("Received potential application message")
        
        if "НОВАЯ ЗАЯВКА В VEXERADUBBING" not in update.message.text:
            logger.debug("Message doesn't contain application marker, skipping")
            return
            
        logger.info("Processing new application...")
        
        # Генерация и сохранение заявки
        async with app_lock:
            app_id = await get_next_application_id(pool)
            telegram = next(
                (line.split(': ')[1] for line in update.message.text.split('\n') if "Telegram" in line), 
                "N/A"
            )
            
            await save_application(
                pool,
                app_id,
                "pending",
                update.message.text,
                telegram
            )
        
        # Помещаем в очередь для обработки
        await app_queue.put((app_id, "telegram"))
        
        logger.info(f"New application processed: {app_id}")
        
    except Exception as e:
        logger.error(f"Error handling application: {str(e)}", exc_info=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pool = context.bot_data['db_pool']
    
    # Проверка прав
    if not is_admin(query.from_user.id):
        logger.warning(f"Unauthorized button press by user: {query.from_user.id}")
        await query.edit_message_text("🚫 У вас нет прав для выполнения этого действия")
        return
    
    try:
        action, app_id = query.data.split('_', 1)
        logger.info(f"Button action: {action} for application: {app_id}")
        
        app = await get_application(pool, app_id)
        if not app:
            await query.edit_message_text(text="⚠️ Заявка не найдена!")
            logger.warning(f"Application not found: {app_id}")
            return
        
        if action == "view":
            await query.edit_message_text(
                text=f"📄 *Заявка {app_id}:*\n\n{app['application_data']}",
                reply_markup=query.message.reply_markup,
                parse_mode='Markdown'
            )
            return
        
        if action == "approve":
            new_status = "approved"
            new_text = f"✅ *Заявка ПРИНЯТА* `{app_id}`\n\n{app['application_data']}"
        elif action == "reject":
            new_status = "rejected"
            new_text = f"❌ *Заявка ОТКЛОНЕНА* `{app_id}`\n\n{app['application_data']}"
        else:
            logger.warning(f"Unknown action: {action}")
            return
        
        # Обновляем статус в БД
        await update_application_status(pool, app_id, new_status)
        
        # Обновляем сообщение
        await query.edit_message_text(
            text=new_text,
            parse_mode='Markdown'
        )
        
        # Обновляем сообщения у других администраторов
        messages = await get_application_messages(pool, app_id)
        for msg in messages:
            if msg['chat_id'] != query.from_user.id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=msg['chat_id'],
                        message_id=msg['message_id'],
                        text=new_text,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Error updating message for admin {msg['chat_id']}: {str(e)}")
        
        logger.info(f"Application {app_id} {action}ed")
        
    except Exception as e:
        logger.error(f"Error in button handler: {str(e)}", exc_info=True)

async def list_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_only(update, context, _list_applications)

async def _list_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data['db_pool']
    try:
        async with pool.acquire() as conn:
            records = await conn.fetch('''
                SELECT id, status FROM applications
                ORDER BY created_at DESC
            ''')
        
        if not records:
            await update.message.reply_text("📭 Нет активных заявок!")
            return
        
        status_labels = {
            "received": "🔵 Получена",
            "pending": "🟡 Ожидает",
            "approved": "🟢 Принята",
            "rejected": "🔴 Отклонена"
        }
        
        response = "📋 *Список заявок:*\n\n"
        for record in records:
            status = status_labels.get(record['status'], record['status'])
            response += f"• `{record['id']}` - {status}\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in list_applications: {str(e)}", exc_info=True)

async def review_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_only(update, context, _review_application)

async def _review_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data['db_pool']
    try:
        if not context.args:
            await update.message.reply_text("ℹ️ Использование: /review <ID заявки>")
            return
            
        app_id = context.args[0].upper()
        app = await get_application(pool, app_id)
        
        if not app:
            await update.message.reply_text(f"⚠️ Заявка `{app_id}` не найдена", parse_mode='Markdown')
            return
            
        status_labels = {
            "received": "🔵 Получена",
            "pending": "🟡 Ожидает",
            "approved": "🟢 Принята",
            "rejected": "🔴 Отклонена"
        }
        status = status_labels.get(app['status'], app['status'])
        
        response = (
            f"📄 *Заявка {app_id}*\n"
            f"Статус: {status}\n\n"
            f"{app['application_data']}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{app_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(response, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in review_application: {str(e)}", exc_info=True)

async def approve_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_only(update, context, _approve_application)

async def _approve_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _process_application_action(update, context, "approve")

async def reject_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_only(update, context, _reject_application)

async def _reject_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _process_application_action(update, context, "reject")

async def _process_application_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    pool = context.bot_data['db_pool']
    try:
        if not context.args:
            await update.message.reply_text(f"ℹ️ Использование: /{action} <ID заявки>")
            return
            
        app_id = context.args[0].upper()
        app = await get_application(pool, app_id)
        
        if not app:
            await update.message.reply_text(f"⚠️ Заявка `{app_id}` не найдена", parse_mode='Markdown')
            return
            
        if action == "approve":
            new_status = "approved"
            status_text = "ПРИНЯТА"
            status_emoji = "✅"
        else:
            new_status = "rejected"
            status_text = "ОТКЛОНЕНА"
            status_emoji = "❌"
        
        # Обновляем статус в БД
        await update_application_status(pool, app_id, new_status)
        new_text = f"{status_emoji} *Заявка {status_text}* `{app_id}`\n\n{app['application_data']}"
        
        # Обновляем все сообщения
        messages = await get_application_messages(pool, app_id)
        for msg in messages:
            try:
                await context.bot.edit_message_text(
                    chat_id=msg['chat_id'],
                    message_id=msg['message_id'],
                    text=new_text,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error updating message for admin {msg['chat_id']}: {str(e)}")
        
        await update.message.reply_text(f"{status_emoji} Заявка `{app_id}` успешно {status_text.lower()}!", parse_mode='Markdown')
        logger.info(f"Application {app_id} {action}d via command")
        
    except Exception as e:
        logger.error(f"Error in {action}_application: {str(e)}", exc_info=True)

async def start_http_server(application):
    app = web.Application()
    app['pool'] = application.bot_data['db_pool']
    app.router.add_post('/submit_application', http_application_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("HTTP server started on port 8080")
    return runner

async def main():
    logger.info("===== Starting VexeraDubbing Bot =====")
    
    try:
        # Инициализация базы данных
        db_pool = await init_db()
        
        # Создание приложения бота
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        application.bot_data['db_pool'] = db_pool
        
        # Регистрация обработчиков
        handlers = [
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("list", list_applications),
            CommandHandler("list", list_applications),
            CommandHandler("review", review_application),
            CommandHandler("approve", approve_application),
            CommandHandler("reject", reject_application),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_application),
            CallbackQueryHandler(button_handler)
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        # Запуск фоновых задач
        application.job_queue.run_once(
            lambda ctx: asyncio.create_task(process_http_applications(ctx)),
            when=0
        )
        
        application.job_queue.run_once(
            lambda ctx: asyncio.create_task(start_http_server(application)),
            when=0
        )
        
        # Запуск бота
        logger.info("Starting bot polling...")
        await application.run_polling()
        
    except Exception as e:
        logger.critical(f"Failed to start bot: {str(e)}", exc_info=True)
        # Перезапуск через 30 секунд
        import time
        time.sleep(30)
        await main()

if __name__ == '__main__':
    asyncio.run(main())
