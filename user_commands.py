import datetime
from typing import Optional

from sqlalchemy import asc
from telegram import Update
from telegram.ext import CallbackContext

from constants import (
    CHANNEL_ID,
    CHAT_ID,
    LINK_IS_EMPTY,
    TEXT_INVITATION,
    THESE_ARE_YOUR_LINKS,
    logger,
)
from database import Session, Subscription, User, create_session
from utils import create_invite_link


# Обработчик сообщения 'Получить ссылку 🏁'
async def get_subscription_link(
    update: Update, context: CallbackContext, phone_number: Optional[str] = None
) -> None:
    not_found_text = (
        "К сожалению, я не вижу данный номер в списке участников 🙁\n\n"
        "Убедись, что ты подождал 10 минут после оплаты, прежде чем запустить "
        "бота. Если нет - запусти его повторно чуть позже.\n\n"
        "В случае, если бот все же не увидел твой номер в списке участников - "
        "напиши в поддержку👇🏼\n\n"
        "В обращении укажи свой адрес электронной почты и номер телефона.\n"
        "Телеграм: @sensei_vasilisa\n"
        "Почта: Vasilisa.sensei@yandex.ru"
    )
    subscription_is_activated = (
        "Поздравляю! Твоя подписка активирована)\n"
        "Первого числа оплаченного месяца "
        "я отправлю тебе ссылку-приглашение для вступления в сленг-клуб 😉"
    )
    telegram_id_already_has_phone = (
        "К твоему телеграм id уже привязан другой номер, обратись в поддержку."
    )
    check_payment_text = "Проверяем наличие оплат..."
    with create_session() as session:
        try:
            # Если передан номер телефона
            if phone_number:
                await update.message.reply_text(check_payment_text)
                user = (
                    session.query(User)
                    .filter(User.phone_number == phone_number)
                    .first()
                )
                if not user:
                    await update.message.reply_text(not_found_text)
                    return None
                # Проверяем, что телеграм id пользователя ещё не привязан
                # к какому-либо телефонному номеру
                if not user.telegram_id:
                    user_from_id = (
                        session.query(User)
                        .filter(User.telegram_id == update.message.chat_id)
                        .first()
                    )
                    if user_from_id:
                        await update.message.reply_text(telegram_id_already_has_phone)
                        return None
                # Обновляем телеграм id и телеграм ссылку пользователя
                user.telegram_id = update.message.chat_id
                if update.message.from_user.username:
                    user.user_link = f"https://t.me/{update.message.from_user.username}"
                session.commit()
                # Смотрим, оплачена ли подписка
                nearest_subscription = (
                    session.query(Subscription)
                    .filter(Subscription.user_id == user.id)
                    .order_by(asc(Subscription.start_datetime))
                    .first()
                )
                if not nearest_subscription:
                    await update.message.reply_text(not_found_text)
                    return None
                # Смотрим, началась ли подписка
                if nearest_subscription.subscription_link:
                    chat_link = (
                        nearest_subscription.chat_link
                        if nearest_subscription.chat_link
                        else LINK_IS_EMPTY
                    )
                    await update.message.reply_text(
                        THESE_ARE_YOUR_LINKS.format(
                            invite_link=nearest_subscription.subscription_link,
                            chat_link=chat_link,
                        )
                    )
                    return None
                now = datetime.datetime.now()
                if (
                    now.day == 1
                    and 12 <= now.hour < 18
                    and nearest_subscription.start_datetime <= now
                ):
                    invite_link = await create_invite_link(
                        context.bot, nearest_subscription.end_datetime, CHANNEL_ID
                    )
                    chat_link = await create_invite_link(
                        context.bot, nearest_subscription.end_datetime, CHAT_ID
                    )
                    # Присваиваем инвайт конкретному пользователю
                    if invite_link:
                        nearest_subscription.subscription_link = invite_link
                        session.commit()
                        # Отправляем текст с инвайтом
                        await context.bot.send_message(
                            chat_id=user.telegram_id,
                            text=TEXT_INVITATION.format(
                                invite_link=invite_link, chat_link=chat_link
                            ),
                        )
                        return None
                # Подписка активирована
                await update.message.reply_text(subscription_is_activated)
                return None
            # Если номер телефона не передан
            telegram_id = update.message.from_user.id
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if not user:
                await update.message.reply_text(
                    "Напишите номер телефона, который вы ввели при оплате👇🏼, "
                    "либо нажмите 'Отправить номер телефона📞' для автоматической отправки."
                )
                return None
            await update.message.reply_text(check_payment_text)
            # Смотрим, оплачена ли подписка
            nearest_subscription = (
                session.query(Subscription)
                .filter(Subscription.user_id == user.id)
                .order_by(asc(Subscription.start_datetime))
                .first()
            )
            if not nearest_subscription:
                await update.message.reply_text(not_found_text)
                return None
            if nearest_subscription.subscription_link:
                chat_link = (
                    nearest_subscription.chat_link
                    if nearest_subscription.chat_link
                    else LINK_IS_EMPTY
                )
                await update.message.reply_text(
                    THESE_ARE_YOUR_LINKS.format(
                        invite_link=nearest_subscription.subscription_link,
                        chat_link=chat_link,
                    )
                )
                return None
            now = datetime.datetime.now()
            if (
                now.day == 1
                and 12 <= now.hour < 18
                and nearest_subscription.start_datetime <= now
            ):
                invite_link = await create_invite_link(
                    context.bot, nearest_subscription.end_datetime, CHANNEL_ID
                )
                chat_link = await create_invite_link(
                    context.bot, nearest_subscription.end_datetime, CHAT_ID
                )
                # Присваиваем инвайт конкретному пользователю
                if invite_link:
                    nearest_subscription.subscription_link = invite_link
                    session.commit()
                    # Отправляем текст с инвайтом
                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=TEXT_INVITATION.format(
                            invite_link=invite_link, chat_link=chat_link
                        ),
                    )
                    return None
            # Подписка активирована
            await update.message.reply_text(subscription_is_activated)
            session.commit()
        except Exception as error:
            session.rollback()
            logger.error(f"Ошибка при отправки ссылки: {str(error)}")
            raise
        finally:
            Session.remove()  # Удаляем сессию из контекста
        return None


