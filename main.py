import re
import threading
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask, jsonify, request
from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    filters,
    MessageHandler,
    ApplicationBuilder,
    ConversationHandler,
    CallbackQueryHandler,
    Application,
)
from telegram.request import HTTPXRequest

from constants import (
    MOSCOW_TZ,
    PAYMENT_KEY,
    PAYMENT_WEBHOOK,
    PHONE_NUMBER_REGEX,
    TELEGRAM_WEBHOOK,
    TOKEN,
    WAITING_NUMBERS,
    logger,
)
from database import Review, User, create_session
from manager_commands import (
    change_phone_number,
    delete_subscription,
    delete_user,
    get_all_reviews,
    get_all_users,
    give_free_subscription,
    send_invite_link_personally,
    set_subscription_end_at,
    send_bulk_messages,
    process_numbers,
    cancel,
    button,
    set_messages,
    get_button_stats,
)
from postponed_tasks import (
    check_subscription_validity,
    get_first_reminder_to_join_the_club,
    get_first_reminder_to_renew_the_subscription,
    get_second_reminder_to_join_the_club,
    get_second_reminder_to_renew_the_subscription,
    handle_overlapping_subscriptions,
    request_feedback_from_all_users,
    send_invite_link,
    test_postponed_task,
)
from user_commands import (
    get_demo_version_of_club,
    get_invitation,
    get_subscription_link,
    get_subscription_period,
    get_technical_support,
    show_linked_phone_number,
    write_review,
)
from utils import update_subscription

app = Flask(__name__)


# Обрабатываем обновления от телеграма с вебхука
@app.route(f"/{TELEGRAM_WEBHOOK}/", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), app.bot)
    app.process_update(update)
    return "ok"


# Обработчик вебхука для уведомлений об оплате от Tilda
@app.route(f"/{PAYMENT_WEBHOOK}/", methods=["POST"])
async def payment_webhook():
    try:
        # Получаем ключ из заголовков запроса
        logger.info(f"Got webhook request headrs: {request.headers}")
        payment_key = request.headers.get("API-Key")
        # Сравниваем полученный ключ с ожидаемым
        if payment_key != PAYMENT_KEY:
            return (jsonify({"status": "failure", "message": "Invalid key"}), 400)
        data = request.get_json()
        logger.info(f"Got webhook request body: {data}")
        phone_number = data.get("Phone")
        if not phone_number:
            return (
                jsonify(
                    {
                        "status": "failure",
                        "message": "В уведомлении отсутствует номер телефона.",
                    }
                ),
                400,
            )
        # Регулярное выражение для удаления всех символов,
        # кроме цифр и знака "+"
        pattern = re.compile(r"[^\d+]")
        phone_number = pattern.sub("", phone_number)
        amount_months = data.get("payment").get("products")[
            0].get("name").split()[-2]
        if not amount_months:
            return (
                jsonify(
                    {
                        "status": "failure",
                        "message": "В уведомлении в имени товара отсутствует количество месяцев.",
                    }
                ),
                400,
            )
        start_year, start_month, _ = data.get("month").split("-")
        if not start_month or not start_year:
            return (
                jsonify(
                    {
                        "status": "failure",
                        "message": "В уведомлении отсутствует начальный месяц или год подписки.",
                    }
                ),
                400,
            )
        tg = data.get("tg")
        tg = tg[1:] if tg.startswith("@") else tg
        # Обновляем подписку в соответствии с условиями
        await update_subscription(
            int(amount_months), phone_number, int(
                start_month), int(start_year), tg
        )
    except Exception as error:
        logger.error(f"payment webhook error: {str(error)}")
        return jsonify({"status": "failure", "message": str(error)}), 500
    return jsonify({"status": "success", "message": "Успешно."}), 200


# Выводим логи ошибок, вызванных обновлениями
async def error(update: Update, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)


