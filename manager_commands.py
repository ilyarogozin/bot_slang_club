import datetime
import time
from io import BytesIO

import pandas as pd
from sqlalchemy import asc
from telegram import Update
from telegram.ext import CallbackContext

from constants import (
    CHANNEL_ID,
    CHAT_ID,
    MODERATOR_IDS,
    MOSCOW_TZ,
    PHONE_NUMBER_REGEX,
    TEXT_INVITATION,
)
from database import Review, Session, Subscription, User
from utils import (
    check_user_in_channel,
    create_invite_link,
    create_session,
    logger,
    update_subscription,
)


# Установить конец подписки вручную через команду /set_subscription_end_at
def set_subscription_end_at(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 2:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /set_subscription_end_at год:месяц:день:часы:минуты номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    manual_datetime, phone_number = args
    if not PHONE_NUMBER_REGEX.match(phone_number):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    try:
        manual_datetime = list(map(int, manual_datetime.split(":")))
        year, month, day, hour, minute = manual_datetime
    except Exception as error:
        logger.error(f"Ошибка ввода конца подписки: {error}")
        update.message.reply_text(
            "Вы где-то ошиблись в этом параметре: год:месяц:день:часы:минуты. Попробуйте снова."
            "Должно быть например: 2024:7:21:12:45"
        )
        return None
    now = datetime.datetime.now()
    end_datetime = datetime.datetime(
        year=year, month=month, day=day, hour=hour, minute=minute
    )
    with create_session() as session:
        try:
            user_id = (
                session.query(User.id).filter(User.phone_number == phone_number).first()
            )
            # Получаем самую ближайшую подписку
            nearest_subscription = (
                session.query(Subscription)
                .filter(Subscription.user_id == user_id[0])
                .order_by(asc(Subscription.start_datetime))
                .first()
            )
            # Проверяем наличие пользователя
            if not user_id:
                update.message.reply_text(
                    "Пользователя с таким номером телефона не существует."
                )
                return None
            if not nearest_subscription:
                new_subscription = Subscription(
                    start_datetime=now, end_datetime=end_datetime, user_id=user_id[0]
                )
                session.add(new_subscription)
                session.commit()
                update.message.reply_text(
                    f"Конец подписки успешно изменён на {end_datetime.strftime('%d-%m-%Y %H:%M')} "
                    f"у пользователя с номером телефона: {phone_number}."
                )
                return None
            # Обновляем конец подписки
            nearest_subscription.end_datetime = end_datetime
            # Фиксируем изменения
            session.commit()
            update.message.reply_text(
                f"Конец подписки успешно изменён на {end_datetime.strftime('%d-%m-%Y %H:%M')} "
                f"у пользователя с номером телефона: {phone_number}."
            )
        except Exception as error:
            session.rollback()
            logger.error(f"Ошибка при set_subscription_end_at: {str(error)}")
        finally:
            Session.remove()  # Удаляем сессию из контекста
    return None


# По этой команде даём пользователю бесплатную подписку по номеру телефона
def give_free_subscription(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Получаем номер телефона пользователя и количество месяцев из команды
    args = context.args
    if len(args) != 4:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /give_free_subscription "
            "номер_телефона количество_месяцев номер_стартового_месяца год_стартового_месяца\n"
            "Пример: /give_free_subscription +79998887776 1 9 2024\n"
            "Одним сообщением, в одну строку."
        )
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    phone_number, months, start_month, start_year = args
    if not PHONE_NUMBER_REGEX.match(phone_number):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    try:
        months = int(months)
        start_month = int(start_month)
        start_year = int(start_year)
    except ValueError:
        update.message.reply_text(
            "Пожалуйста, введите количество месяцев, месяц начала и год числом."
        )
        return None
    # now = datetime.datetime.now()
    # if start_year < now.year:
    #     update.message.reply_text(
    #         "Пожалуйста, введите год начала не раньше нынешнего.")
    #     return None
    # if start_month < now.month:
    #     update.message.reply_text(
    #         "Пожалуйста, введите месяц начала не раньше нынешнего."
    #     )
    #     return None
    if months < 0:
        update.message.reply_text(
            "Пожалуйста, введите положительное количество месяцев."
        )
        return None
    # Даём пользователю бесплатную подписку
    update_subscription(months, phone_number, start_month, start_year, "-")
    # Отвечаем, что всё прошло успешно
    update.message.reply_text(
        f"Пользователю с номером {phone_number} была предоставлена подписка на {months} месяцев, "
        f"старт подписки {start_month} месяца {start_year} года."
    )
    return None


# Функция для удаления ближайшей подписки пользователя
def delete_subscription(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /delete_subscription номер_телефона\n"
            "Будет удалена самая ближайшая подписка.\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            # Проверяем наличие пользователя
            user_id = (
                session.query(User.id)
                .filter(User.phone_number == phone_number)
                .first()[0]
            )
            if not user_id:
                update.message.reply_text(
                    "Пользователя с таким телефонным номером не существует."
                )
                return None
            nearest_subscription = (
                session.query(Subscription)
                .filter(Subscription.user_id == user_id)
                .order_by(asc(Subscription.start_datetime))
                .first()
            )
            # Если нет подписки
            if not nearest_subscription:
                update.message.reply_text(
                    f"У пользователя {phone_number} нет подписки."
                )
                return None
            # Отменяем ссылку-приглашение в канал и чат-болталку, если есть
            if nearest_subscription.subscription_link:
                try:
                    context.bot.revoke_chat_invite_link(
                        CHANNEL_ID, nearest_subscription.subscription_link
                    )
                    context.bot.revoke_chat_invite_link(
                        CHAT_ID, nearest_subscription.chat_link
                    )
                except Exception as error:
                    logger.error(
                        f"Ошибка при отмене ссылки на канал или чат-болталку у подписки с id: {nearest_subscription.id}\n"
                        f"error: {str(error)}"
                    )
            # Удаляем подписку
            session.delete(nearest_subscription)
            # Фиксируем изменения в базе данных
            session.commit()
            # Сообщаем, что всё прошло успешно
            update.message.reply_text(
                f"Ближайшая подписка пользователя {phone_number} успешно удалена."
            )
        except Exception as error:
            logger.error(f"Ошибка при delete_subscription: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Функция для изменения номера телефона пользователя
def change_phone_number(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 2:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /change_phone_number старый_номер__пользователя новый_номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    old_phone_number, new_phone_number = args
    if not PHONE_NUMBER_REGEX.match(old_phone_number) or not PHONE_NUMBER_REGEX.match(
        new_phone_number
    ):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            # Проверяем, не занят ли такой номер кем-либо ещё
            user = (
                session.query(User)
                .filter(User.phone_number == new_phone_number)
                .first()
            )
            if user:
                update.message.reply_text(
                    "Номер телефона, на который вы хотите поменять, уже принадлежит другому пользователю."
                )
                return None
            user = (
                session.query(User)
                .filter(User.phone_number == old_phone_number)
                .first()
            )
            if not user:
                update.message.reply_text(
                    "Пользователя с таким нмоером телефона не существует."
                )
                return None
            # Обновляем номер телефона пользователя
            user.phone_number = new_phone_number
            # Обновляем запись базы данных
            session.commit()
            # Сообщаем, что всё прошло успешно
            update.message.reply_text(
                f"Номер телефона успешно изменён на {new_phone_number} у пользователя с прошлым номером: {old_phone_number}."
            )
        except Exception as error:
            logger.error(f"Ошибка при change_phone_number: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Получаем все отзывы пользователей в файле excel
def get_all_reviews(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    with create_session() as session:
        # Запрашиваем данные из базы
        query = session.query(
            Review.review_text, User.phone_number, User.user_link
        ).join(User)
    Session.remove()
    if query.count() == 0:
        update.message.reply_text("Новых отзывов не найдено.")
        return None
    # Преобразуем результаты запроса в DataFrame
    df = pd.read_sql(query.statement, query.session.bind)
    # Переименовываем столбцы
    df.columns = ["Текст отзыва", "Телефонный номер", "Ссылка на телеграм аккаунт"]
    # Создаём Excel-файла в памяти
    with BytesIO() as output:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Отзывы", index=False)
            worksheet = writer.sheets["Отзывы"]
            # Настройка ширины столбцов и перенос текста для длинных отзывов
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    # Определяем максимальную длину содержимого в ячейке
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except Exception:
                        pass
                # Устанавливаем ширину столбца
                adjusted_width = max_length + 2
                worksheet.column_dimensions[column].width = adjusted_width
        output.seek(0)  # Перемещаемся к началу потока
        # Отправляем файл пользователю
        context.bot.send_document(
            chat_id=update.effective_chat.id, document=output, filename="reviews.xlsx"
        )
    return None


# Получаем всех пользователей в файле excel
def get_all_users(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    with create_session() as session:
        # Получаем всех пользователей
        users = session.query(User).all()
        # Преобразование данных в формат, подходящий для записи в Excel
        all_users_data = []
        subscribed_users_data = []
        active_subscriptions_data = []
        unjoined_users_data = []
        unjoined_in_chat_data = []
        now = datetime.datetime.now(MOSCOW_TZ)
        for user in users:
            try:
                subscriptions_str = ", ".join(
                    [
                        f"{sub.start_datetime.strftime('%d.%m.%Y')}-{sub.end_datetime.strftime('%d.%m.%Y')}"
                        for sub in user.subscriptions
                    ]
                )
                user_data = {
                    "Телеграм ID": user.telegram_id,
                    "Телефонный номер": user.phone_number,
                    "Ссылка на телеграм аккаунт": user.user_link,
                    "Подписки": subscriptions_str,
                }
                all_users_data.append(user_data)
                if user.subscriptions:
                    subscribed_users_data.append(user_data)
                    sub = user.subscriptions[0]
                    if sub.start_datetime.tzinfo is None:
                        sub.start_datetime = sub.start_datetime.replace(
                            tzinfo=MOSCOW_TZ
                        )
                    if sub.end_datetime.tzinfo is None:
                        sub.end_datetime = sub.end_datetime.replace(tzinfo=MOSCOW_TZ)
                    if sub.start_datetime < now < sub.end_datetime:
                        active_subscriptions_data.append(user_data)
                        if not sub.user.telegram_id:
                            unjoined_users_data.append(user_data)
                            unjoined_in_chat_data.append(user_data)
                            continue
                        if not check_user_in_channel(
                            context, sub.user.telegram_id, CHANNEL_ID
                        ):
                            unjoined_users_data.append(user_data)
                        if not check_user_in_channel(
                            context, sub.user.telegram_id, CHAT_ID
                        ):
                            unjoined_in_chat_data.append(user_data)
            except Exception as error:
                logger.error(
                    f"Ошибка при get_all_users: {str(error)}\n"
                    f"Телефонный номер: {user.phone_number}"
                )
    Session.remove()
    # Создание DataFrame'ов из данных
    all_users_df = pd.DataFrame(all_users_data)
    subscribed_users_df = pd.DataFrame(subscribed_users_data)
    active_subscriptions_df = pd.DataFrame(active_subscriptions_data)
    unjoined_users_df = pd.DataFrame(unjoined_users_data)
    unjoined_in_chat_df = pd.DataFrame(unjoined_in_chat_data)
    # Создание Excel-файла в памяти
    with BytesIO() as output:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for df, sheet_name in [
                (all_users_df, "Все пользователи"),
                (subscribed_users_df, "Пользователи с подписками"),
                (active_subscriptions_df, "Активные подписки"),
                (unjoined_users_df, "Не вступили в канал"),
                (unjoined_in_chat_df, "Не вступили в чат"),
            ]:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                worksheet = writer.sheets[sheet_name]
                for col in worksheet.columns:
                    max_length = 0
                    column = col[0].column_letter
                    for cell in col:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(cell.value)
                        except Exception:
                            pass
                    adjusted_width = max_length + 2
                    worksheet.column_dimensions[column].width = adjusted_width
        output.seek(0)  # Перемещаемся к началу потока
        # Отправляем файл пользователю
        context.bot.send_document(
            chat_id=update.effective_chat.id, document=output, filename="users.xlsx"
        )
    return None


# Отправить ссылку-приглашение персонально одному пользователю
def send_invite_link_personally(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /send_invite_link_personally номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            user = session.query(User).filter(User.phone_number == phone_number).first()
            # Проверяем наличие пользователя
            if not user:
                update.message.reply_text(
                    "Пользователя с таким номером телефона не существует."
                )
                return None
            nearest_subscription = (
                session.query(Subscription)
                .filter(Subscription.user_id == user.id)
                .order_by(asc(Subscription.start_datetime))
                .first()
            )
            # Если нет подписки
            if not nearest_subscription:
                update.message.reply_text(
                    f"У пользователя {phone_number} нет подписки."
                )
                return None
            # Проверяем наличие ссылки-приглашения
            if nearest_subscription.subscription_link:
                if user.telegram_id:
                    context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=TEXT_INVITATION.format(
                            invite_link=nearest_subscription.subscription_link,
                            chat_link=nearest_subscription.chat_link,
                        ),
                    )
                    # Отвечаем, что всё прошло успешно
                    update.message.reply_text(
                        f"Пользователю с номером {phone_number} успешно отправлена ссылка-приглашение."
                    )
                    return None
                update.message.reply_text(
                    "Ссылка-приглашение создана и привязана, но не отправлена, "
                    "так как у пользователя отсутствует привязанный телеграм id."
                )
                return None
            if nearest_subscription.start_datetime.astimezone(
                MOSCOW_TZ
            ) <= datetime.datetime.now(MOSCOW_TZ):
                # Создаём ссылку, если отсутствует
                invite_link = create_invite_link(
                    context.bot,
                    nearest_subscription.end_datetime.astimezone(MOSCOW_TZ),
                    CHANNEL_ID,
                )
                time.sleep(1)
                chat_link = create_invite_link(
                    context.bot,
                    nearest_subscription.end_datetime.astimezone(MOSCOW_TZ),
                    CHAT_ID,
                )
                time.sleep(1)
                # Присваиваем инвайт конкретному пользователю
                if invite_link and chat_link:
                    nearest_subscription.subscription_link = invite_link
                    nearest_subscription.chat_link = chat_link
                    session.commit()
                    # Отправляем текст с инвайтом
                    if user.telegram_id:
                        context.bot.send_message(
                            chat_id=user.telegram_id,
                            text=TEXT_INVITATION.format(
                                invite_link=invite_link, chat_link=chat_link
                            ),
                        )
                        # Отвечаем, что всё прошло успешно
                        update.message.reply_text(
                            f"Пользователю с номером {phone_number} успешно отправлена ссылка-приглашение."
                        )
                        return None
                    update.message.reply_text(
                        "Ссылка-приглашение создана и привязана, но не отправлена, "
                        "так как у пользователя отсутствует привязанный телеграм id."
                    )
                    return None
                logger.error(
                    f"Не удалось создать сhat_link или invite_link для телеграм id: {user.telegram_id}\n"
                    "Соответственно сообщение-приглашение не отправлено при задаче send_invite_link"
                )
                update.message.reply_text("Не удалось создать ссылку-приглашение.")
                return None
            update.message.reply_text(
                "Ссылка-приглашение не может быть создана, так как период подписки ещё не начался."
            )
        except Exception as error:
            logger.error(f"Ошибка при send_invite_link_personally: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Функция для удаления пользователя
def delete_user(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /delete_user номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            # Проверяем наличие пользователя
            user = session.query(User).filter(User.phone_number == phone_number).first()
            if not user:
                update.message.reply_text(
                    "Пользователя с таким телефонным номером не существует."
                )
                return None
            session.delete(user)
            session.commit()
            # Сообщаем, что всё прошло успешно
            update.message.reply_text(
                f"Пользователь с номером {phone_number} успешно удален."
            )
        except Exception as error:
            logger.error(f"Ошибка при delete_user: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Отправляем уведомление о новом чате персонально
def notify_about_new_chat_personally(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        update.message.reply_text(
            "Пожалуйста, введите команду в формате: /notify_about_new_chat_personally телеграм_id\n"
            "Одним сообщением, в одну строку."
        )
        return None
    telegram_id = args[0]
    if not telegram_id.isdigit():
        update.message.reply_text("Телеграм id должен быть числом")
        return None
    telegram_id = int(telegram_id)
    bot = context.bot
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
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if not user:
                update.message.reply_text(
                    "Пользователя с таким телеграм id не существует."
                )
                return None
            subscription = user.subscriptions[0]
            if not subscription:
                update.message.reply_text("У пользователя отсутствует подписка.")
                return None
            if not subscription.chat_link:
                chat_link = create_invite_link(bot, subscription.end_datetime, CHAT_ID)
                if not chat_link:
                    update.message.reply_text(
                        f"Не удалось создать ссылку для {user.telegram_id}"
                    )
                    return None
                subscription.chat_link = chat_link
            try:
                bot.send_message(
                    chat_id=user.telegram_id,
                    text=notification_about_chat.format(
                        chat_link=subscription.chat_link
                    ),
                )
            except Exception as error:
                logger.error(
                    "Ошибка при отправки сообщения в notify_about_new_chat_personally "
                    f"для пользователя с телеграм id: {user.telegram_id}\n"
                    f"Ошибка: {str(error)}"
                )
            else:
                update.message.reply_text(
                    f"Пользователю с телеграм id {user.telegram_id} успешно отправлено уведомление"
                )
            session.commit()
        except Exception as error:
            logger.error(f"Ошибка при notify_about_new_chat: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None
