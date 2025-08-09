import os
import logging
import sys
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
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']
    ADMIN_CHAT_IDS = [int(id.strip()) for id in os.environ['ADMIN_CHAT_IDS'].split(',')]
    logger.info("Environment variables loaded successfully")
    logger.info(f"BOT_TOKEN: {BOT_TOKEN[:5]}...{BOT_TOKEN[-5:]}")
    logger.info(f"ADMIN_CHAT_IDS: {ADMIN_CHAT_IDS}")
except KeyError as e:
    logger.critical(f"Missing environment variable: {e}")
    sys.exit(1)

# Хранилище заявок
applications = {}

# Проверка прав администратора
def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id in ADMIN_CHAT_IDS

async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE, func) -> None:
    """Проверяет права перед выполнением команды"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        logger.warning(f"Unauthorized access attempt by user: {user_id}")
        await update.message.reply_text("🚫 У вас нет прав для выполнения этой команды")
        return
    await func(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"Command /start from user: {user.id}")
    
    if is_admin(user.id):
        response = (
            f"👋 Привет, администратор {user.first_name}!\n\n"
            "🔧 Доступные команды:\n"
            "/list - список заявок\n"
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
    # Обернем в проверку прав
    await admin_only(update, context, _help_command)

async def _help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Command /help from admin: {update.effective_user.id}")
    help_text = (
        "🛠️ Команды администратора:\n\n"
        "/list - показать активные заявки\n"
        "/review [ID] - просмотреть заявку\n"
        "/approve [ID] - принять заявку\n"
        "/reject [ID] - отклонить заявку\n\n"
        "Заявки автоматически поступают с сайта проекта!"
    )
    await update.message.reply_text(help_text)

async def handle_application(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Received potential application message")
        
        if "НОВАЯ ЗАЯВКА В VEXERADUBBING" not in update.message.text:
            logger.debug("Message doesn't contain application marker, skipping")
            return
            
        logger.info("Processing new application...")
        
        # Генерация ID заявки
        app_id = f"APP-{len(applications)+1:04d}"
        
        # Сохранение заявки
        applications[app_id] = {
            "status": "pending",
            "data": update.message.text,
            "telegram": next((line.split(': ')[1] for line in update.message.text.split('\n') if "Telegram" in line), "N/A")
        }
        
        # Клавиатура для управления
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
        
        # Отправка уведомления всем администраторам
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📬 *Новая заявка* `{app_id}`\n_Используйте кнопки для управления_",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                logger.info(f"Notification sent to admin: {admin_id}")
            except Exception as e:
                logger.error(f"Failed to send notification to admin {admin_id}: {str(e)}")
        
        logger.info(f"New application processed: {app_id}")
        
    except Exception as e:
        logger.error(f"Error handling application: {str(e)}", exc_info=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Проверяем права пользователя
    if not is_admin(query.from_user.id):
        logger.warning(f"Unauthorized button press by user: {query.from_user.id}")
        await query.edit_message_text("🚫 У вас нет прав для выполнения этого действия")
        return
    
    try:
        action, app_id = query.data.split('_', 1)
        logger.info(f"Button action: {action} for application: {app_id}")
        
        if app_id not in applications:
            await query.edit_message_text(text="⚠️ Заявка не найдена!")
            logger.warning(f"Application not found: {app_id}")
            return
        
        app_data = applications[app_id]['data']
        
        if action == "view":
            await query.edit_message_text(
                text=f"📄 *Заявка {app_id}:*\n\n{app_data}",
                reply_markup=query.message.reply_markup,
                parse_mode='Markdown'
            )
            return
        
        if action == "approve":
            applications[app_id]['status'] = "approved"
            new_text = f"✅ *Заявка ПРИНЯТА* `{app_id}`\n\n{app_data}"
        elif action == "reject":
            applications[app_id]['status'] = "rejected"
            new_text = f"❌ *Заявка ОТКЛОНЕНА* `{app_id}`\n\n{app_data}"
        else:
            logger.warning(f"Unknown action: {action}")
            return
        
        await query.edit_message_text(
            text=new_text,
            parse_mode='Markdown'
        )
        logger.info(f"Application {app_id} {action}ed")
        
    except Exception as e:
        logger.error(f"Error in button handler: {str(e)}", exc_info=True)

async def list_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обернем в проверку прав
    await admin_only(update, context, _list_applications)

async def _list_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(f"Command /list from admin: {update.effective_user.id}")
        
        if not applications:
            await update.message.reply_text("📭 Нет активных заявок!")
            return
        
        response = "📋 *Список заявок:*\n\n"
        for app_id, app in applications.items():
            status = {
                "pending": "🟡 Ожидает",
                "approved": "🟢 Принята",
                "rejected": "🔴 Отклонена"
            }[app['status']]
            
            response += f"• `{app_id}` - {status}\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in list_applications: {str(e)}", exc_info=True)

def main():
    logger.info("===== Starting VexeraDubbing Bot =====")
    
    try:
        # Создание приложения
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("list", list_applications))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_application))
        application.add_handler(CallbackQueryHandler(button_handler))
        
        # Запуск бота
        logger.info("Starting bot polling...")
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Failed to start bot: {str(e)}", exc_info=True)
        # Перезапуск через 30 секунд
        import time
        time.sleep(30)
        main()

if __name__ == '__main__':
    main()