# Обработчик текстовых сообщений-действий
async def handle_text(update: Update, context: CallbackContext) -> None:
    try:
        user_text = update.message.text
        # Если ждем отзыв
        if context.user_data.get("awaiting_review"):
            if user_text in {
                "-",
                "Получить ссылку 🏁",
                "Срок действия подписки 🕑",
                "Показать привязанный номер 📲",
                "Оставить отзыв ✍🏼",
                "Техническая поддержка ⚙️",
            }:
                await update.message.reply_text("Отзыв отменён.")
            else:
                with create_session() as session:
                    telegram_id = update.message.from_user.id
                    user = (
                        session.query(User)
                        .filter(User.telegram_id == telegram_id)
                        .first()
                    )
                    new_review = Review(review_text=user_text, user_id=user.id)
                    session.add(new_review)
                    session.commit()
                await update.message.reply_text("Спасибо за ваш отзыв!")
            context.user_data["awaiting_review"] = False
            return None

        if PHONE_NUMBER_REGEX.match(user_text):
            await get_subscription_link(update, context, user_text)
        if user_text == "Получить ссылку 🏁":
            await get_subscription_link(update, context)
        elif user_text == "Срок действия подписки 🕑":
            await get_subscription_period(update, context)
        elif user_text == "Показать привязанный номер 📲":
            await show_linked_phone_number(update, context)
        elif user_text == "Демо-версия сленг-клуба 🖼️":
            await get_demo_version_of_club(update, context)
        elif user_text == "Оставить отзыв ✍🏼":
            await write_review(update, context)
        elif user_text == "Техническая поддержка ⚙️":
            await get_technical_support(update, context)
    except Exception as error:
        logger.error(str(error))
        await update.message.reply_text(
            "Неизвестная ошибка. Обратитесь в техническую поддержку."
        )
    return None


