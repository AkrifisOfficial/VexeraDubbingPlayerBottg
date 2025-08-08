import os
import logging
import json
import time
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Dispatcher,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
DB_FILE = "applications_db.json"

# Инициализация данных
PENDING_APPLICATIONS = {}
APPROVED_APPLICATIONS = {}
REJECTED_APPLICATIONS = {}
application_counter = 1

# Создаем Flask приложение
app = Flask(__name__)

# Инициализация Telegram бота
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

# Инициализация базы данных
def init_database():
    global PENDING_APPLICATIONS, APPROVED_APPLICATIONS, REJECTED_APPLICATIONS, application_counter
    
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r') as f:
                data = json.load(f)
                PENDING_APPLICATIONS = data.get('pending', {})
                APPROVED_APPLICATIONS = data.get('approved', {})
                REJECTED_APPLICATIONS = data.get('rejected', {})
                application_counter = data.get('counter', 1)
            logger.info("База данных загружена")
    except Exception as e:
        logger.error(f"Ошибка загрузки БД: {e}")

# Сохранение базы данных
def save_database():
    try:
        data = {
            'pending': PENDING_APPLICATIONS,
            'approved': APPROVED_APPLICATIONS,
            'rejected': REJECTED_APPLICATIONS,
            'counter': application_counter
        }
        with open(DB_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("База данных сохранена")
    except Exception as e:
        logger.error(f"Ошибка сохранения БД: {e}")

# Инициализация обработчиков команд
def init_handlers():
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("apps", show_applications))
    dispatcher.add_handler(CommandHandler("approved", show_approved))
    dispatcher.add_handler(CommandHandler("rejected", show_rejected))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Обработчики команд инициализированы")

# Клавиатура для действий с заявкой
def application_keyboard(app_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{app_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}")
        ],
        [
            InlineKeyboardButton("📝 Подробнее", callback_data=f"details_{app_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{app_id}")
        ]
    ])

# Команда старт
def start_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        update.message.reply_text(
            "👋 Привет, администратор VexeraDubbing!\n\n"
            "📋 Доступные команды:\n"
            "/apps - Ожидающие заявки\n"
            "/approved - Одобренные заявки\n"
            "/rejected - Отклоненные заявки\n"
            "/help - Помощь\n\n"
            "Для обработки заявок используйте кнопки под сообщениями."
        )
    else:
        update.message.reply_text("❌ Этот бот предназначен только для администраторов команды озвучки.")

# Команда помощи
def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "ℹ️ Справочник администратора:\n\n"
        "/apps - Показать все ожидающие заявки\n"
        "/approved - Показать одобренные заявки\n"
        "/rejected - Показать отклоненные заявки\n\n"
        "Кнопки управления заявками:\n"
        "✅ Одобрить - Принять заявку в команду\n"
        "❌ Отклонить - Отклонить заявку\n"
        "📝 Подробнее - Показать детали заявки\n"
        "🗑️ Удалить - Удалить заявку из системы"
    )
    update.message.reply_text(help_text)

