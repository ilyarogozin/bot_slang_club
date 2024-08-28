import datetime
from io import BytesIO

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    Dispatcher,
    Filters,
    MessageHandler,
    Updater,
)

from constants import (
    CHANNEL_ID,
    DOMAIN,
    MOSCOW_TZ,
    PAYMENT_KEY,
    PAYMENT_WEBHOOK,
    PHONE_NUMBER_REGEX,
    TELEGRAM_WEBHOOK,
    TOKEN,
)
from database import Review, Session, User
from manager_commands import (
    change_phone_number,
    delete_subscription,
    delete_user,
    get_all_reviews,
    get_all_users,
    give_free_subscription,
    send_invite_link_personally,
    set_subscription_end_at,
)
from postponed_tasks import (
    check_subscription_validity,
    get_first_reminder_to_join_the_club,
    get_first_reminder_to_renew_the_subscription,
    get_second_reminder_to_join_the_club,
    get_second_reminder_to_renew_the_subscription,
    handle_overlapping_subscriptions,
    notify_about_new_chat,
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
from utils import create_session, logger, update_subscription

app = Flask(__name__)
updater = Updater(
    TOKEN, use_context=True, request_kwargs={"connect_timeout": 10, "read_timeout": 20}
)
dispatcher = updater.dispatcher


# Обрабатываем обновления от телеграма с вебхука
@app.route(f"/{TELEGRAM_WEBHOOK}/", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), dispatcher.bot)
    dispatcher.process_update(update)
    return "ok"


# Обработчик вебхука для уведомлений об оплате от Tilda
@app.route(f"/{PAYMENT_WEBHOOK}/", methods=["POST"])
def payment_webhook():
    try:
        # Получаем ключ из заголовков запроса
        logger.info(f"Got webhook request headrs: {request.headers}")
        payment_key = request.headers.get("API-Key")
        # Сравниваем полученный ключ с ожидаемым
        if payment_key != PAYMENT_KEY:
            return jsonify({"status": "failure", "message": "Invalid key"}), 400
        data = request.json
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
        _, start_month, start_year = data.get("month").split("-")
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
        # Обновляем подписку в соответствии с условиями
        update_subscription(
            int(amount_months), phone_number, int(start_month), int(start_year)
        )
    except Exception as error:
        logger.error(f"payment webhook error: {str(error)}")
        return jsonify({"status": "failure", "message": str(error)}), 500
    return jsonify({"status": "success", "message": "Успешно."}), 200