# Обработчик команды /start
async def start(update: Update, context: CallbackContext) -> None:
    contact_keyboard = KeyboardButton(
        text="Отправить номер телефона📞", request_contact=True
    )
    keyboard = [
        ["Получить ссылку 🏁", "Срок действия подписки 🕑"],
        [contact_keyboard, "Показать привязанный номер 📲"],
        ["Оставить отзыв ✍🏼", "Техническая поддержка ⚙️"],
        ["Демо-версия сленг-клуба 🖼️"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Для активации подписки отправь свой номер телефона в формате "
        "+7, либо с другим кодом страны. Номер телефона должен быть "
        "таким же, как вы указывали при оплате услуги.",
        reply_markup=reply_markup,
    )
    return None


# Обрабатываем номер телефона, который пользователь отправил с клавиатуры
async def handle_contact(update: Update, context: CallbackContext) -> None:
    contact = update.message.contact
    if contact is not None:
        phone_number = contact.phone_number
        if phone_number[0] != "+":
            phone_number = "+" + phone_number
        await get_subscription_link(update, context, phone_number)
    return None


def main() -> None:
    # Устанавливаем вебхук
    # webhook_url = f"https://{DOMAIN}/{TELEGRAM_WEBHOOK}/"
    # application.bot.setWebhook(webhook_url)
    # Обработчик для текста
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler(
            "send_bulk_messages", send_bulk_messages)],
        states={
            WAITING_NUMBERS: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, process_numbers)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    text_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex("#"), handle_text
    )
    start_handler = CommandHandler("start", start)
    handler_free_subscription = CommandHandler(
        "give_free_subscription", give_free_subscription
    )
    handler_delete_subscription = CommandHandler(
        "delete_subscription", delete_subscription
    )
    handler_change_phone_number = CommandHandler(
        "change_phone_number", change_phone_number
    )
    handler_get_all_reviews = CommandHandler(
        "get_all_reviews", get_all_reviews)
    handler_get_all_users = CommandHandler("get_all_users", get_all_users)
    get_invitation_handler = CommandHandler("get_invitation", get_invitation)
    test_postponed_task_handler = CommandHandler(
        "test_postponed_task", test_postponed_task
    )
    set_subscription_end_at_handler = CommandHandler(
        "set_subscription_end_at", set_subscription_end_at
    )
    send_invite_link_personally_handler = CommandHandler(
        "send_invite_link_personally", send_invite_link_personally
    )
    delete_user_handler = CommandHandler("delete_user", delete_user)
    # Обработчик номера телефона, отправленного с клавиатуры
    contact_handler = MessageHandler(filters.CONTACT, handle_contact)

    async def post_init(app: Application):
        scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)

        # Задача с запросом обратной связи на 26-е число каждого месяца в 14:00 MSK
        scheduler.add_job(
            request_feedback_from_all_users,
            "cron",
            day=26,
            hour=14,
            minute=0,
            args=[app],
        )
        # Задача с напоминанием о продлении подписки на
        # 25-е число каждого месяца в 12:00 MSK
        scheduler.add_job(
            get_first_reminder_to_renew_the_subscription,
            "cron",
            day=25,
            hour=12,
            minute=0,
            args=[app],
        )
        # Задача с напоминанием о продлении подписки на последнее
        # число каждого месяца в 12:00 MSK
        scheduler.add_job(
            get_second_reminder_to_renew_the_subscription,
            "cron",
            day="last",
            hour=12,
            minute=0,
            args=[app],
        )
        # Задача на первое число каждого месяца в 15:00 MSK
        scheduler.add_job(
            get_first_reminder_to_join_the_club,
            "cron",
            day=1,
            hour=15,
            minute=0,
            args=[app],
        )
        # Задача на первое число каждого месяца в 17:00 MSK
        scheduler.add_job(
            get_second_reminder_to_join_the_club,
            "cron",
            day=1,
            hour=17,
            minute=0,
            args=[app],
        )
        # Проверяем валидность подписки у всех пользователей
        # первого числа каждого месяца в 18:10 MSK
        scheduler.add_job(
            check_subscription_validity,
            "cron",
            day=1,
            hour=18,
            minute=10,
            args=[app],
        )
        # Задача для отправки инвайта новым подписчикам и сообщения о
        # продлении старым на первое число каждого месяца в 12:00 MSK
        scheduler.add_job(
            send_invite_link,
            "cron",
            day=1,
            hour=12,
            minute=0,
            args=[app],
        )
        # Задача для слияния пересекающихся подписок с интервалом в один день
        scheduler.add_job(
            handle_overlapping_subscriptions,
            "interval",
            minutes=9,
            args=[app],
            coalesce=True,
            misfire_grace_time=60,
        )

        scheduler.start()

    request_settings = HTTPXRequest(connect_timeout=10, read_timeout=20)
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request_settings)
        .post_init(post_init)
        .build()
    )

    # Регистрируем все ошибки
    application.add_error_handler(error)

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(CommandHandler("set_messages", set_messages))
    application.add_handler(CommandHandler(
        "get_button_stats", get_button_stats))
    application.add_handler(delete_user_handler)
    application.add_handler(send_invite_link_personally_handler)
    application.add_handler(set_subscription_end_at_handler)
    application.add_handler(test_postponed_task_handler)
    application.add_handler(contact_handler)
    application.add_handler(text_handler)
    application.add_handler(get_invitation_handler)
    application.add_handler(start_handler)
    application.add_handler(handler_get_all_users)
    application.add_handler(handler_get_all_reviews)
    application.add_handler(handler_change_phone_number)
    application.add_handler(handler_delete_subscription)
    application.add_handler(handler_free_subscription)

    thread = threading.Thread(
        target=lambda: app.run(
            port=5001,
            debug=False,
            threaded=True,
            use_reloader=False
        )
    )
    thread.daemon = True
    thread.start()
    application.run_polling()


if __name__ == "__main__":
    main()
