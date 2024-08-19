import datetime

from sqlalchemy import and_, extract, func
from sqlalchemy.orm import joinedload
from telegram import Update
from telegram.ext import CallbackContext

from constants import CHANNEL_ID, MODERATOR_IDS, MONTHS, TEXT_INVITATION
from database import Review, Session, Subscription, User
from utils import create_invite_link, kick_user_from_channel, logger, create_session


# Объединяем пересекающиеся подписки пользователей
def handle_overlapping_subscriptions(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        try:
            # Получим id всех пользователей
            all_user_ids = session.query(User.id).with_for_update().all()
            # Преобразуем список кортежей в плоский список
            all_user_ids = [user_id[0] for user_id in all_user_ids]

            # Функция для проверки перекрытия двух интервалов времени
            def is_overlap(start1, end1, start2, end2):
                return max(start1, start2) <= min(end1, end2)

            # Обработка подписок каждого пользователя
            for user_id in all_user_ids:
                subscriptions = (
                    session.query(Subscription)
                    .with_for_update()
                    .filter(Subscription.user_id == user_id)
                    .order_by(Subscription.start_datetime)
                    .all()
                )
                overlapping_subscriptions = []
                # Сравниваем каждую пару подписок
                for i in range(len(subscriptions)):
                    for j in range(i + 1, len(subscriptions)):
                        if is_overlap(
                            subscriptions[i].start_datetime,
                            subscriptions[i].end_datetime,
                            subscriptions[j].start_datetime,
                            subscriptions[j].end_datetime,
                        ):
                            overlapping_subscriptions.append(
                                (subscriptions[i], subscriptions[j])
                            )
                # Обрабатываем найденные перекрывающиеся подписки
                for sub1, sub2 in overlapping_subscriptions:
                    # Объединяем подписки
                    combined_start = min(
                        sub1.start_datetime, sub2.start_datetime)
                    combined_end = max(sub1.end_datetime, sub2.end_datetime)
                    # Обновляем первую подписку с объединенными датами
                    sub1.start_datetime = combined_start
                    sub1.end_datetime = combined_end
                    # Удаляем вторую подписку
                    session.delete(sub2)
            # Сохраняем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(
                f"Ошибка при handle_overlapping_subscriptions: {str(error)}")
            session.rollback()
        finally:
            Session.remove()  # Удаляем сессию из контекста
    return None


# Запрос обратной связи от всех пользователей 26 числа каждого месяца
def request_feedback_from_all_users(updater) -> None:
    bot = updater.bot
    text = (
        "Мы стараемся улучшать сленг-клуб каждый день! "
        "И будем рады получить вашу обратную связь:)\n"
        "Пожалуйста, отправляйте отзыв одним сообщением, "
        "нажав на кнопку 'Оставить отзыв'!\n"
        "Заранее Благодарим 😉"
    )
    with create_session() as session:
        telegram_ids = session.query(User.telegram_id).all()
        for telegram_id in telegram_ids:
            chat_id = telegram_id[0]
            if chat_id:
                try:
                    bot.send_message(chat_id=chat_id, text=text,
                                     parse_mode="markdown")
                except Exception as error:
                    logger.error(
                        f"Ошибка при отправке сообщения пользователю с chat_id {chat_id}: {error}"
                    )
            else:
                logger.error("Неверный chat_id: None")
    Session.remove()
    return None