# Выводим логи ошибок, вызванных обновлениями
def error(update: Update, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    return None


# Обработчик текстовых сообщений-действий
def handle_text(update: Update, context: CallbackContext) -> None:
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
                update.message.reply_text("Отзыв отменён.")
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
                Session.remove()
                update.message.reply_text("Спасибо за ваш отзыв!")
            context.user_data["awaiting_review"] = False
            return None

        if PHONE_NUMBER_REGEX.match(user_text):
            get_subscription_link(update, context, user_text)
        if user_text == "Получить ссылку 🏁":
            get_subscription_link(update, context)
        elif user_text == "Срок действия подписки 🕑":
            get_subscription_period(update, context)
        elif user_text == "Показать привязанный номер 📲":
            show_linked_phone_number(update, context)
        elif user_text == "Демо-версия сленг-клуба 🖼️":
            get_demo_version_of_club(update, context)
        elif user_text == "Оставить отзыв ✍🏼":
            write_review(update, context)
        elif user_text == "Техническая поддержка ⚙️":
            get_technical_support(update, context)
    except Exception as error:
        logger.error(str(error))
        update.message.reply_text(
            "Неизвестная ошибка. Обратитесь в техническую поддержку."
        )
    return None


# Обработчик команды /start
def start(update: Update, context: CallbackContext) -> None:
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
    update.message.reply_text(
        "Для активации подписки отправь свой номер телефона в формате "
        "+7, либо с другим кодом страны. Номер телефона должен быть "
        "таким же, как вы указывали при оплате услуги.",
        reply_markup=reply_markup,
    )
    return None


# Обрабатываем номер телефона, который пользователь отправил с клавиатуры
def handle_contact(update: Update, context: CallbackContext) -> None:
    contact = update.message.contact
    if contact is not None:
        phone_number = contact.phone_number
        if phone_number[0] != "+":
            phone_number = "+" + phone_number
        get_subscription_link(update, context, phone_number)
    return None


def main() -> None:
    # Устанавливаем вебхук
    # webhook_url = f"https://{DOMAIN}/{TELEGRAM_WEBHOOK}/"
    # updater.bot.setWebhook(webhook_url)
    # Обработчик для текста
    text_handler = MessageHandler(
        Filters.text & ~Filters.command & ~Filters.regex("#"), handle_text
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
    contact_handler = MessageHandler(Filters.contact, handle_contact)

    # Регистрируем все ошибки
    dispatcher.add_error_handler(error)

    dispatcher.add_handler(delete_user_handler)
    dispatcher.add_handler(send_invite_link_personally_handler)
    dispatcher.add_handler(set_subscription_end_at_handler)
    dispatcher.add_handler(test_postponed_task_handler)
    dispatcher.add_handler(contact_handler)
    dispatcher.add_handler(text_handler)
    dispatcher.add_handler(get_invitation_handler)
    dispatcher.add_handler(start_handler)
    dispatcher.add_handler(handler_get_all_users)
    dispatcher.add_handler(handler_get_all_reviews)
    dispatcher.add_handler(handler_change_phone_number)
    dispatcher.add_handler(handler_delete_subscription)
    dispatcher.add_handler(handler_free_subscription)

    scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)

    # Задача с запросом обратной связи на 26-е число каждого месяца в 14:00 MSK
    scheduler.add_job(
        request_feedback_from_all_users,
        "cron",
        day=26,
        hour=14,
        minute=0,
        args=[updater],
    )
    # Задача с напоминанием о продлении подписки на
    # 25-е число каждого месяца в 17:00 MSK
    scheduler.add_job(
        get_first_reminder_to_renew_the_subscription,
        "cron",
        day=25,
        hour=17,
        minute=0,
        args=[updater],
    )
    # Задача с напоминанием о продлении подписки на последнее
    # число каждого месяца в 12:00 MSK
    scheduler.add_job(
        get_second_reminder_to_renew_the_subscription,
        "cron",
        day="last",
        hour=12,
        minute=0,
        args=[updater],
    )
    # Задача на первое число каждого месяца в 16:00 MSK
    scheduler.add_job(
        get_first_reminder_to_join_the_club,
        "cron",
        day=1,
        hour=16,
        minute=0,
        args=[updater],
    )
    # Задача на первое число каждого месяца в 18:00 MSK
    scheduler.add_job(
        get_second_reminder_to_join_the_club,
        "cron",
        day=1,
        hour=18,
        minute=0,
        args=[updater],
    )
    # Проверяем валидность подписки у всех пользователей
    # первого числа каждого месяца в 18:00 MSK
    scheduler.add_job(
        check_subscription_validity,
        "cron",
        day=1,
        hour=18,
        minute=0,
        args=[updater],
    )
    # Задача для отправки инвайта новым подписчикам и сообщения о
    # продлении старым на первое число каждого месяца в 12:00 MSK
    scheduler.add_job(
        send_invite_link,
        "cron",
        day=1,
        hour=12,
        minute=0,
        args=[updater],
    )
    # Задача для слияния пересекающихся подписок с интервалом в один день
    scheduler.add_job(
        handle_overlapping_subscriptions,
        "interval",
        minutes=10,
        args=[updater],
    )
    # Задача для уведомления о новом чате-болталке для пользователей, продливших подписку
    # Устанавливаем время выполнения задачи: 1 сентября текущего года в 12:01 MSK
    execution_time = datetime.datetime(2024, 9, 1, 12, 1)
    scheduler.add_job(
        notify_about_new_chat,
        "date",
        run_date=execution_time,
        args=[updater],
        replace_existing=True,
    )

    scheduler.start()
    updater.start_polling()
    updater.idle()
    app.run(port=5001, debug=False)


if __name__ == "__main__":
    main()