# Показать ожидающие заявки
def show_applications(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ У вас нет прав для просмотра заявок")
        return

    if not PENDING_APPLICATIONS:
        update.message.reply_text("ℹ️ В настоящее время нет ожидающих заявок.")
        return

    for app_id in PENDING_APPLICATIONS:
        send_application_message(update, context, PENDING_APPLICATIONS[app_id])

# Показать одобренные заявки
def show_approved(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ У вас нет прав для просмотра заявок")
        return

    if not APPROVED_APPLICATIONS:
        update.message.reply_text("ℹ️ Пока нет одобренных заявок.")
        return

    for app_id in APPROVED_APPLICATIONS:
        send_application_message(update, context, APPROVED_APPLICATIONS[app_id], approved=True)

# Показать отклоненные заявки
def show_rejected(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ У вас нет прав для просмотра заявок")
        return

    if not REJECTED_APPLICATIONS:
        update.message.reply_text("ℹ️ Пока нет отклоненных заявок.")
        return

    for app_id in REJECTED_APPLICATIONS:
        send_application_message(update, context, REJECTED_APPLICATIONS[app_id], rejected=True)

# Форматирование заявки
def format_application(application, detailed=False):
    timestamp = datetime.fromtimestamp(application['timestamp']).strftime('%d.%m.%Y %H:%M')
    
    message = (
        f"🚀 {'Одобрена' if application.get('approved') else 'Отклонена' if application.get('rejected') else 'Заявка'} "
        f"#{application['id']}\n"
        f"📅 {timestamp}\n\n"
        f"👤 Имя: {application['name']}\n"
        f"📞 Контакты: {application['contact']}\n"
        f"🎭 Роль: {application['role']}\n"
    )
    
    if detailed:
        message += (
            f"\n💼 Опыт:\n{application.get('experience', 'не указан')}\n"
            f"\n🔗 Примеры работ:\n{application.get('samples', 'не указаны')}\n"
            f"\n💬 Мотивация:\n{application['motivation']}\n"
        )
    
    if application.get('processed_by'):
        message += f"\n👤 Обработал: {application['processed_by']}"
    
    if application.get('processed_time'):
        proc_time = datetime.fromtimestamp(application['processed_time']).strftime('%d.%m.%Y %H:%M')
        message += f"\n⏱ Время обработки: {proc_time}"
    
    return message

# Отправка сообщения с заявкой
def send_application_message(update, context, application, approved=False, rejected=False):
    message = format_application(application)
    
    keyboard = None
    if not approved and not rejected:
        keyboard = application_keyboard(application['id'])
    
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

# Обработка действий с заявкой
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data.split("_")
    action = data[0]
    app_id = data[1]
    
    if query.from_user.id not in ADMIN_IDS:
        query.edit_message_text("❌ У вас нет прав для обработки заявок")
        return

    application = None
    source = None
    
    # Поиск заявки в разных категориях
    if app_id in PENDING_APPLICATIONS:
        application = PENDING_APPLICATIONS[app_id]
        source = 'pending'
    elif app_id in APPROVED_APPLICATIONS:
        application = APPROVED_APPLICATIONS[app_id]
        source = 'approved'
    elif app_id in REJECTED_APPLICATIONS:
        application = REJECTED_APPLICATIONS[app_id]
        source = 'rejected'
    
    if not application:
        query.edit_message_text("⚠️ Заявка не найдена")
        return

    admin_name = query.from_user.first_name
    current_time = time.time()
    
    if action == "approve":
        # Перемещаем заявку в одобренные
        application['approved'] = True
        application['processed_by'] = admin_name
        application['processed_time'] = current_time
        
        if source == 'pending':
            del PENDING_APPLICATIONS[app_id]
        elif source == 'rejected':
            del REJECTED_APPLICATIONS[app_id]
        
        APPROVED_APPLICATIONS[app_id] = application
        
        # Обновляем сообщение
        new_text = format_application(application, detailed=True)
        query.edit_message_text(
            text=new_text + "\n\n✅ Заявка одобрена!",
            parse_mode='Markdown'
        )
        
        # Уведомление другим админам
        notify_admins(f"👤 {admin_name} одобрил заявку #{app_id}")

    elif action == "reject":
        # Перемещаем заявку в отклоненные
        application['rejected'] = True
        application['processed_by'] = admin_name
        application['processed_time'] = current_time
        
        if source == 'pending':
            del PENDING_APPLICATIONS[app_id]
        elif source == 'approved':
            del APPROVED_APPLICATIONS[app_id]
        
        REJECTED_APPLICATIONS[app_id] = application
        
        query.edit_message_text(
            text=f"❌ Заявка #{app_id} отклонена",
            parse_mode='Markdown'
        )
        notify_admins(f"👤 {admin_name} отклонил заявку #{app_id}")

    elif action == "details":
        new_text = format_application(application, detailed=True)
        keyboard = None if application.get('approved') or application.get('rejected') else application_keyboard(app_id)
        
        query.edit_message_text(
            text=new_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif action == "delete":
        # Удаление заявки из системы
        if source == 'pending' and app_id in PENDING_APPLICATIONS:
            del PENDING_APPLICATIONS[app_id]
        elif source == 'approved' and app_id in APPROVED_APPLICATIONS:
            del APPROVED_APPLICATIONS[app_id]
        elif source == 'rejected' and app_id in REJECTED_APPLICATIONS:
            del REJECTED_APPLICATIONS[app_id]
        
        query.edit_message_text(
            text=f"🗑️ Заявка #{app_id} удалена",
            parse_mode='Markdown'
        )
        notify_admins(f"👤 {admin_name} удалил заявку #{app_id}")
    
    # Сохраняем изменения в БД
    save_database()

# Уведомление администраторов
def notify_admins(message: str):
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")

# Эндпоинт для приема заявок с сайта
@app.route('/submit', methods=['POST'])
def webhook_handler():
    global application_counter
    
    try:
        data = request.json
        
        # Проверка обязательных полей
        required_fields = ['name', 'contact', 'role', 'motivation']
        for field in required_fields:
            if field not in data or not data[field].strip():
                return jsonify({
                    "status": "error",
                    "message": f"Поле {field} обязательно для заполнения"
                }), 400
        
        # Создаем заявку
        app_id = str(application_counter)
        application = {
            "id": app_id,
            "name": data['name'],
            "contact": data['contact'],
            "role": data['role'],
            "experience": data.get('experience', 'Не указан'),
            "samples": data.get('samples', 'Не указаны'),
            "motivation": data['motivation'],
            "timestamp": time.time()
        }
        application_counter += 1
        
        # Сохраняем заявку
        PENDING_APPLICATIONS[app_id] = application
        
        # Отправляем всем админам
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(
                    chat_id=admin_id,
                    text=format_application(application),
                    reply_markup=application_keyboard(app_id),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка отправки заявки админу {admin_id}: {e}")
        
        # Сохраняем БД
        save_database()
        
        return jsonify({
            "status": "success",
            "message": "Заявка получена",
            "application_id": app_id
        }), 200
    
    except Exception as e:
        logger.error(f"Ошибка обработки заявки: {e}")
        return jsonify({
            "status": "error",
            "message": "Внутренняя ошибка сервера"
        }), 500

# Эндпоинт для обработки обновлений Telegram
@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    update = Update.de_json(request.get_json(), bot)
    dispatcher.process_update(update)
    return 'ok', 200

# Статус сервера
@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "service": "VexeraDubbing Application Bot",
        "version": "1.0",
        "pending_applications": len(PENDING_APPLICATIONS),
        "approved_applications": len(APPROVED_APPLICATIONS),
        "rejected_applications": len(REJECTED_APPLICATIONS)
    })

# Инициализация при запуске
if __name__ == '__main__':
    # Загрузка базы данных
    init_database()
    
    # Инициализация обработчиков
    init_handlers()
    
    # Настройка вебхука (если используется)
    # bot.delete_webhook()
    # bot.set_webhook(url="https://your-render-url.onrender.com/telegram")
    
    # Запуск Flask сервера
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