# Отправляем всем действующим подписчикам 25 числа в 17:00 MSK напоминание о продлении подписки
def get_first_reminder_to_renew_the_subscription(updater) -> None:
    bot = updater.bot
    text = (
        "Ма френд, привет!🤗\n"
        "Совсем скоро начнётся новый месяц, "
        "а значит и новый период подписки на сленг-клуб «Sensei, for real!?» 🤍\n\n"
        "Чтобы остаться в самом сленговом комьюнити и продолжить развивать "
        "свой уровень языка, переходи по ссылке:\n"
        "https://vasilisa-slang.ru/\n\n"
        "Важное напоминание: контент в сленг-клубе сохраняется только на оплаченный период.\n\n"
        "Как только подписка закончится - бот автоматически исключит тебя из сленг-клуба.🥺"
    )
    # Определяем текущую дату
    now = datetime.datetime.now()
    # Находим первый и последний день текущего месяца
    first_day_of_month = datetime.datetime(now.year, now.month, 1)
    last_day_of_month = datetime.datetime(
        now.year, now.month + 1, 1
    ) - datetime.timedelta(days=1)
    # Получаем подписок, заканчивающиеся в последний день месяца
    with create_session() as session:
        expiring_subscriptions = (
            session.query(Subscription)
            .filter(
                and_(
                    extract("year", Subscription.end_datetime) == now.year,
                    extract("month", Subscription.end_datetime) == now.month,
                    extract(
                        "day", Subscription.end_datetime) == last_day_of_month.day,
                )
            )
            .all()
        )
        # Извлекаем telegram id пользователей, связанных с найденными подписками
        telegram_ids = [
            subscription.user.telegram_id for subscription in expiring_subscriptions
        ]
        # Отправляем им соответствующее сообщение
        for telegram_id in telegram_ids:
            if telegram_id:
                try:
                    bot.send_message(
                        chat_id=telegram_id, text=text, parse_mode="markdown"
                    )
                except Exception as error:
                    logger.error(
                        f"Ошибка при отправке сообщения пользователю с telegram_id {telegram_id}: {error}"
                    )
            else:
                logger.error("Неверный telegram_id: None")
    Session.remove()
    return None


# Отправляем подписчикам в последнее число месяца напоминание о продлении/возобновлении подписки в 12:00 MSK
def get_second_reminder_to_renew_the_subscription(updater) -> None:
    bot = updater.bot
    renew_message = (
        "Ма френд, привет!:)\n"
        "Сегодня последний день твоей подписки "
        "сленг-клуба «Sensei, for real!?»\n\n"
        "Не забудь [оплатить](https://vasilisa-slang.ru/), если хочешь сохранить контент и продолжить "
        "вместе со мной совершенствовать свой английский🥳"
    )
    prolong_message = (
        "Ма френд, привет!🙂\n"
        "Недавно ты был участником нашего "
        "сленг-клуба «Sensei, for real!!?», но что-то пошло не так и ты его покинул.\n\n"
        "Если ты просто взял паузу, а теперь вновь хочешь присоединиться "
        "к нашему коммьюнити, то это можно сделать по ссылке: "
        "https://vasilisa-slang.ru/"
    )
    # Определите текущую дату
    now = datetime.datetime.now()
    today = now.date()
    with create_session() as session:
        # Получаем подписки, заканчивающиеся сегодня
        expiring_subscriptions = (
            session.query(Subscription)
            .filter(func.date(Subscription.end_datetime) == today)
            .all()
        )
        # Получаем пользователей, связанных с найденными подписками
        renew_ids = [
            subscription.user.telegram_id for subscription in expiring_subscriptions
        ]
        # Получим всех пользователей
        all_users = session.query(User).all()
        # Найдём пользователей, у которых все подписки закончились
        prolong_ids = []
        for user in all_users:
            # Проверим, есть ли активные подписки
            active_subscriptions = (
                session.query(Subscription)
                .filter(
                    and_(
                        Subscription.user_id == user.id,
                        Subscription.end_datetime >= now,
                    )
                )
                .all()
            )
            # Если нет активных подписок, добавим пользователя в список
            if not active_subscriptions:
                prolong_ids.append(user.telegram_id)
        # Отправляем всем полученным пользователям соответствующее сообщение
        for telegram_id in renew_ids:
            bot.send_message(
                chat_id=telegram_id, text=renew_message, parse_mode="markdown"
            )
        for telegram_id in prolong_ids:
            bot.send_message(chat_id=telegram_id, text=prolong_message)
    Session.remove()
    return None