# Обработчик сообщения 'Срок действия подписки 🕑'
async def get_subscription_period(update: Update, context: CallbackContext) -> None:
    telegram_id = update.message.from_user.id
    with create_session() as session:
        user_id = session.query(User.id).filter(User.telegram_id == telegram_id).first()
        if not user_id:
            await update.message.reply_text("У тебя нет действующей подписки.")
            return None
        # Получаем самую ближайшую подписку
        nearest_subscription = (
            session.query(Subscription)
            .filter(Subscription.user_id == user_id[0])
            .order_by(asc(Subscription.start_datetime))
            .first()
        )
        if not nearest_subscription:
            await update.message.reply_text("У тебя нет действующей подписки.")
            return None
        await update.message.reply_text(
            "Срок действия подписки Sensei, for real!?: "
            f"{nearest_subscription.start_datetime.strftime('%d.%m.%Y')}-"
            f"{nearest_subscription.end_datetime.strftime('%d.%m.%Y')}"
        )
        session.commit()
    return None


# Обработчик сообщения 'Показать привязанный номер 📲'
async def show_linked_phone_number(update: Update, context: CallbackContext) -> None:
    telegram_id = update.message.from_user.id
    with create_session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            await update.message.reply_text("У тебя нет привязанного номера.")
            return None
        await update.message.reply_text(
            f"К твоему аккаунту привязан номер: {user.phone_number}"
        )
    Session.remove()  # Удаляем сессию из контекста
    return None


# Обработчик сообщения 'Техническая поддержка ⚙️'
async def get_technical_support(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Пожалуйста, обращайся в техническую поддержку:\n"
        "Телеграм: @rogozin_ilya\n"
        "Почта: rogozin.il2399@gmail.com"
    )
    return None


# Обработчик сообщения 'Оставить отзыв ✍🏼'
async def write_review(update: Update, context: CallbackContext) -> None:
    telegram_id = update.message.from_user.id
    with create_session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            await update.message.reply_text(
                "К сожалению, ты не можешь оставить отзыв, так как не являешься членом сленг клуба.\n\n"
                "Мы будем рады, если ты присоединишься к нашему комьюнити и будешь развивать с нами свой английский!"
            )
            return None
        await update.message.reply_text(
            "Мы стараемся улучшать сленг-клуб каждый день! И будем рады получить твою обратную связь:)\n\n"
            "Пожалуйста, отправляй отзыв одним сообщением! Заранее Благодарим!\n\n"
            "Для отмены отправь '-'."
        )
        context.user_data["awaiting_review"] = True
    return None


# Функция для команды /get_invitation
def get_invitation(update: Update, context: CallbackContext) -> None:
    get_subscription_link(update, context)
    return None


# Обработчик сообщения 'Демо-версия сленг-клуба 🖼️'
async def get_demo_version_of_club(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Ма френд, привет!: Ссылка для вступления в демо-версию "
        "сленг-клуба «Sensei, for real!?»: https://t.me/+vynLcyHSc9Y4N2Ji"
    )
    return None
