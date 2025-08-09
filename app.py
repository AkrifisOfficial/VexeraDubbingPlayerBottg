import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Настройки из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилище заявок (для демо)
applications = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}! Я бот для управления заявками VexeraDubbing.\n\n"
        "🔧 Доступные команды:\n"
        "/list - список заявок\n"
        "/help - справка по командам"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        if "НОВАЯ ЗАЯВКА В VEXERADUBBING" not in update.message.text:
            return
            
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
        
        # Отправка уведомления
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"📬 *Новая заявка* `{app_id}`\n_Используйте кнопки для управления_",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        logger.info(f"New application received: {app_id}")
        
    except Exception as e:
        logger.error(f"Error handling application: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, app_id = query.data.split('_', 1)
    
    if app_id not in applications:
        await query.edit_message_text(text="⚠️ Заявка не найдена!")
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
    
    await query.edit_message_text(
        text=new_text,
        parse_mode='Markdown'
    )
    logger.info(f"Application {app_id} {action}ed")

async def list_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

def main():
    # Проверка переменных окружения
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        logger.error("Missing required environment variables!")
        return
    
    # Создание приложения
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_applications))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_application))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Запуск бота
    logger.info("Starting VexeraDubbing Bot...")
    application.run_polling()

if __name__ == '__main__':
    main()
