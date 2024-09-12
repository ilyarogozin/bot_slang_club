import datetime
import time

from sqlalchemy import and_, extract, func
from sqlalchemy.sql import exists
from telegram import Update
from telegram.ext import CallbackContext

from constants import CHANNEL_ID, CHAT_ID, MODERATOR_IDS, MONTHS, TEXT_INVITATION
from database import Session, Subscription, User
from utils import create_invite_link, create_session, kick_user_from_channel, logger


# Объединяем пересекающиеся подписки пользователей
def handle_overlapping_subscriptions(updater) -> None:
    with create_session() as session:
        try:
            # Получим id всех пользователей
            all_user_ids = session.query(User.id).with_for_update().all()
            # Преобразуем список кортежей в плоский список
            all_user_ids = [user_id[0] for user_id in all_user_ids]

            # Функция для проверки перекрытия двух интервалов времени
            def is_overlap_or_adjacent(start1, end1, start2, end2):
                return max(start1, start2) <= min(end1, end2) + datetime.timedelta(
                    days=1
                )

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
                        if is_overlap_or_adjacent(
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
                    combined_start = min(sub1.start_datetime, sub2.start_datetime)
                    combined_end = max(sub1.end_datetime, sub2.end_datetime)
                    # Обновляем первую подписку с объединенными датами
                    sub1.start_datetime = combined_start
                    sub1.end_datetime = combined_end
                    # Удаляем вторую подписку
                    session.delete(sub2)
            # Сохраняем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при handle_overlapping_subscriptions: {str(error)}")
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
                    bot.send_message(chat_id=chat_id, text=text, parse_mode="markdown")
                except Exception as error:
                    logger.error(
                        f"Ошибка при отправке сообщения пользователю с chat_id {chat_id}: {error}"
                    )
            else:
                logger.error("Неверный chat_id: None")
    Session.remove()
    return None


# Отправляем всем действующим подписчикам 25 числа
# в 17:00 MSK напоминание о продлении подписки
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
    last_day_of_month = datetime.datetime(
        now.year, now.month + 1, 1
    ) - datetime.timedelta(days=1)
    # Получаем телеграм id пользователей, у которых подписка заканчивается в последний день месяца
    with create_session() as session:
        telegram_ids = (
            session.query(User.telegram_id)
            .join(Subscription)
            .filter(
                and_(
                    extract("year", Subscription.end_datetime) == now.year,
                    extract("month", Subscription.end_datetime) == now.month,
                    extract("day", Subscription.end_datetime) == last_day_of_month.day,
                )
            )
            .all()
        )
        # Отправляем им соответствующее сообщение
        for telegram_id in telegram_ids:
            if telegram_id[0]:
                try:
                    bot.send_message(
                        chat_id=telegram_id[0], text=text, parse_mode="markdown"
                    )
                except Exception as error:
                    logger.error(
                        "Задача get_first_reminder_to_renew_the_subscription\n"
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
    # Определяем текущую дату
    now = datetime.datetime.now()
    today = now.date()
    with create_session() as session:
        # Получаем все telegram_id подписок, заканчивающихся сегодня
        renew_ids = (
            session.query(User.telegram_id)
            .join(Subscription)
            .filter(func.date(Subscription.end_datetime) == today)
            .all()
        )
        # Получаем телеграм id пользователей, у которых все подписки закончились
        ids_without_subscriptions = (
            session.query(User.telegram_id)
            .filter(~exists().where(Subscription.user_id == User.id))
            .all()
        )
        # Отправляем всем полученным пользователям соответствующее сообщение
        for telegram_id in renew_ids:
            if telegram_id[0]:
                try:
                    bot.send_message(
                        chat_id=telegram_id[0],
                        text=renew_message,
                        parse_mode="markdown",
                    )
                except Exception as error:
                    logger.error(
                        "Задача get_second_reminder_to_renew_the_subscription\n"
                        f"Ошибка при отправке сообщения пользователю с telegram_id {telegram_id}: {error}"
                    )
            else:
                logger.error("Неверный telegram_id: None")
        for telegram_id in ids_without_subscriptions:
            bot.send_message(chat_id=telegram_id[0], text=prolong_message)
            if telegram_id[0]:
                try:
                    bot.send_message(chat_id=telegram_id[0], text=renew_message)
                except Exception as error:
                    logger.error(
                        "Задача get_second_reminder_to_renew_the_subscription\n"
                        f"Ошибка при отправке сообщения пользователю с telegram_id {telegram_id}: {error}"
                    )
            else:
                logger.error("Неверный telegram_id: None")
    Session.remove()
    return None


# Отправляем напоминание всем подписчикам первого число месяца в 15:00 по MSK
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
                    bot.send_message(chat_id=chat_id, text=text, parse_mode="markdown")
                except Exception as error:
                    logger.error(
                        "Задача get_first_reminder_to_join_the_club\n"
                        f"Ошибка при отправке сообщения пользователю с chat_id {chat_id}: {error}"
                    )
            else:
                logger.error("Неверный chat_id: None")
    Session.remove()
    return None


# Отправляем напоминание подписчикам первого число месяца в 17:00 по MSK
def get_second_reminder_to_join_the_club(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        # Получаем подписки с заполненным полем subscription_link
        ids_with_subscriptions = (
            session.query(User.telegram_id)
            .filter(exists().where(Subscription.user_id == User.id))
            .all()
        )
        text = (
            "Как ответственный бот сленг-клуба «Sensei, for real!?», хочу "
            "тебе напомнить о моём предыдущем сообщении, если ты по каким-либо "
            "причинам не обратил на него внимание или просто забыл о нём, "
            "будучи занятым важными делами.\n\n"
            "Оно поможет тебе вовремя [вступить](https://vasilisa-slang.ru/) в сленг-клуб и не пропустить "
            "ни капельки смешного и познавательного контента🤗\n\n"
            "Жду тебя✨"
        )
        for telegram_id in ids_with_subscriptions:
            if telegram_id[0]:
                try:
                    bot.send_message(
                        chat_id=telegram_id[0], text=text, parse_mode="markdown"
                    )
                except Exception as error:
                    logger.error(
                        "Задача get_second_reminder_to_join_the_club\n"
                        f"Ошибка при отправке сообщения пользователю с telegram_id {telegram_id}: {error}"
                    )
            else:
                logger.error("Неверный chat_id: None")
    Session.remove()
    return None


# Проверям валидность подписки 1ого числа в 18:10 MSK
def check_subscription_validity(updater) -> None:
    bot = updater.bot
    with create_session() as session:
        try:
            # Получаем все истекшие подписки
            expired_subscriptions = (
                session.query(Subscription)
                .with_for_update()
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
                            "Бот не смог отозвать ссылку-приглашение в клуб у пользователя: "
                            f"{subscription.user.phone_number}, "
                            "возможно она была создана другим администратором.\n"
                            f"Error: {error}"
                        )
                    try:
                        bot.revoke_chat_invite_link(CHAT_ID, subscription.chat_link)
                    except Exception as error:
                        logger.error(
                            "Бот не смог отозвать ссылку-приглашение в чат-болталку у пользователя: "
                            f"{subscription.user.phone_number}, "
                            "возможно она была создана другим администратором.\n"
                            f"Error: {error}"
                        )
                session.delete(subscription)
                # Исключаем из канала и чата-болталки
                kick_user_from_channel(bot, subscription.user.telegram_id, CHANNEL_ID)
                kick_user_from_channel(bot, subscription.user.telegram_id, CHAT_ID)
            # Фиксируем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при check_subscription_validity: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Отправляем ссылку-приглашение новым подписчикам и сообщении о продлении старым в 12:00 MSK
def send_invite_link(updater) -> None:
    bot = updater.bot
    text_prolonged = (
        "Ма френд, привет! ✨\n\n"
        "Сегодня начинается новый период подписки на сленг-клуб **«Sensei, for real!?»**\n\n"
        "Твоя подписка успешно продлена, дополнительных действий с твоей стороны не требуется.\n\n"
        "Информация будет приходить в тот же чат, что и в предыдущем месяце.\n\n"
        "Make the most of it ♥️"
    )
    with create_session() as session:
        try:
            now = datetime.datetime.utcnow()
            yesterday = now - datetime.timedelta(days=1)
            # Получаем telegram_id пользователей с новыми подписками
            new_subscriptions = (
                session.query(Subscription, User.telegram_id)
                .with_for_update()
                .join(User, Subscription.user_id == User.id)
                .filter(Subscription.start_datetime > yesterday)
                .all()
            )
            # Получаем телеграм id пользователей с продленными подписками
            prolonged_users = (
                session.query(User.telegram_id, Subscription)
                .with_for_update()
                .join(Subscription, User.id == Subscription.user_id)
                .filter(
                    and_(
                        Subscription.start_datetime
                        < yesterday,  # Подписка началась до вчерашнего дня
                        Subscription.end_datetime
                        > now,  # Подписка еще активна на данный момент
                    )
                )
                .all()
            )
            # Отправляем соответствующие сообщения пользователям
            for subscription, telegram_id in new_subscriptions:
                # Создаём инвайты в канал и чат-болталку
                invite_link = create_invite_link(
                    bot, subscription.end_datetime, CHANNEL_ID
                )
                time.sleep(1)
                chat_link = create_invite_link(bot, subscription.end_datetime, CHAT_ID)
                time.sleep(1)
                # Присваиваем инвайт конкретному пользователю
                if invite_link and chat_link:
                    subscription.subscription_link = invite_link
                    subscription.chat_link = chat_link
                    # Отправляем текст с инвайтом
                    try:
                        bot.send_message(
                            chat_id=telegram_id,
                            text=TEXT_INVITATION.format(
                                invite_link=invite_link, chat_link=chat_link
                            ),
                        )
                    except Exception as error:
                        logger.error(
                            "В процессе задачи send_invite_link"
                            f"Ошибка при отправке сообщения пользователю {telegram_id}: {error}"
                        )
                else:
                    logger.error(
                        f"Не удалось создать сhat_link или invite_link для телеграм id: {telegram_id}\n"
                        "Соответственно сообщение-приглашение не отправлено при задаче send_invite_link"
                    )
            for telegram_id, subscription in prolonged_users:
                if not subscription.chat_link:
                    chat_link = create_invite_link(
                        bot, subscription.end_datetime, CHAT_ID
                    )
                    subscription.chat_link = chat_link
                try:
                    bot.send_message(
                        chat_id=telegram_id, text=text_prolonged, parse_mode="markdown"
                    )
                except Exception as error:
                    logger.error(
                        "В процессе задачи send_invite_link"
                        f"Ошибка при отправке сообщения пользователю {telegram_id}: {error}"
                    )
            # Сохраняем изменения в базе данных
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при send_invite_link: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Отправляем уведомление подписчикам, продлившим подписку, о новом чате-болталке в 12:05 MSK
def notify_about_new_chat(updater) -> None:
    bot = updater.bot
    notification_about_chat = (
        "Ма френд, привет!:)\n\n"
        "В этом месяце мы добавили новую функцию🪄\n"
        "Важное нововведение❗️\n\n"
        "Теперь у нас есть чат клуба, где мы можем с тобой и со всеми участниками клуба общаться!\n"
        "Скорее переходи и вступай))\n\n"
        "Ссылка-приглашение для вступления в чат клуба «Sensei, for real!?»:  {chat_link}\n\n"
        "Жду тебя ✨"
    )
    with create_session() as session:
        try:
            now = datetime.datetime.utcnow()
            yesterday = now - datetime.timedelta(days=1)
            # Получаем телеграм id пользователей с продленными подписками
            prolonged_users = (
                session.query(User.telegram_id, Subscription)
                .join(Subscription, User.id == Subscription.user_id)
                .filter(
                    and_(
                        Subscription.start_datetime
                        < yesterday,  # Подписка началась до вчерашнего дня
                        Subscription.end_datetime
                        > now,  # Подписка еще активна на данный момент
                    )
                )
                .all()
            )
            for telegram_id, subscription in prolonged_users:
                if not telegram_id:
                    continue
                if not subscription.chat_link:
                    chat_link = create_invite_link(
                        bot, subscription.end_datetime, CHAT_ID
                    )
                    if not chat_link:
                        logger.error(f"Не удалось создать ссылку для {telegram_id}")
                        continue
                    subscription.chat_link = chat_link
                try:
                    bot.send_message(
                        chat_id=telegram_id,
                        text=notification_about_chat.format(
                            chat_link=subscription.chat_link
                        ),
                        parse_mode="markdown",
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка при отправки сообщения в notify_about_new_chat "
                        f"для пользователя с телеграм id: {telegram_id}\n"
                        f"Ошибка: {str(error)}"
                    )
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при notify_about_new_chat: {str(error)}")
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
    elif task_name == "notify_about_new_chat":
        notify_about_new_chat(context)
    else:
        update.message.reply_text("Такой задачи не существует.")
        return None
    update.message.reply_text("Запрос успешно выполнен.")
    return None