# Отправляем напоминание всем подписчикам первого число месяца в 16:00 по MSK
def get_first_reminder_to_join_the_club(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        telegram_ids = session.query(User.telegram_id).all()
        text = (
            "Как ответственный бот сленг-клуба «Sensei, for real!?» напоминаю "
            "о том, что если ты оплатил подписку, то тебе необходимо самостоятельно "
            "войти в сленг-клуб, чтобы не пропустить первую подборку.\n\n"
            "❗️Обязательно убедись, что ты находишься в сленг-клубе "
            "(да, сейчас он может быть пустым, если ты с нами впервые🫂)\n\n"
            "Ну а если ты ещё думаешь, когда начать свою сленговую жизнь, то сейчас "
            "самое время, ведь ты еще успеваешь присоединиться в этом месяце к нашему "
            "комьюнити.\n\n"
            "Для этого тебе нужно:\n"
            "- [оплатить](https://vasilisa-slang.ru/) сленг-клуб\n"
            "- через 10 минут запустить меня🤖\n\n"
            f"Оплаты на {MONTHS[datetime.datetime.now().month][0]} закроются сегодня в 18:00. "
            "Сразу после закрытия оплат будет первый пост🤗\n\n"
            "Жду тебя✨"
        )
        for telegram_id in telegram_ids:
            chat_id = telegram_id[0]
            if chat_id:
                try:
                    bot.send_message(chat_id=chat_id, text=text,
                                     parse_mode="markdown")
                except Exception as error:
                    logger.error(
                        f"Ошибка при отправке сообщения пользователю с chat_id {chat_id}: {error}"
                    )
            else:
                logger.error("Неверный chat_id: None")
    Session.remove()
    return None


# Отправляем напоминание подписчикам первого число месяца в 18:00 по MSK
def get_second_reminder_to_join_the_club(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        # Получаем подписки с заполненным полем subscription_link
        subscriptions_with_links = (
            session.query(Subscription)
            .filter(Subscription.subscription_link.isnot(None))
            .all()
        )
        telegram_ids = [
            subscription.user.telegram_id for subscription in subscriptions_with_links
        ]
        text = (
            "Как ответственный бот сленг-клуба «Sensei, for real!?», хочу "
            "тебе напомнить о моём предыдущем сообщении, если ты по каким-либо "
            "причинам не обратил на него внимание или просто забыл о нём, "
            "будучи занятым важными делами.\n\n"
            "Оно поможет тебе вовремя [вступить](https://vasilisa-slang.ru/) в сленг-клуб и не пропустить "
            "ни капельки смешного и познавательного контента🤗\n\n"
            "Жду тебя✨"
        )
        for telegram_id in telegram_ids:
            bot.send_message(chat_id=telegram_id, text=text,
                             parse_mode="markdown")
    Session.remove()
    return None


# Проверям валидность подписки 1ого числа в 18:00 MSK
def check_subscription_validity(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        try:
            # Получаем все истекшие подписки
            expired_subscriptions = (
                session.query(Subscription)
                .filter(Subscription.end_datetime < datetime.datetime.now())
                .all()
            )
            # Удаляем все истекшие подписки
            for subscription in expired_subscriptions:
                if subscription.subscription_link:
                    try:
                        bot.revoke_chat_invite_link(
                            CHANNEL_ID, subscription.subscription_link
                        )
                    except Exception as error:
                        logger.error(
                            f"Бот не смог отозвать ссылку-приглашение подписки: {subscription}, "
                            "возможно она была создана другим администратором."
                        )
                session.delete(subscription)
            # Исключаем из канала
            kick_user_from_channel(bot, subscription.user.telegram_id, CHANNEL_ID)
            # Фиксируем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(
                f"Ошибка при check_subscription_validity: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Отправляем в 12:00 ссылку-приглашение новым подписчикам и сообщении о продлении старым в 12:00 MSK
def send_invite_link(updater) -> None:
    bot = updater.bot
    text_prolonged = (
        "Ма френд, привет!:)\n"
        "Сегодня начинается новый период подписки на online "
        "сленг-клуб🤍\n\n"
        "Твоя подписка успешно продлена, дополнительных действий с твоей "
        "стороны не требуется. Информация будет приходить в тот же чат, "
        "что и в предыдущем месяце :)\n\n"
        "Make the most of it❤️"
    )
    with create_session() as session:
        try:
            # Получаем telegram_id пользователей с подписками без полученного инвайта
            new_subscriptions = (
                session.query(Subscription)
                .with_for_update()
                .filter(Subscription.subscription_link.is_(None))
                .options(joinedload(Subscription.user))
                .all()
            )
            # Получаем телеграм id пользователей, у которых ещё действует подписка
            prolonged_telegram_ids = (
                session.query(User.telegram_id)
                .join(Subscription, User.id == Subscription.user_id)
                .filter(Subscription.subscription_link.isnot(None))
                .all()
            )
            # Отправляем соответствующие сообщения пользователям
            for subscription in new_subscriptions:
                # Создаём инвайт в канал
                invite_link = create_invite_link(
                    context, subscription.end_datetime, CHANNEL_ID)
                # Присваиваем инвайт конкретному пользователю
                if invite_link:
                    subscription.subscription_link = invite_link
                    # Отправляем текст с инвайтом
                    bot.send_message(
                        chat_id=subscription.user.telegram_id,
                        text=TEXT_INVITATION.format(invite_link=invite_link),
                    )
            for telegram_id in prolonged_telegram_ids:
                bot.send_message(chat_id=telegram_id[0], text=text_prolonged)
            # Сохраняем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при send_invite_link: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Функция для тестирования отложенных задач
def test_postponed_task(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /test_postponed_task название_задачи\n\n"
            "Одним сообщением, в одну строку. Вот список названий всех задач:\n\n"
            "request_feedback_from_all_users - запросить отзыв от всех пользователей\n\n"
            "get_first_reminder_to_renew_the_subscription - напомнить о продлении подписки 25 числа в 17:00 MSK\n\n"
            "get_second_reminder_to_renew_the_subscription - напомнить о продлении/возобновлении подписки последнего числа месяца в 12:00 MSK\n\n"
            "get_first_reminder_to_join_the_club - напомнить о вступлении в клуб по ссылке 1ого числа в 16:00 MSK\n\n"
            "get_second_reminder_to_join_the_club - напомнить о вступлении в клуб по ссылке 1ого числа в 18:00 MSK\n\n"
            "check_subscription_validity - проверить валидность подпискок 1ого числа в 18:00 MSK\n\n"
            "send_invite_link - отправить ссылку-приглашение всем новым подписчикам 1ого числа месяца в 12:00 MSK, либо сообщение о продлении подписки, если действующие\n\n"
            "handle_overlapping_subscriptions - объединить пересекающиеся по времени подписки, выполняется с интервалом в один день"
        )
        return None
    task_name = args[0]
    if task_name == "request_feedback_from_all_users":
        request_feedback_from_all_users(context)
    elif task_name == "get_first_reminder_to_renew_the_subscription":
        get_first_reminder_to_renew_the_subscription(context)
    elif task_name == "get_second_reminder_to_renew_the_subscription":
        get_second_reminder_to_renew_the_subscription(context)
    elif task_name == "get_first_reminder_to_join_the_club":
        get_first_reminder_to_join_the_club(context)
    elif task_name == "get_second_reminder_to_join_the_club":
        get_second_reminder_to_join_the_club(context)
    elif task_name == "check_subscription_validity":
        check_subscription_validity(context)
    elif task_name == "send_invite_link":
        send_invite_link(context)
    elif task_name == "handle_overlapping_subscriptions":
        handle_overlapping_subscriptions(context)
    else:
        update.message.reply_text("Такой задачи не существует.")
        return None
    update.message.reply_text("Запрос успешно выполнен.")
    return None
