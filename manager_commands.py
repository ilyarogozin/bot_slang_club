import datetime
from io import BytesIO
from openpyxl.utils import get_column_letter

import pandas as pd
from sqlalchemy import asc
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CallbackContext,
    ConversationHandler,
)

from constants import (
    CHANNEL_ID,
    CHAT_ID,
    MODERATOR_IDS,
    MOSCOW_TZ,
    PHONE_NUMBER_REGEX,
    TEXT_INVITATION,
    WAITING_NUMBERS,
    logger,
)
from database import (
    Review,
    Session,
    Subscription,
    User,
    ButtonStat,
    get_user_by_phone,
    set_messages_acceptable_by_phone,
    set_messages_acceptable,
    log_button_click,
    get_phone_by_telegram_id,
    create_session,
)
from utils import (
    check_user_in_channel,
    create_invite_link,
    update_subscription,
    restricted,
)


# Установить конец подписки вручную через команду /set_subscription_end_at
async def set_subscription_end_at(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /set_subscription_end_at год:месяц:день:часы:минуты номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    manual_datetime, phone_number = args
    if not PHONE_NUMBER_REGEX.match(phone_number):
        await update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    try:
        manual_datetime = list(map(int, manual_datetime.split(":")))
        year, month, day, hour, minute = manual_datetime
    except Exception as error:
        logger.error(f"Ошибка ввода конца подписки: {error}")
        await update.message.reply_text(
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
                await update.message.reply_text(
                    "Пользователя с таким номером телефона не существует."
                )
                return None
            if not nearest_subscription:
                new_subscription = Subscription(
                    start_datetime=now, end_datetime=end_datetime, user_id=user_id[0]
                )
                session.add(new_subscription)
                session.commit()
                await update.message.reply_text(
                    f"Конец подписки успешно изменён на {end_datetime.strftime('%d-%m-%Y %H:%M')} "
                    f"у пользователя с номером телефона: {phone_number}."
                )
                return None
            # Обновляем конец подписки
            nearest_subscription.end_datetime = end_datetime
            # Фиксируем изменения
            session.commit()
            await update.message.reply_text(
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
async def give_free_subscription(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Получаем номер телефона пользователя и количество месяцев из команды
    args = context.args
    if len(args) != 4:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /give_free_subscription "
            "номер_телефона количество_месяцев номер_стартового_месяца год_стартового_месяца\n"
            "Пример: /give_free_subscription +79998887776 1 9 2024\n"
            "Одним сообщением, в одну строку."
        )
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    phone_number, months, start_month, start_year = args
    if not PHONE_NUMBER_REGEX.match(phone_number):
        await update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    try:
        months = int(months)
        start_month = int(start_month)
        start_year = int(start_year)
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите количество месяцев, месяц начала и год числом."
        )
        return None
    if months < 0:
        await update.message.reply_text(
            "Пожалуйста, введите положительное количество месяцев."
        )
        return None
    # Даём пользователю бесплатную подписку
    try:
        await update_subscription(months, phone_number, start_month, start_year, "-")
    except Exception as e:
        logger.error(f"Ошибка в give_free_subscription: {e}")
        await update.message.reply_text(f"Ошибка: {e}")
        return None
    # Отвечаем, что всё прошло успешно
    await update.message.reply_text(
        f"Пользователю с номером {phone_number} была предоставлена подписка на {months} месяцев, "
        f"старт подписки {start_month} месяца {start_year} года."
    )
    return None


# Функция для удаления ближайшей подписки пользователя
async def delete_subscription(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /delete_subscription номер_телефона\n"
            "Будет удалена самая ближайшая подписка.\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        await update.message.reply_text(
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
                await update.message.reply_text(
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
                await update.message.reply_text(
                    f"У пользователя {phone_number} нет подписки."
                )
                return None
            # Отменяем ссылку-приглашение в канал и чат-болталку, если есть
            if nearest_subscription.subscription_link:
                try:
                    await context.bot.revoke_chat_invite_link(
                        CHANNEL_ID, nearest_subscription.subscription_link
                    )
                    await context.bot.revoke_chat_invite_link(
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
            await update.message.reply_text(
                f"Ближайшая подписка пользователя {phone_number} успешно удалена."
            )
        except Exception as error:
            logger.error(f"Ошибка при delete_subscription: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Функция для изменения номера телефона пользователя
async def change_phone_number(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /change_phone_number старый_номер__пользователя новый_номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    old_phone_number, new_phone_number = args
    if not PHONE_NUMBER_REGEX.match(old_phone_number) or not PHONE_NUMBER_REGEX.match(
        new_phone_number
    ):
        await update.message.reply_text(
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
                await update.message.reply_text(
                    "Номер телефона, на который вы хотите поменять, уже принадлежит другому пользователю."
                )
                return None
            user = (
                session.query(User)
                .filter(User.phone_number == old_phone_number)
                .first()
            )
            if not user:
                await update.message.reply_text(
                    "Пользователя с таким нмоером телефона не существует."
                )
                return None
            # Обновляем номер телефона пользователя
            user.phone_number = new_phone_number
            # Обновляем запись базы данных
            session.commit()
            # Сообщаем, что всё прошло успешно
            await update.message.reply_text(
                f"Номер телефона успешно изменён на {new_phone_number} у пользователя с прошлым номером: {old_phone_number}."
            )
        except Exception as error:
            logger.error(f"Ошибка при change_phone_number: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Получаем все отзывы пользователей в файле excel
async def get_all_reviews(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    with create_session() as session:
        # Запрашиваем данные из базы
        query = session.query(
            Review.review_text, User.phone_number, User.user_link
        ).join(User)
    if query.count() == 0:
        await update.message.reply_text("Новых отзывов не найдено.")
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
        await context.bot.send_document(
            chat_id=update.effective_chat.id, document=output, filename="reviews.xlsx"
        )
    return None


# Получаем всех пользователей в файле excel
async def get_all_users(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
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
                        if not await check_user_in_channel(
                            context, sub.user.telegram_id, CHANNEL_ID
                        ):
                            unjoined_users_data.append(user_data)
                        if not await check_user_in_channel(
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
        await context.bot.send_document(
            chat_id=update.effective_chat.id, document=output, filename="users.xlsx"
        )
    return None


# Отправить ссылку-приглашение персонально одному пользователю
async def send_invite_link_personally(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /send_invite_link_personally номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        await update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            user = session.query(User).filter(User.phone_number == phone_number).first()
            # Проверяем наличие пользователя
            if not user:
                await update.message.reply_text(
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
                await update.message.reply_text(
                    f"У пользователя {phone_number} нет подписки."
                )
                return None
            # Проверяем наличие ссылки-приглашения
            if nearest_subscription.subscription_link:
                if user.telegram_id:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=TEXT_INVITATION.format(
                            invite_link=nearest_subscription.subscription_link,
                            chat_link=nearest_subscription.chat_link,
                        ),
                    )
                    # Отвечаем, что всё прошло успешно
                    await update.message.reply_text(
                        f"Пользователю с номером {phone_number} успешно отправлена ссылка-приглашение."
                    )
                    return None
                await update.message.reply_text(
                    "Ссылка-приглашение создана и привязана, но не отправлена, "
                    "так как у пользователя отсутствует привязанный телеграм id."
                )
                return None
            if nearest_subscription.start_datetime.astimezone(
                MOSCOW_TZ
            ) <= datetime.datetime.now(MOSCOW_TZ):
                # Создаём ссылку, если отсутствует
                invite_link = await create_invite_link(
                    context.bot,
                    nearest_subscription.end_datetime.astimezone(MOSCOW_TZ),
                    CHANNEL_ID,
                )
                chat_link = await create_invite_link(
                    context.bot,
                    nearest_subscription.end_datetime.astimezone(MOSCOW_TZ),
                    CHAT_ID,
                )
                # Присваиваем инвайт конкретному пользователю
                if invite_link and chat_link:
                    nearest_subscription.subscription_link = invite_link
                    nearest_subscription.chat_link = chat_link
                    session.commit()
                    # Отправляем текст с инвайтом
                    if user.telegram_id:
                        await context.bot.send_message(
                            chat_id=user.telegram_id,
                            text=TEXT_INVITATION.format(
                                invite_link=invite_link, chat_link=chat_link
                            ),
                        )
                        # Отвечаем, что всё прошло успешно
                        await update.message.reply_text(
                            f"Пользователю с номером {phone_number} успешно отправлена ссылка-приглашение."
                        )
                        return None
                    await update.message.reply_text(
                        "Ссылка-приглашение создана и привязана, но не отправлена, "
                        "так как у пользователя отсутствует привязанный телеграм id."
                    )
                    return None
                logger.error(
                    f"Не удалось создать сhat_link или invite_link для телеграм id: {user.telegram_id}\n"
                    "Соответственно сообщение-приглашение не отправлено при задаче send_invite_link"
                )
                await update.message.reply_text("Не удалось создать ссылку-приглашение.")
                return None
            await update.message.reply_text(
                "Ссылка-приглашение не может быть создана, так как период подписки ещё не начался."
            )
        except Exception as error:
            logger.error(f"Ошибка при send_invite_link_personally: {str(error)}")
            session.rollback()
        finally:
            Session.remove()
    return None


# Функция для удаления пользователя
async def delete_user(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Запрос обрабатывается...")
    # Проверяем, является ли пользователь команды модератором
    if update.message.from_user.id not in MODERATOR_IDS:
        await update.message.reply_text("Вы не являетесь модератором.")
        return None
    # Обрабатываем возможные ошибки при введении аргументов
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            "Пожалуйста, введите команду в формате: /delete_user номер_телефона\n"
            "Одним сообщением, в одну строку."
        )
        return None
    phone_number = args[0]
    if not PHONE_NUMBER_REGEX.match(phone_number):
        await update.message.reply_text(
            "Пожалуйста, введите номер телефона вида: +71112223331"
        )
        return None
    with create_session() as session:
        try:
            # Проверяем наличие пользователя
            user = session.query(User).filter(User.phone_number == phone_number).first()
            if not user:
                await update.message.reply_text(
                    "Пользователя с таким телефонным номером не существует."
                )
                return None
            session.delete(user)
            session.commit()
            # Сообщаем, что всё прошло успешно
            await update.message.reply_text(
                f"Пользователь с номером {phone_number} успешно удален."
            )
        except Exception as error:
            logger.error(f"Ошибка при delete_user: {str(error)}")
            session.rollback()
    return None


async def send_bulk_messages(update: Update, context: CallbackContext):
    if not await restricted(update):
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите список номеров (через пробел, запятую или с новой строки):"
    )
    return WAITING_NUMBERS


async def process_numbers(update: Update, context: CallbackContext):
    if not await restricted(update):
        return ConversationHandler.END

    raw_text = update.message.text.strip()
    numbers = [n.strip() for n in raw_text.replace(",", " ").split() if n.strip()]

    not_found, failed, sent, blocked = [], [], [], []

    for phone in numbers:
        user = get_user_by_phone(phone)

        if not user:
            not_found.append(phone)
            continue

        telegram_id, acceptable = user

        if not acceptable:
            blocked.append(phone)
            continue

        try:
            text = (
                "Привет! \n"
                "Это Василиса из клуба Sensei, for real?!\n"
                "Вы были с нами раньше — спасибо ❤️\n\n"
                "С тех пор многое поменялось:\n\n"
                "🤖 AI-Sensei — появился ваш личный ИИ-преподаватель\n"
                "🗣 Подкасты от native speakers\n"
                "👩🏼‍💻 Ежедневные посты и ежемесячный Zoom\n"
                "❤️‍🔥 Цена теперь 490 ₽\n\n"
                "В знак благодарности — подарок: PDF «Словарик современного сленга 2025»."
            )
            keyboard = [
                [InlineKeyboardButton("🎁 Получить бонус", callback_data="bonus")],
                [InlineKeyboardButton("👀 Что нового?", callback_data="news")],
                [InlineKeyboardButton("🚫 Не беспокоить", callback_data="stop")]
            ]
            await context.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            sent.append(phone)

        except Exception as e:
            logger.error(f"Ошибка при отправке {phone}: {e}")
            failed.append(phone)

    report = []
    if sent:
        report.append("✅ Успешно отправлено: " + ", ".join(sent))
    if not_found:
        report.append("❌ Нет в базе: " + ", ".join(not_found))
    if blocked:
        report.append("🚫 Не принимают сообщения: " + ", ".join(blocked))
    if failed:
        report.append("⚠️ Ошибка при отправке: " + ", ".join(failed))

    await update.message.reply_text("\n".join(report) if report else "Номера не обработаны.")
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext):
    if not await restricted(update):
        return ConversationHandler.END

    await update.message.reply_text("Рассылка отменена.")
    return ConversationHandler.END


# Экспорт статистики кнопок в Excel через pandas
def export_button_stats_to_excel(filename="button_stats.xlsx"):
    with create_session() as session:
        try:
            week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

            # ORM-запрос
            query = (
                session.query(ButtonStat.phone_number, ButtonStat.button, ButtonStat.timestamp)
                .filter(ButtonStat.timestamp >= week_ago)
                .order_by(ButtonStat.timestamp.desc())
            )

            # Загружаем результат в pandas
            df = pd.read_sql(query.statement, session.bind)

            # Пишем сразу в Excel
            with pd.ExcelWriter(filename, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Button Stats", index=False)

                worksheet = writer.sheets["Button Stats"]
                for i, column in enumerate(df.columns, 1):
                    max_len = max(df[column].astype(str).map(len).max(), len(column)) + 2
                    col_letter = get_column_letter(i)
                    worksheet.column_dimensions[col_letter].width = max_len

            return filename

        finally:
            session.close()


async def get_button_stats(update: Update, context: CallbackContext):
    await update.message.reply_text("Запрос обрабатывается...")
    if not await restricted(update):
        return

    filename = "button_stats.xlsx"
    export_button_stats_to_excel(filename)

    await update.message.reply_document(
        document=open(filename, "rb"),
        filename=filename,
        caption="📊 Статистика по нажатиям кнопок"
    )


async def set_messages(update: Update, context: CallbackContext):
    if not await restricted(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text("Использование: /set_messages <phone> <0|1>")
        return

    phone, flag = context.args
    if flag not in ("0", "1"):
        await update.message.reply_text("Второй аргумент должен быть 0 или 1")
        return

    ok = set_messages_acceptable_by_phone(phone, bool(int(flag)))
    if ok:
        await update.message.reply_text(
            f"✅ Для номера {phone} сообщения {'разрешены' if flag == '1' else 'запрещены'}."
        )
    else:
        await update.message.reply_text(f"❌ Номер {phone} не найден в базе.")


async def main_menu(update_or_query, context: CallbackContext):
    """Главный экран"""
    text = (
        "Привет! \n"
        "Это Василиса из клуба Sensei, for real?!\n"
        "Вы были с нами раньше — спасибо ❤️\n\n"
        "С тех пор многое поменялось:\n\n"
        "🤖 AI-Sensei — появился ваш личный ИИ-преподаватель\n"
        "🗣 Подкасты от native speakers\n"
        "👩🏼‍💻 Ежедневные посты и ежемесячный Zoom\n\n"
        "❤️‍🔥 Цена теперь 490 ₽\n\n"
        "В знак благодарности — подарок: PDF «Словарик современного сленга 2025»."
    )
    keyboard = [
        [InlineKeyboardButton("🎁 Получить бонус", callback_data="bonus")],
        [InlineKeyboardButton("👀 Что нового?", callback_data="news")],
        [InlineKeyboardButton("🚫 Не беспокоить", callback_data="stop")]
    ]

    if isinstance(update_or_query, Update):
        if update_or_query.message:
            await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:  # callback_query
        await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_phone = get_phone_by_telegram_id(user_id)

    # фиксируем нажатие
    log_button_click(user_phone, query.data)

    if query.data == "news":
        text = (
            "🤖 AI-Sensei: персональный ИИ-тичер → объясняет, тренирует, исправляет мягко\n\n"
            "🗣 Подкасты от носителей: для прокачки аутентичного произношения и аудирования\n\n"
            "👩🏼‍💻 Zoom 1×/мес: мини-уроки\n\n"
            "📱 Посты ежедневно: самый актуальный контент"
        )
        keyboard = [
            [InlineKeyboardButton("⏭️ Вернуться в клуб за 490₽", url="https://vasilisa-slang.ru/#subscribe", callback_data="club")],
            [InlineKeyboardButton("🏠 На главный экран", callback_data="main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "bonus":
        await query.message.reply_document(
            document=open("🥤Словарик_современного_сленга_Классическая_версия.pdf", "rb"),
            filename="🥤Словарик_современного_сленга_Классическая_версия.pdf",
            caption="💅🏻 Держите ваш подарок!"
        )
        text = (
            "Готовы примерить это в речи?\n"
            "Наш клубный 🤖 AI-Sensei проведет с вами полноценный урок."
        )
        keyboard = [
            [InlineKeyboardButton("⏭️ Вернуться в клуб за 490₽", url="https://vasilisa-slang.ru/#subscribe", callback_data="club")],
            [InlineKeyboardButton("🤖 Занятие с Сенсеем", url="https://t.me/sensei_for_real/545", callback_data="lesson")],
            [InlineKeyboardButton("🏠 На главный экран", callback_data="main")]
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "stop":
        user_id = query.from_user.id
        set_messages_acceptable(user_id, False)
        text = (
            "Понял. Больше писать не будем. Спасибо, что были с нами. "
            "Если захотите вернуться — мы рядом ❤️‍🔥"
        )
        await query.edit_message_text(text)

    elif query.data == "main":
        await main_menu(query, context)
